"""Tests for the inventory service-level frontier."""

import math

import numpy as np
import pandas as pd
from scipy.stats import norm

from supplynet.data import generate_network
from supplynet.facility import solve_facility_milp
from supplynet.safetystock import pooling_analysis
from supplynet.service_frontier import (
    HOLDING_RATE_PER_YEAR,
    SERVICE_GRID,
    UNIT_VALUE_USD,
    frontier_readout,
    run_service_frontier,
    to_csv,
    to_svg,
)


def _frontier(seed: int = 42):
    d = generate_network(seed=seed)
    m = solve_facility_milp(d)
    return d, m, run_service_frontier(d, assignment=m.assignment)


def test_z_matches_normal_quantile_at_each_point():
    _, _, f = _frontier(42)
    for p in f.points:
        assert math.isclose(p.z, float(norm.ppf(p.service_level)), rel_tol=1e-12)


def test_base_case_collapses_to_pooling_analysis():
    # At the base 95% service level the frontier must reproduce the package's
    # headline pooling numbers EXACTLY (same engine, one call per level).
    d, m, f = _frontier(42)
    ref = pooling_analysis(d, service_level=0.95, assignment=m.assignment)
    base = next(p for p in f.points if math.isclose(p.service_level, 0.95))
    assert base.ss_decentralized == ref.decentralized
    assert base.ss_network == ref.network
    assert base.ss_centralized == ref.centralized


def test_pooling_ordering_and_monotonic_in_service():
    _, _, f = _frontier(42)
    for p in f.points:
        # More pooling never needs more safety stock at a fixed service level.
        assert p.ss_centralized <= p.ss_network + 1e-9
        assert p.ss_network <= p.ss_decentralized + 1e-9
    # Every regime's safety stock rises with the service target.
    for regime in ("ss_decentralized", "ss_network", "ss_centralized"):
        vals = [getattr(p, regime) for p in f.points]
        assert vals == sorted(vals)
        assert vals[-1] > vals[0]


def test_holding_cost_is_units_times_value_times_rate():
    _, _, f = _frontier(42)
    for p in f.points:
        expected_val = p.ss_network * UNIT_VALUE_USD
        assert math.isclose(p.inv_value_network, expected_val, rel_tol=1e-12)
        assert math.isclose(
            p.holding_cost_network, expected_val * HOLDING_RATE_PER_YEAR, rel_tol=1e-12
        )


def test_marginal_cost_definition_and_first_point_zero():
    _, _, f = _frontier(42)
    assert f.points[0].marginal_cost_per_point == 0.0
    for prev, cur in zip(f.points, f.points[1:], strict=False):
        d_points = 100.0 * (cur.service_level - prev.service_level)
        expected = (cur.holding_cost_network - prev.holding_cost_network) / d_points
        assert math.isclose(cur.marginal_cost_per_point, expected, rel_tol=1e-9)


def test_marginal_cost_rises_the_last_points_cost_most():
    # z(sl) is convex, so each extra point of service costs strictly more than
    # the previous one -- the recognizable diminishing-returns shape.
    _, _, f = _frontier(42)
    marginals = [p.marginal_cost_per_point for p in f.points if p.marginal_cost_per_point > 0]
    assert marginals == sorted(marginals)
    assert all(b > a for a, b in zip(marginals, marginals[1:], strict=False))
    # Documented on seed 42: the top point of service is ~6x the first.
    assert f.marginal_ratio_top_to_bottom > 1.0
    assert 6.0 < f.marginal_ratio_top_to_bottom < 6.6
    # steepest is the highest-service increment here.
    assert f.steepest is f.points[-1]


