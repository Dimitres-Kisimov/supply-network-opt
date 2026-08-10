"""Tests for the demand-growth capacity plan (expansion triggers + headroom)."""

import functools
import math

import numpy as np
import pandas as pd
import pytest

from supplynet.data import NetworkData, generate_network
from supplynet.facility import solve_facility_milp
from supplynet.growth import (
    GROWTH_GRID,
    best_k_capacity,
    growth_readout,
    run_growth_plan,
    scale_demand,
    to_csv,
    to_svg,
)


@functools.lru_cache(maxsize=2)
def _plan(seed: int = 42):
    d = generate_network(seed=seed)
    m = solve_facility_milp(d)
    return d, m, run_growth_plan(d, base=m)


def _tiny() -> NetworkData:
    """Hand-checkable instance: one customer (demand 80), two candidate DCs.

    DC0: capacity 100, fixed $10, $1/unit outbound. DC1: capacity 1000, fixed
    $1000, $2/unit outbound. Optimal at base demand is DC0 alone (cost 90).
    """
    plants = pd.DataFrame({
        "plant_id": ["P0"], "x": [0.0], "y": [0.0],
        "capacity": [10_000.0], "prod_cost": [1.0],
    })
    dcs = pd.DataFrame({
        "dc_id": ["DC0", "DC1"], "x": [0.0, 0.0], "y": [0.0, 0.0],
        "capacity": [100.0, 1000.0], "fixed_cost": [10.0, 1000.0],
    })
    customers = pd.DataFrame({
        "cust_id": ["C0"], "x": [1.0], "y": [0.0],
        "demand_mean": [80.0], "demand_std": [8.0], "lead_time": [3.0],
    })
    return NetworkData(
        plants=plants, dcs=dcs, customers=customers,
        plant_dc_cost=np.zeros((1, 2)), dc_cust_cost=np.array([[1.0], [2.0]]),
        seed=0,
    )


# ---------------------------------------------------------------- scale_demand


def test_scale_demand_scales_mean_and_std_uniformly():
    d = generate_network(seed=42)
    s = scale_demand(d, 1.3)
    assert np.allclose(s.customers["demand_mean"], d.customers["demand_mean"] * 1.3)
    assert np.allclose(s.customers["demand_std"], d.customers["demand_std"] * 1.3)
    # Coefficient of variation (and hence the mix) is unchanged.
    assert np.allclose(
        s.customers["demand_std"] / s.customers["demand_mean"],
        d.customers["demand_std"] / d.customers["demand_mean"],
    )
    # Everything else is shared, not copied: same DCs, plants and cost matrices.
    assert s.dcs is d.dcs
    assert s.plants is d.plants
    assert s.plant_dc_cost is d.plant_dc_cost
    assert s.dc_cust_cost is d.dc_cust_cost
    assert math.isclose(s.total_demand, 1.3 * d.total_demand, rel_tol=1e-12)


def test_scale_demand_rejects_nonpositive_growth():
    d = _tiny()
    with pytest.raises(ValueError):
        scale_demand(d, 0.0)
    with pytest.raises(ValueError):
        scale_demand(d, -1.5)


def test_best_k_capacity_is_sum_of_largest():
    d = _tiny()
    assert best_k_capacity(d, 1) == 1000.0
    assert best_k_capacity(d, 2) == 1100.0


# ------------------------------------------------- hand-checked tiny instance


def test_tiny_instance_hand_checked_costs_and_triggers():
    d = _tiny()
    plan = run_growth_plan(d, growth_grid=(1.0, 1.2, 1.3, 1.5))
    assert [p.growth for p in plan.points] == [1.0, 1.2, 1.3, 1.5]

    # Hand-computed optima:
    #  g=1.0 (demand  80): DC0 alone, 10 + 80*1        =   90
    #  g=1.2 (demand  96): DC0 alone, 10 + 96*1        =  106
    #  g=1.3 (demand 104): both, 1010 + 100*1 + 4*2    = 1118
    #  g=1.5 (demand 120): both, 1010 + 100*1 + 20*2   = 1150
    expected = [90.0, 106.0, 1118.0, 1150.0]
    for p, exp in zip(plan.points, expected, strict=True):
        assert math.isclose(p.cost, exp, rel_tol=1e-7), (p.growth, p.cost, exp)

    assert plan.base_opened == ["DC0"]
    assert [p.opened for p in plan.points[:2]] == [["DC0"], ["DC0"]]
    assert plan.points[2].opened == ["DC0", "DC1"]

    # Committed (DC0 frozen): feasible up to its exact wall 100/80 = 1.25.
    assert math.isclose(plan.committed_wall_growth, 1.25, rel_tol=1e-12)
    assert math.isclose(plan.committed_headroom_pct, 25.0, rel_tol=1e-12)
    assert [p.committed_feasible for p in plan.points] == [True, True, False, False]
    assert math.isclose(plan.points[0].committed_cost, 90.0, rel_tol=1e-7)
    assert math.isclose(plan.points[1].committed_cost, 106.0, rel_tol=1e-7)
    # While the frozen set IS the optimal set, freezing costs nothing extra.
    assert plan.points[0].committed_premium == 0.0
    assert plan.points[1].committed_premium == 0.0

    # The first change is a true expansion (1 -> 2 DCs) at 1.3x.
    assert plan.first_expansion is plan.points[2]
    assert plan.first_reconfig is plan.points[2]
    assert plan.expansion_triggers == [plan.points[2]]

    # Exact analytic ceilings.
    assert math.isclose(plan.base_count_ceiling_growth, 1000.0 / 80.0, rel_tol=1e-12)
    assert math.isclose(plan.dc_ceiling_growth, 1100.0 / 80.0, rel_tol=1e-12)
    assert math.isclose(plan.plant_ceiling_growth, 10_000.0 / 80.0, rel_tol=1e-12)


