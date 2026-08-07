"""Disruption resilience: single-DC-outage (N-1) contingency screen + recovery.

A cost-optimal network commits to a lean set of opened DCs. Lean is efficient
but can be fragile: if one DC goes down (fire, strike, flood, IT outage) the
surviving DCs may not have the capacity to serve every customer. This module
runs the classic **N-1 contingency screen** on the committed network and, where
an outage causes lost demand, finds the cheapest way to recover.

Two questions, both reusing the existing cost data and the facility engine:

  1. OPERATIONAL screen. Knock out each opened DC in turn and re-optimize the
     outbound assignment over the *surviving committed* DCs, allowing UNMET
     demand (a slack). Surviving capacity may be short, so the unmet slack
     becomes the lost fill rate; the survivors' outbound transport is the
     re-routing cost. Same outbound cost matrix and DC capacities the facility
     model uses -- no geometry or cost re-derived. Solved as a transportation
     LP (OR-Tools GLOP).

  2. RECOVERY. For an outage the survivors cannot absorb, re-solve the
     capacitated facility MILP with the survivors forced open and the failed DC
     forced closed, letting it activate the cheapest standby (currently-closed)
     candidate DC(s) to restore 100% service. The added fixed cost plus the
     transport change is the recovery premium for that single failure.

All figures are model-based estimates on synthetic, seeded data, under the same
assumptions as the rest of the package (deterministic demand, per-unit
distance-proportional transport, capacity as the only hard operating limit).
"""

from dataclasses import dataclass, field

import numpy as np
from ortools.linear_solver import pywraplp

from supplynet.data import NetworkData
from supplynet.facility import FacilitySolution, solve_facility_milp


@dataclass
class OutageScenario:
    """One single-DC outage on the committed network.

    Operational fields describe the immediate impact if the network keeps only
    its surviving committed DCs; recovery fields describe the cheapest standby
    activation that would restore full service.
    """

    failed_dc: str
    survivors: list[str]
    surviving_capacity: float
    total_demand: float
    # -- operational impact (re-route within surviving committed DCs) --
    served_units: float
    unmet_units: float
    fill_rate_pct: float  # 100 * served / total demand
    reroute_transport_cost: float  # survivors' outbound transport on served units
    resilient: bool  # survivors alone still serve 100% of demand
    # -- recovery (activate the cheapest standby to restore full service) --
    recovery_possible: bool
    recovery_activated: list[str] = field(default_factory=list)  # standby DCs opened
    recovery_added_fixed: float = 0.0  # fixed cost of activated standby(s)
    recovery_transport: float = 0.0  # transport of the recovered full-service network
    recovery_premium: float = 0.0  # added fixed + (recovery transport - baseline transport)


@dataclass
class ResilienceReport:
    total_demand: float
    committed_opened: list[str]
    committed_transport: float
    scenarios: list[OutageScenario] = field(default_factory=list)

    @property
    def worst(self) -> OutageScenario:
        """The single outage with the lowest surviving fill rate."""
        return min(self.scenarios, key=lambda s: s.fill_rate_pct)

    @property
    def n_critical(self) -> int:
        """How many opened DCs cause lost demand if they alone go down."""
        return sum(1 for s in self.scenarios if not s.resilient)

    @property
    def n_1_resilient(self) -> bool:
        """True if the committed network survives *any* single DC loss at 100%."""
        return all(s.resilient for s in self.scenarios)

    @property
    def mean_fill_rate_pct(self) -> float:
        if not self.scenarios:
            return 0.0
        return float(np.mean([s.fill_rate_pct for s in self.scenarios]))

    @property
    def max_recovery_premium(self) -> float:
        prem = [s.recovery_premium for s in self.scenarios if s.recovery_possible]
        return max(prem) if prem else 0.0


