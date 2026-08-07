"""Capacitated facility-location: MILP (OR-Tools CBC) vs a greedy baseline.

Model (classic CFLP): decide which candidate DCs to open (binary) and how much
each open DC ships to each customer (continuous), minimizing fixed opening cost
plus outbound transport cost, subject to demand satisfaction and DC capacity.

This is an NP-hard problem. For the instance sizes here CBC solves it to
optimality, but that is a statement about THIS synthetic model, not a guarantee
about any real-world network.
"""

from dataclasses import dataclass, field

import numpy as np
from ortools.linear_solver import pywraplp

from supplynet.data import NetworkData


@dataclass
class FacilitySolution:
    method: str
    opened: list[str]
    total_cost: float
    fixed_cost: float
    transport_cost: float
    # assignment[i][j] = units shipped from DC index i to customer index j
    assignment: np.ndarray = field(repr=False)
    status: str = "optimal"

    @property
    def n_opened(self) -> int:
        return len(self.opened)


def solve_facility_milp(
    data: NetworkData,
    time_limit_s: float = 30.0,
    n_open: int | None = None,
    force_open: set[int] | None = None,
    force_closed: set[int] | None = None,
) -> FacilitySolution:
    """Solve the capacitated facility-location MILP with CBC.

    Returns the optimal (for this instance) set of open DCs and the flow.

    If ``n_open`` is given, the model is constrained to open *exactly* that many
    DCs (``sum(y) == n_open``). This is what the CO2/sensitivity sweep uses to
    trace the cost-optimal design at each network density; leaving it ``None``
    reproduces the unconstrained optimum and is fully backward compatible.

    ``force_open`` / ``force_closed`` pin individual DC indices open (``y == 1``)
    or closed (``y == 0``). The resilience module uses these to model a disrupted
    network: the failed DC is forced closed and the surviving committed DCs are
    forced open, so the solver only chooses which standby to activate. Leaving
    both ``None`` is fully backward compatible.

    Raises ``RuntimeError`` if the constrained model is infeasible (e.g. too few
    DCs to cover demand).
    """
    n_dc = data.n_dcs
    n_cust = data.n_customers
    demand = data.customers["demand_mean"].to_numpy()
    capacity = data.dcs["capacity"].to_numpy()
    fixed = data.dcs["fixed_cost"].to_numpy()
    cost = data.dc_cust_cost  # (n_dc x n_cust) per unit

    solver = pywraplp.Solver.CreateSolver("CBC")
    if solver is None:
        raise RuntimeError("CBC solver unavailable in this OR-Tools build")
    solver.SetTimeLimit(int(time_limit_s * 1000))

    y = [solver.BoolVar(f"open_{i}") for i in range(n_dc)]
    x = {
        (i, j): solver.NumVar(0.0, demand[j], f"x_{i}_{j}")
        for i in range(n_dc)
        for j in range(n_cust)
    }

    # Each customer's demand must be fully served.
    for j in range(n_cust):
        solver.Add(solver.Sum(x[i, j] for i in range(n_dc)) == demand[j])

    # A DC can only ship if it is open, and never above its capacity.
    for i in range(n_dc):
        solver.Add(solver.Sum(x[i, j] for j in range(n_cust)) <= capacity[i] * y[i])

    # Optionally force an exact number of open DCs (used by the sensitivity sweep).
    if n_open is not None:
        solver.Add(solver.Sum(y[i] for i in range(n_dc)) == n_open)

    # Optionally pin specific DCs open/closed (used by the resilience recovery model).
    for i in force_open or ():
        solver.Add(y[i] == 1)
    for i in force_closed or ():
        solver.Add(y[i] == 0)

    solver.Minimize(
        solver.Sum(fixed[i] * y[i] for i in range(n_dc))
        + solver.Sum(cost[i, j] * x[i, j] for i in range(n_dc) for j in range(n_cust))
    )

    result = solver.Solve()
    if result not in (pywraplp.Solver.OPTIMAL, pywraplp.Solver.FEASIBLE):
        raise RuntimeError(f"MILP did not solve (status={result})")

    assignment = np.zeros((n_dc, n_cust))
    for i in range(n_dc):
        for j in range(n_cust):
            assignment[i, j] = x[i, j].solution_value()

    opened_idx = [i for i in range(n_dc) if y[i].solution_value() > 0.5]
    opened = [data.dcs.iloc[i]["dc_id"] for i in opened_idx]
    fixed_cost = float(sum(fixed[i] for i in opened_idx))
    transport_cost = float((assignment * cost).sum())
    status = "optimal" if result == pywraplp.Solver.OPTIMAL else "feasible"

    return FacilitySolution(
        method="milp_cbc",
        opened=opened,
        total_cost=fixed_cost + transport_cost,
        fixed_cost=fixed_cost,
        transport_cost=transport_cost,
        assignment=assignment,
        status=status,
    )


def solve_facility_greedy(data: NetworkData) -> FacilitySolution:
    """Named baseline: open the cheapest-to-open DCs until capacity covers demand,
    then serve each customer from the lowest-cost open DC with room left.

    This is a deliberately simple heuristic that ignores transport when choosing
    which DCs to open, so we can quantify what the MILP buys over it.
    """
    n_dc = data.n_dcs
    n_cust = data.n_customers
    demand = data.customers["demand_mean"].to_numpy()
    capacity = data.dcs["capacity"].to_numpy().copy()
    fixed = data.dcs["fixed_cost"].to_numpy()
    cost = data.dc_cust_cost
    total_demand = demand.sum()

    # Open cheapest-fixed-cost DCs until cumulative capacity covers demand.
    order = np.argsort(fixed)
    opened_idx: list[int] = []
    cum_cap = 0.0
    for i in order:
        opened_idx.append(int(i))
        cum_cap += capacity[i]
        if cum_cap >= total_demand:
            break

    remaining = {i: capacity[i] for i in opened_idx}
    assignment = np.zeros((n_dc, n_cust))
    # Serve each customer from cheapest open DCs first, splitting if needed.
    for j in range(n_cust):
        need = demand[j]
        for i in sorted(opened_idx, key=lambda i, j=j: cost[i, j]):
            if need <= 0:
                break
            take = min(need, remaining[i])
            if take <= 0:
                continue
            assignment[i, j] += take
            remaining[i] -= take
            need -= take
        if need > 1e-6:
            raise RuntimeError("greedy baseline could not satisfy demand")

    opened = [data.dcs.iloc[i]["dc_id"] for i in opened_idx]
    fixed_cost = float(sum(fixed[i] for i in opened_idx))
    transport_cost = float((assignment * cost).sum())

    return FacilitySolution(
        method="greedy_cheapest",
        opened=opened,
        total_cost=fixed_cost + transport_cost,
        fixed_cost=fixed_cost,
        transport_cost=transport_cost,
        assignment=assignment,
        status="heuristic",
    )