def test_growth_beyond_dc_pool_truncates_sweep():
    d = _tiny()
    # 20x demand = 1600 units > the whole 1100-unit candidate pool: infeasible,
    # so the sweep must stop after the feasible levels (the ceiling explains why).
    plan = run_growth_plan(d, growth_grid=(1.0, 20.0))
    assert [p.growth for p in plan.points] == [1.0]
    assert plan.dc_ceiling_growth < 20.0


def test_growth_grid_is_deduplicated_and_sorted():
    d = _tiny()
    plan = run_growth_plan(d, growth_grid=(1.5, 1.0, 1.5))
    assert [p.growth for p in plan.points] == [1.0, 1.5]


# --------------------------------------------------------- seed-42 (documented)


def test_base_case_collapses_to_committed_milp():
    d, m, plan = _plan(42)
    p0 = plan.points[0]
    assert p0.growth == 1.0
    assert p0.opened == m.opened
    assert math.isclose(p0.cost, m.total_cost, rel_tol=1e-9)
    # At 1.0x the frozen network IS the optimum: same cost, zero premium.
    assert p0.committed_feasible
    assert math.isclose(p0.committed_cost, m.total_cost, rel_tol=1e-7)
    assert p0.committed_premium == 0.0
    assert not p0.reconfigured and not p0.expanded
    assert plan.base_opened == m.opened
    assert math.isclose(plan.base_demand, d.total_demand, rel_tol=1e-12)


def test_committed_wall_is_exact_capacity_ratio():
    d, m, plan = _plan(42)
    dc_ids = list(d.dcs["dc_id"])
    cap = d.dcs["capacity"].to_numpy()
    committed_cap = sum(cap[dc_ids.index(dc)] for dc in m.opened)
    assert math.isclose(plan.committed_capacity, committed_cap, rel_tol=1e-12)
    assert math.isclose(
        plan.committed_wall_growth, committed_cap / d.total_demand, rel_tol=1e-12
    )
    # Documented on seed 42: DC1+DC4+DC6 = 19,451 units -> +8.8% headroom.
    assert plan.committed_capacity == 19_451.0
    assert 1.087 < plan.committed_wall_growth < 1.089
    assert 8.7 < plan.committed_headroom_pct < 8.9
    # Feasibility flags agree with the exact wall at every swept level.
    for p in plan.points:
        assert p.committed_feasible == (
            p.growth * plan.base_demand <= plan.committed_capacity + 1e-6
        )
    assert plan.last_committed_feasible.growth == 1.05


def test_optimal_cost_strictly_increases_with_growth():
    _, _, plan = _plan(42)
    costs = [p.cost for p in plan.points]
    assert all(b > a for a, b in zip(costs, costs[1:], strict=False))


def test_opened_capacity_always_covers_scaled_demand():
    d, _, plan = _plan(42)
    dc_ids = list(d.dcs["dc_id"])
    cap = d.dcs["capacity"].to_numpy()
    for p in plan.points:
        opened_cap = sum(cap[dc_ids.index(dc)] for dc in p.opened)
        assert opened_cap >= p.demand_units - 1e-6
        assert p.utilization_pct <= 100.0 + 1e-6
        assert math.isclose(
            p.cost_per_unit, p.cost / p.demand_units, rel_tol=1e-12
        )