def test_pooling_dividend_z_scales_with_pooling_ratio():
    # Holding the decentralized 95%-service budget, the pooled network reaches a
    # higher safety factor z in EXACT proportion to the safety-stock ratio.
    d, m, f = _frontier(42)
    base = next(p for p in f.points if math.isclose(p.service_level, 0.95))
    z_base = float(norm.ppf(0.95))
    ratio_net = base.ss_decentralized / base.ss_network
    ratio_cen = base.ss_decentralized / base.ss_centralized
    assert math.isclose(f.pooling_z_network, z_base * ratio_net, rel_tol=1e-9)
    assert math.isclose(f.pooling_z_centralized, z_base * ratio_cen, rel_tol=1e-9)
    # Pooling strictly buys service (lower stockout) on the same inventory.
    assert f.pooling_z_network > z_base
    assert f.pooling_z_centralized >= f.pooling_z_network
    assert f.pooling_service_network > 0.95
    assert f.pooling_stockout_network < 0.05
    assert math.isclose(
        f.pooling_stockout_network, float(norm.sf(f.pooling_z_network)), rel_tol=1e-9
    )


def test_equal_zones_pooling_dividend_matches_sqrt_law():
    # n equal, independent zones: fully pooling cuts safety stock by sqrt(n), so
    # the decentralized budget lifts z to z95 * sqrt(n) exactly.
    n = 9
    cust = pd.DataFrame(
        {
            "cust_id": [f"C{i}" for i in range(n)],
            "x": np.zeros(n),
            "y": np.zeros(n),
            "demand_mean": np.full(n, 500.0),
            "demand_std": np.full(n, 100.0),
            "lead_time": np.full(n, 4.0),
        }
    )

    class _Fake:
        pass

    data = _Fake()
    data.customers = cust
    # assignment=None -> the network regime equals full centralization.
    f = run_service_frontier(data, assignment=None)
    z95 = float(norm.ppf(0.95))
    assert math.isclose(f.pooling_z_centralized, z95 * math.sqrt(n), rel_tol=1e-9)
    assert math.isclose(f.pooling_z_network, f.pooling_z_centralized, rel_tol=1e-12)


def test_is_deterministic_and_serializers_byte_stable():
    d, m, _ = _frontier(42)
    a = run_service_frontier(d, assignment=m.assignment)
    b = run_service_frontier(d, assignment=m.assignment)
    assert [p.holding_cost_network for p in a.points] == [p.holding_cost_network for p in b.points]
    assert [p.z for p in a.points] == [p.z for p in b.points]
    assert to_csv(a) == to_csv(b)
    assert to_svg(a) == to_svg(b)


def test_csv_and_svg_carry_disclaimer_and_data():
    _, _, f = _frontier(42)
    csv = to_csv(f)
    assert "ILLUSTRATIVE" in csv
    assert "holding_cost_network_usd_per_yr" in csv
    body = [ln for ln in csv.splitlines() if ln and not ln.startswith("#")]
    assert len(body) == 1 + len(f.points)  # column header + one row per point
    svg = to_svg(f)
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
    assert "illustrative" in svg.lower()


def test_readout_is_populated_and_honest():
    _, _, f = _frontier(42)
    lines = frontier_readout(f)
    assert len(lines) >= 3
    # Must not oversell: the illustrative-factor disclaimer is present.
    assert any("ILLUSTRATIVE" in ln for ln in lines)
    # Deep-tail honesty about the pooling dividend must be stated.
    assert any("directional" in ln.lower() or "unreliable" in ln.lower() for ln in lines)


def test_default_grid_contains_base_case():
    # The base 95% level is in the default sweep so the collapse check is real.
    assert 0.95 in SERVICE_GRID


def test_custom_grid_and_base_outside_grid_work():
    d = generate_network(seed=7)
    m = solve_facility_milp(d)
    # A base service level that is NOT one of the swept points still yields a
    # well-defined pooling dividend (it depends only on the pooling constants).
    f = run_service_frontier(
        d, assignment=m.assignment, service_levels=(0.90, 0.98), base_service_level=0.93
    )
    assert [p.service_level for p in f.points] == [0.90, 0.98]
    assert not math.isnan(f.pooling_z_network)
    assert f.pooling_z_network > float(norm.ppf(0.93))


def test_second_seed_is_usable():
    _, _, f = _frontier(7)
    assert f.points
    assert all(not math.isnan(p.holding_cost_network) for p in f.points)
    assert f.marginal_ratio_top_to_bottom > 1.0
