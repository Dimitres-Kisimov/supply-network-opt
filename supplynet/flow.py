"""Min-cost multi-echelon network flow: plant -> DC -> customer.

Given a set of open DCs, route production from plants through DCs to customers
at minimum total cost (production + inbound + outbound), respecting plant and DC
throughput. We solve it two independent ways and cross-check them:

  * a graph solver (OR-Tools SimpleMinCostFlow), and
  * a transportation LP (scipy.optimize.linprog, HiGHS).

Because a min-cost-flow polytope is integral, the LP optimum and the integer
graph optimum should agree to within rounding tolerance.
"""

from dataclasses import dataclass, field

import numpy as np
from ortools.graph.python import min_cost_flow
from scipy.optimize import linprog

from supplynet.data import NetworkData

# Costs are scaled to integers for the graph solver, then converted back.
COST_SCALE = 10_000


@dataclass
class Arc:
    tail: int
    head: int
    capacity: int
    unit_cost: float  # real (unscaled) per-unit cost
    kind: str  # "supply" | "inbound" | "throughput" | "outbound"
    meta: tuple = ()


@dataclass
class FlowSolution:
    method: str
    total_cost: float
    arc_flows: list[tuple[str, float]] = field(repr=False, default_factory=list)


def _build_arcs(data: NetworkData, opened_idx: list[int]) -> tuple[list[Arc], list[int], int]:
    """Construct the layered flow network for the given open DCs.

    Node layout: 0 = super source; then plants; then DC-in; DC-out; customers.
    Supplies: source = +total_demand, each customer = -demand_j (integers).
    """
    plants = data.plants
    dcs = data.dcs
    cust = data.customers
    demand = np.round(cust["demand_mean"].to_numpy()).astype(int)
    total_demand = int(demand.sum())

    n_p = len(plants)
    n_d = len(opened_idx)
    n_c = len(cust)

    source = 0
    plant_node = {k: 1 + k for k in range(n_p)}
    dc_in = {i: 1 + n_p + pos for pos, i in enumerate(opened_idx)}
    dc_out = {i: 1 + n_p + n_d + pos for pos, i in enumerate(opened_idx)}
    cust_node = {j: 1 + n_p + 2 * n_d + j for j in range(n_c)}
    n_nodes = 1 + n_p + 2 * n_d + n_c

    supplies = [0] * n_nodes
    supplies[source] = total_demand
    for j in range(n_c):
        supplies[cust_node[j]] = -int(demand[j])

    arcs: list[Arc] = []
    # Source -> plant: capped by plant capacity, priced at production cost.
    for k in range(n_p):
        cap = int(plants.iloc[k]["capacity"])
        arcs.append(Arc(source, plant_node[k], cap, float(plants.iloc[k]["prod_cost"]),
                        "supply", (plants.iloc[k]["plant_id"],)))
    # Plant -> DC-in: inbound transport, effectively uncapped.
    for k in range(n_p):
        for i in opened_idx:
            arcs.append(Arc(plant_node[k], dc_in[i], total_demand,
                            float(data.plant_dc_cost[k, i]), "inbound",
                            (plants.iloc[k]["plant_id"], dcs.iloc[i]["dc_id"])))
    # DC-in -> DC-out: throughput capacity of the DC.
    for i in opened_idx:
        cap = int(dcs.iloc[i]["capacity"])
        arcs.append(Arc(dc_in[i], dc_out[i], cap, 0.0, "throughput", (dcs.iloc[i]["dc_id"],)))
    # DC-out -> customer: outbound transport.
    for i in opened_idx:
        for j in range(n_c):
            arcs.append(Arc(dc_out[i], cust_node[j], total_demand,
                            float(data.dc_cust_cost[i, j]), "outbound",
                            (dcs.iloc[i]["dc_id"], cust.iloc[j]["cust_id"])))

    return arcs, supplies, n_nodes


def _arc_label(arc: Arc) -> str:
    return f"{arc.kind}:" + "->".join(str(m) for m in arc.meta)


def solve_flow_graph(data: NetworkData, opened_idx: list[int]) -> FlowSolution:
    """Solve the multi-echelon flow with OR-Tools SimpleMinCostFlow."""
    arcs, supplies, _ = _build_arcs(data, opened_idx)
    smcf = min_cost_flow.SimpleMinCostFlow()
    arc_ids = []
    for arc in arcs:
        aid = smcf.add_arc_with_capacity_and_unit_cost(
            arc.tail, arc.head, arc.capacity, round(arc.unit_cost * COST_SCALE)
        )
        arc_ids.append(aid)
    for node, sup in enumerate(supplies):
        smcf.set_node_supply(node, sup)

    status = smcf.solve()
    if status != smcf.OPTIMAL:
        raise RuntimeError(f"min_cost_flow did not reach optimal (status={status})")

    # Recompute total cost from flows using REAL (unscaled) costs.
    total = 0.0
    arc_flows = []
    for arc, aid in zip(arcs, arc_ids, strict=True):
        f = smcf.flow(aid)
        if f > 0:
            total += f * arc.unit_cost
            arc_flows.append((_arc_label(arc), float(f)))
    return FlowSolution("graph_min_cost_flow", float(total), arc_flows)


def solve_flow_lp(data: NetworkData, opened_idx: list[int]) -> FlowSolution:
    """Solve the same network as a transportation LP with scipy linprog (HiGHS)."""
    arcs, supplies, n_nodes = _build_arcs(data, opened_idx)
    n_arcs = len(arcs)

    c = np.array([arc.unit_cost for arc in arcs])
    bounds = [(0, arc.capacity) for arc in arcs]

    # Node-balance equality: outflow - inflow = supply, for every node.
    a_eq = np.zeros((n_nodes, n_arcs))
    for a, arc in enumerate(arcs):
        a_eq[arc.tail, a] += 1.0
        a_eq[arc.head, a] -= 1.0
    b_eq = np.array(supplies, dtype=float)

    res = linprog(c, A_eq=a_eq, b_eq=b_eq, bounds=bounds, method="highs")
    if not res.success:
        raise RuntimeError(f"linprog failed: {res.message}")

    arc_flows = []
    for a, arc in enumerate(arcs):
        f = res.x[a]
        if f > 1e-6:
            arc_flows.append((_arc_label(arc), float(f)))
    return FlowSolution("linprog_highs", float(res.fun), arc_flows)


def solve_flow(data: NetworkData, opened_idx: list[int]) -> dict:
    """Solve both ways and return both solutions plus their agreement gap."""
    graph = solve_flow_graph(data, opened_idx)
    lp = solve_flow_lp(data, opened_idx)
    gap = abs(graph.total_cost - lp.total_cost)
    rel_gap = gap / max(lp.total_cost, 1.0)
    return {"graph": graph, "lp": lp, "abs_gap": gap, "rel_gap": rel_gap}