def test_committed_premium_nonnegative_and_frozen_never_cheaper():
    _, _, plan = _plan(42)
    for p in plan.points:
        if p.committed_feasible:
            # A frozen network is a restriction of the MILP: never cheaper.
            assert p.committed_cost >= p.cost - 1e-6
            assert p.committed_premium >= 0.0


def test_seed42_expansion_staircase_documented():
    _, _, plan = _plan(42)
    # Documented on seed 42: reshuffle first (swap DC6 for DC0 at 1.10x), the
    # 4th DC pays at 1.30x, the 5th at 1.65x, the 6th at 1.95x; 6 DCs at 2.00x.
    assert plan.base_n_opened == 3
    assert plan.first_reconfig.growth == 1.10
    assert plan.first_reconfig.n_opened == 3
    assert plan.first_expansion.growth == 1.30
    assert plan.first_expansion.n_opened == 4
    assert [t.growth for t in plan.expansion_triggers] == [1.30, 1.65, 1.95]
    assert [t.n_opened for t in plan.expansion_triggers] == [4, 5, 6]
    assert plan.points[-1].n_opened == 6
    # On this instance the optimal count never shrinks as demand grows.
    counts = [p.n_opened for p in plan.points]
    assert counts == sorted(counts)
    # The 4th DC arrives WITH the physical ceiling of 3-DC designs (~1.29x),
    # not earlier for cost reasons.
    assert 1.29 < plan.base_count_ceiling_growth < 1.30
    assert plan.first_expansion.growth >= plan.base_count_ceiling_growth
    # Up to the wall, freezing today's network costs nothing extra here.
    assert plan.max_committed_premium < 1.0
    # Exact echelon ceilings: DC pool 49,035 units (2.74x); plants 2.1x.
    assert plan.dc_portfolio_capacity == 49_035.0
    assert 2.74 < plan.dc_ceiling_growth < 2.75
    assert math.isclose(plan.plant_ceiling_growth, 2.1, rel_tol=1e-9)


def test_default_grid_starts_at_base_and_reaches_double():
    assert GROWTH_GRID[0] == 1.0
    assert GROWTH_GRID[-1] == 2.0
    assert list(GROWTH_GRID) == sorted(GROWTH_GRID)


# ------------------------------------------------- determinism and serializers


def test_is_deterministic_and_serializers_byte_stable():
    d, m, _ = _plan(42)
    a = run_growth_plan(d, base=m, growth_grid=(1.0, 1.1, 1.3))
    b = run_growth_plan(d, base=m, growth_grid=(1.0, 1.1, 1.3))
    assert [p.cost for p in a.points] == [p.cost for p in b.points]
    assert [p.opened for p in a.points] == [p.opened for p in b.points]
    assert [p.committed_cost for p in a.points] == [p.committed_cost for p in b.points]
    assert to_csv(a) == to_csv(b)
    assert to_svg(a) == to_svg(b)


def test_csv_carries_disclaimer_and_blank_committed_past_wall():
    _, _, plan = _plan(42)
    csv = to_csv(plan)
    assert "UNIFORM" in csv
    assert "committed_premium_usd" in csv
    body = [ln for ln in csv.splitlines() if ln and not ln.startswith("#")]
    assert len(body) == 1 + len(plan.points)  # column header + one row per point
    for p, row in zip(plan.points, body[1:], strict=True):
        if p.committed_feasible:
            assert row.split(",")[-1] != ""
        else:
            # Committed columns are blank past the capacity wall.
            assert row.endswith(",no,,")


def test_svg_is_valid_and_marks_the_wall():
    _, _, plan = _plan(42)
    svg = to_svg(plan)
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
    assert "committed wall" in svg
    assert "Synthetic" in svg or "synthetic" in svg


def test_readout_is_populated_and_honest():
    _, _, plan = _plan(42)
    lines = growth_readout(plan)
    assert len(lines) >= 5
    text = " ".join(lines)
    # Honest scope must be stated: uniform growth, synthetic, deterministic.
    assert "UNIFORM" in text
    assert "synthetic" in text.lower()
    assert "deterministic" in text.lower()
    # The headline wall and the first trigger are both reported.
    assert f"{plan.committed_wall_growth:.3f}x" in text
    assert f"{plan.first_expansion.growth:.2f}x" in text
    # The plant echelon caveat (not re-solved in the siting sweep) is present.
    assert "plant" in text.lower()


def test_second_seed_is_usable():
    d = generate_network(seed=7)
    m = solve_facility_milp(d)
    plan = run_growth_plan(d, base=m, growth_grid=(1.0, 1.2))
    assert plan.points
    assert plan.points[0].opened == m.opened
    assert plan.committed_wall_growth >= 1.0  # the base network covers base demand
    assert all(not math.isnan(p.cost) for p in plan.points)