def _reroute_within(
    data: NetworkData, survivor_idx: list[int]
) -> tuple[float, float, float]:
    """Re-route all demand over the surviving DCs, allowing unmet demand (slack).

    Transportation LP: x[i, j] units from survivor i to customer j, plus a slack
    u[j] for unmet demand at customer j. Serving one unit is always preferred to
    dropping it (the slack penalty exceeds the most expensive lane), so the LP
    serves as much as the surviving capacity allows, then minimizes transport
    on what it serves.

    Returns (served_units, unmet_units, reroute_transport_cost).
    """
    demand = data.customers["demand_mean"].to_numpy()
    capacity = data.dcs["capacity"].to_numpy()
    cost = data.dc_cust_cost  # (n_dc x n_cust) per unit
    n_cust = data.n_customers

    if not survivor_idx:
        return 0.0, float(demand.sum()), 0.0

    # Penalty per unmet unit: safely above the priciest lane so serving wins
    # whenever capacity permits (a strict hierarchy: fill rate first, cost second).
    penalty = float(cost.max()) * 1_000.0 + 1.0

    solver = pywraplp.Solver.CreateSolver("GLOP")
    if solver is None:
        raise RuntimeError("GLOP solver unavailable in this OR-Tools build")

    x = {
        (i, j): solver.NumVar(0.0, demand[j], f"x_{i}_{j}")
        for i in survivor_idx
        for j in range(n_cust)
    }
    u = [solver.NumVar(0.0, demand[j], f"u_{j}") for j in range(n_cust)]

    # Demand balance with slack: served + unmet == demand.
    for j in range(n_cust):
        solver.Add(solver.Sum(x[i, j] for i in survivor_idx) + u[j] == demand[j])
    # Surviving DC capacities.
    for i in survivor_idx:
        solver.Add(solver.Sum(x[i, j] for j in range(n_cust)) <= capacity[i])

    solver.Minimize(
        solver.Sum(cost[i, j] * x[i, j] for i in survivor_idx for j in range(n_cust))
        + solver.Sum(penalty * u[j] for j in range(n_cust))
    )

    result = solver.Solve()
    if result not in (pywraplp.Solver.OPTIMAL, pywraplp.Solver.FEASIBLE):
        raise RuntimeError(f"re-route LP did not solve (status={result})")

    unmet = float(sum(u[j].solution_value() for j in range(n_cust)))
    transport = float(
        sum(
            cost[i, j] * x[i, j].solution_value()
            for i in survivor_idx
            for j in range(n_cust)
        )
    )
    served = float(demand.sum()) - unmet
    return served, unmet, transport


def _recover(
    data: NetworkData,
    survivor_idx: list[int],
    failed_idx: int,
    baseline_transport: float,
) -> OutageScenario | None:
    """Cheapest full-service network that keeps survivors and drops the failed DC.

    Re-solves the facility MILP with the survivors forced open and the failed DC
    forced closed, so the only free choice is which standby (currently-closed)
    candidate to activate. Returns the recovery fields, or ``None`` if even the
    whole remaining fleet cannot cover demand without the failed DC.
    """
    try:
        rec: FacilitySolution = solve_facility_milp(
            data,
            force_open=set(survivor_idx),
            force_closed={failed_idx},
        )
    except RuntimeError:
        return None

    survivors = {data.dcs.iloc[i]["dc_id"] for i in survivor_idx}
    activated = [dc for dc in rec.opened if dc not in survivors]
    dc_ids = list(data.dcs["dc_id"])
    added_fixed = float(
        sum(data.dcs.iloc[dc_ids.index(dc)]["fixed_cost"] for dc in activated)
    )
    premium = added_fixed + (rec.transport_cost - baseline_transport)
    return OutageScenario(
        failed_dc=data.dcs.iloc[failed_idx]["dc_id"],
        survivors=[data.dcs.iloc[i]["dc_id"] for i in survivor_idx],
        surviving_capacity=0.0,  # filled in by the caller
        total_demand=data.total_demand,
        served_units=0.0,
        unmet_units=0.0,
        fill_rate_pct=0.0,
        reroute_transport_cost=0.0,
        resilient=False,
        recovery_possible=True,
        recovery_activated=activated,
        recovery_added_fixed=added_fixed,
        recovery_transport=rec.transport_cost,
        recovery_premium=premium,
    )


