"""End-to-end orchestration: data -> facility MILP + baseline -> flow -> pooling.

Everything downstream (CLI report, PDF, Excel, tests) consumes the single
``PipelineResult`` produced here, so the numbers are computed exactly once.
"""

from dataclasses import dataclass

from supplynet.co2_sensitivity import Co2Sensitivity, run_co2_sensitivity
from supplynet.data import NetworkData, generate_network
from supplynet.facility import (
    FacilitySolution,
    solve_facility_greedy,
    solve_facility_milp,
)
from supplynet.flow import solve_flow
from supplynet.resilience import ResilienceReport, resilience_analysis
from supplynet.safetystock import PoolingResult, echelon_safety_stock, pooling_analysis
from supplynet.service_frontier import ServiceFrontier, run_service_frontier


@dataclass
class PipelineResult:
    data: NetworkData
    milp: FacilitySolution
    greedy: FacilitySolution
    flow: dict
    pooling: PoolingResult
    echelon: dict
    service_level: float
    co2: Co2Sensitivity
    resilience: ResilienceReport
    service_frontier: ServiceFrontier

    @property
    def opened_idx(self) -> list[int]:
        ids = list(self.data.dcs["dc_id"])
        return [ids.index(d) for d in self.milp.opened]

    @property
    def savings_abs(self) -> float:
        return self.greedy.total_cost - self.milp.total_cost

    @property
    def savings_pct(self) -> float:
        return 100.0 * self.savings_abs / self.greedy.total_cost


def run_pipeline(seed: int = 42, service_level: float = 0.95) -> PipelineResult:
    """Run the full analysis for one seeded instance and return all results."""
    data = generate_network(seed=seed)
    milp = solve_facility_milp(data)
    greedy = solve_facility_greedy(data)

    opened_idx = [list(data.dcs["dc_id"]).index(d) for d in milp.opened]
    flow = solve_flow(data, opened_idx)

    pooling = pooling_analysis(data, service_level=service_level, assignment=milp.assignment)
    echelon = echelon_safety_stock(data, service_level=service_level)
    co2 = run_co2_sensitivity(data)
    resilience = resilience_analysis(data, milp)
    service_frontier = run_service_frontier(
        data, assignment=milp.assignment, base_service_level=service_level
    )

    return PipelineResult(
        data=data,
        milp=milp,
        greedy=greedy,
        flow=flow,
        pooling=pooling,
        echelon=echelon,
        service_level=service_level,
        co2=co2,
        resilience=resilience,
        service_frontier=service_frontier,
    )
