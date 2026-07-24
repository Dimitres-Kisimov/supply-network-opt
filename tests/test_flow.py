"""Tests for the min-cost multi-echelon flow (graph solver + LP cross-check)."""

from collections import defaultdict

import numpy as np

from supplynet.data import generate_network
from supplynet.facility import solve_facility_milp
from supplynet.flow import solve_flow, solve_flow_graph, solve_flow_lp


def _opened_idx(d, milp):
    ids = list(d.dcs["dc_id"])
    return [ids.index(x) for x in milp.opened]


def test_lp_matches_graph_solver():
    d = generate_network(seed=42)
    milp = solve_facility_milp(d)
    idx = _opened_idx(d, milp)
    graph = solve_flow_graph(d, idx)
    lp = solve_flow_lp(d, idx)
    # Integral polytope: the two independent solvers must agree closely.
    assert abs(graph.total_cost - lp.total_cost) <= max(1.0, 1e-4 * lp.total_cost)


def test_flow_wrapper_reports_small_gap():
    d = generate_network(seed=3)
    milp = solve_facility_milp(d)
    idx = _opened_idx(d, milp)
    res = solve_flow(d, idx)
    assert res["rel_gap"] <= 1e-3


def test_flow_conservation_and_demand_balance():
    d = generate_network(seed=42)
    milp = solve_facility_milp(d)
    idx = _opened_idx(d, milp)
    graph = solve_flow_graph(d, idx)

    inflow = defaultdict(float)
    outflow = defaultdict(float)
    for label, f in graph.arc_flows:
        # label form "kind:A->B" or "kind:A" (single-node meta)
        parts = label.split(":", 1)[1].split("->")
        if len(parts) == 2:
            tail, head = parts
            outflow[tail] += f
            inflow[head] += f

    total_demand = float(np.round(d.customers["demand_mean"]).sum())
    # Conservation at every transshipment node (DCs) appearing on both sides.
    trans_nodes = set(inflow) & set(outflow)
    for node in trans_nodes:
        assert abs(inflow[node] - outflow[node]) < 1e-4

    # All customer demand is delivered.
    delivered = sum(
        f for label, f in graph.arc_flows if label.startswith("outbound:")
    )
    assert abs(delivered - total_demand) < 1.0


def test_flow_respects_dc_throughput_capacity():
    d = generate_network(seed=42)
    milp = solve_facility_milp(d)
    idx = _opened_idx(d, milp)
    graph = solve_flow_graph(d, idx)
    cap = {d.dcs.iloc[i]["dc_id"]: d.dcs.iloc[i]["capacity"] for i in idx}
    throughput = defaultdict(float)
    for label, f in graph.arc_flows:
        if label.startswith("throughput:"):
            dc = label.split(":", 1)[1]
            throughput[dc] += f
    for dc, used in throughput.items():
        assert used <= cap[dc] + 1e-4