def resilience_analysis(
    data: NetworkData, committed: FacilitySolution
) -> ResilienceReport:
    """Run the N-1 outage screen and recovery for a committed facility solution.

    For each opened DC: re-route within the surviving committed DCs (operational
    impact), then find the cheapest standby activation that restores full service
    (recovery). ``committed`` is the cost-optimal facility solution being stress-
    tested; its outbound transport is the baseline the recovery premium is
    measured against.
    """
    dc_ids = list(data.dcs["dc_id"])
    capacity = data.dcs["capacity"].to_numpy()
    total_demand = data.total_demand
    baseline_transport = committed.transport_cost
    opened_idx = [dc_ids.index(dc) for dc in committed.opened]

    scenarios: list[OutageScenario] = []
    for failed_idx in opened_idx:
        survivor_idx = [i for i in opened_idx if i != failed_idx]
        surv_cap = float(sum(capacity[i] for i in survivor_idx))

        served, unmet, reroute_cost = _reroute_within(data, survivor_idx)
        fill_rate = 100.0 * served / total_demand if total_demand else 0.0
        resilient = unmet <= 1e-6

        recovery = _recover(data, survivor_idx, failed_idx, baseline_transport)

        scenario = OutageScenario(
            failed_dc=dc_ids[failed_idx],
            survivors=[dc_ids[i] for i in survivor_idx],
            surviving_capacity=surv_cap,
            total_demand=total_demand,
            served_units=served,
            unmet_units=unmet,
            fill_rate_pct=fill_rate,
            reroute_transport_cost=reroute_cost,
            resilient=resilient,
            recovery_possible=recovery is not None,
            recovery_activated=recovery.recovery_activated if recovery else [],
            recovery_added_fixed=recovery.recovery_added_fixed if recovery else 0.0,
            recovery_transport=recovery.recovery_transport if recovery else 0.0,
            recovery_premium=recovery.recovery_premium if recovery else 0.0,
        )
        scenarios.append(scenario)

    return ResilienceReport(
        total_demand=total_demand,
        committed_opened=list(committed.opened),
        committed_transport=baseline_transport,
        scenarios=scenarios,
    )


def resilience_readout(report: ResilienceReport) -> list[str]:
    """Plain-language, honest read of the N-1 screen. Returns a list of lines."""
    lines: list[str] = []
    if report.n_1_resilient:
        lines.append(
            f"The committed network ({', '.join(report.committed_opened)}) is "
            "N-1 resilient: losing any single opened DC, the survivors still "
            "serve 100% of demand."
        )
    else:
        w = report.worst
        lines.append(
            f"The committed network is NOT N-1 resilient: {report.n_critical} of "
            f"{len(report.scenarios)} opened DCs are critical (their loss leaves "
            "demand unserved within the surviving fleet)."
        )
        lines.append(
            f"Worst single loss is {w.failed_dc}: surviving capacity "
            f"{w.surviving_capacity:,.0f} vs demand {w.total_demand:,.0f} units "
            f"serves only {w.fill_rate_pct:.1f}% ({w.unmet_units:,.0f} units unmet)."
        )
    # Recovery summary across the critical outages.
    criticals = [s for s in report.scenarios if not s.resilient]
    recoverable = [s for s in criticals if s.recovery_possible]
    if recoverable:
        worst_rec = max(recoverable, key=lambda s: s.recovery_premium)
        lines.append(
            f"Cheapest recovery from the worst outage ({worst_rec.failed_dc}): "
            f"activate standby {', '.join(worst_rec.recovery_activated) or '(none)'} "
            f"to restore 100% service, at an added ${worst_rec.recovery_premium:,.0f} "
            "(fixed + transport) -- the resilience premium for that single failure."
        )
    lines.append(
        "Model-based estimate on synthetic data: capacity is the only hard limit "
        "modeled, and demand is deterministic; treat fill rates as a planning "
        "screen, not a guaranteed service outcome."
    )
    return lines
