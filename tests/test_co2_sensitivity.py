"""Tests for the CO2-aware variant and the cost/CO2/service sensitivity sweep."""

import numpy as np

from supplynet.co2_sensitivity import (
    EMISSION_FACTOR_KG_PER_TKM,
    UNIT_WEIGHT_T,
    co2_per_unit_kg,
    evaluate_design,
    run_co2_sensitivity,
    to_csv,
    to_svg,
    tradeoff_readout,
)
from supplynet.data import generate_network
from supplynet.facility import solve_facility_milp


def test_n_open_forces_exact_dc_count():
    d = generate_network(seed=42)
    sol = solve_facility_milp(d, n_open=4)
    assert sol.n_opened == 4
    # Unconstrained default behaviour is unchanged (still 3 DCs on seed 42).
    assert solve_facility_milp(d).n_opened == 3


def test_co2_matches_manual_recompute():
    d = generate_network(seed=42)
    sol = solve_facility_milp(d)
    co2_t, _, _ = evaluate_design(d, sol)
    manual_kg = float((sol.assignment * co2_per_unit_kg(d)).sum())
    assert abs(co2_t - manual_kg / 1000.0) < 1e-9


def test_every_design_opens_exactly_its_count():
    d = generate_network(seed=42)
    s = run_co2_sensitivity(d)
    assert s.designs, "sweep produced no feasible designs"
    for pt in s.designs:
        assert len(pt.opened) == pt.n_dc
        assert pt.co2_t > 0
        assert 0.0 <= pt.service_within_radius_pct <= 100.0


def test_more_dcs_cut_co2_on_this_instance():
    d = generate_network(seed=42)
    s = run_co2_sensitivity(d)
    sparsest = min(s.designs, key=lambda p: p.n_dc)
    densest = max(s.designs, key=lambda p: p.n_dc)
    # Shorter last-mile hops with more DCs cut modeled outbound CO2 here.
    assert densest.co2_t <= sparsest.co2_t


def test_cost_optimal_matches_unconstrained_milp():
    d = generate_network(seed=42)
    s = run_co2_sensitivity(d)
    milp = solve_facility_milp(d)
    assert abs(s.cost_optimal.cost - milp.total_cost) < 1.0
    # No swept design undercuts the unconstrained optimum's cost.
    assert all(pt.cost >= milp.total_cost - 1e-6 for pt in s.designs)


def test_pareto_points_are_non_dominated():
    d = generate_network(seed=42)
    s = run_co2_sensitivity(d)
    assert s.pareto, "expected at least one Pareto-optimal design"
    for p in s.pareto:
        for o in s.designs:
            strictly_better = (o.cost <= p.cost and o.co2_t <= p.co2_t) and (
                o.cost < p.cost or o.co2_t < p.co2_t
            )
            assert not strictly_better


def test_sweep_is_deterministic():
    d = generate_network(seed=42)
    a = run_co2_sensitivity(d)
    b = run_co2_sensitivity(d)
    assert [p.cost for p in a.designs] == [p.cost for p in b.designs]
    assert [p.co2_t for p in a.designs] == [p.co2_t for p in b.designs]
    assert to_csv(a) == to_csv(b)
    assert to_svg(a) == to_svg(b)


def test_tradeoff_readout_is_honest_and_populated():
    d = generate_network(seed=42)
    s = run_co2_sensitivity(d)
    lines = tradeoff_readout(s)
    assert len(lines) >= 2
    # The illustrative-factor disclaimer must be present in the plain-language read.
    assert any("ILLUSTRATIVE" in ln for ln in lines)


def test_csv_and_svg_carry_disclaimer_and_data():
    d = generate_network(seed=42)
    s = run_co2_sensitivity(d)
    csv = to_csv(s)
    assert "ILLUSTRATIVE" in csv
    assert "co2_tonnes" in csv
    # One header/comment line plus a row per design.
    body = [ln for ln in csv.splitlines() if ln and not ln.startswith("#")]
    assert len(body) == 1 + len(s.designs)  # column header + rows
    svg = to_svg(s)
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
    assert "illustrative" in svg.lower()


def test_emission_constants_are_positive_placeholders():
    assert UNIT_WEIGHT_T > 0
    assert EMISSION_FACTOR_KG_PER_TKM > 0
    # A second seed still yields a usable, non-empty frontier.
    s = run_co2_sensitivity(generate_network(seed=7))
    assert s.designs
    assert not np.isnan(s.cost_optimal.co2_t)
