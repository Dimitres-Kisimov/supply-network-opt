"""Tests for multi-echelon safety stock and risk pooling."""

import math

from scipy.stats import norm

from supplynet.data import generate_network
from supplynet.facility import solve_facility_milp
from supplynet.safetystock import (
    base_stock_safety,
    echelon_safety_stock,
    pooling_analysis,
    z_score,
)


def test_z_score_mapping():
    assert math.isclose(z_score(0.95), norm.ppf(0.95), rel_tol=1e-9)
    assert math.isclose(z_score(0.50), 0.0, abs_tol=1e-9)
    # Higher service level demands a larger safety factor.
    assert z_score(0.99) > z_score(0.95) > z_score(0.90)


def test_base_stock_scales_with_sqrt_lead_time():
    a = base_stock_safety(sigma=100, lead_time=4, service_level=0.95)
    b = base_stock_safety(sigma=100, lead_time=16, service_level=0.95)
    # 4x the lead time -> 2x the safety stock.
    assert math.isclose(b / a, 2.0, rel_tol=1e-9)


def test_pooling_reduces_total_safety_stock():
    d = generate_network(seed=42)
    milp = solve_facility_milp(d)
    p = pooling_analysis(d, service_level=0.95, assignment=milp.assignment)
    # Centralizing must not increase safety stock; more pooling -> less stock.
    assert p.centralized < p.decentralized
    assert p.network <= p.decentralized
    assert p.centralized <= p.network + 1e-6
    assert p.reduction_pct > 0
    assert p.network_reduction_pct > 0


def test_full_pooling_matches_sqrt_law_for_equal_zones():
    # For n equal, independent zones, pooling cuts safety stock by sqrt(n).
    class _Fake:
        pass

    import numpy as np
    import pandas as pd

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
    data = _Fake()
    data.customers = cust
    p = pooling_analysis(data, service_level=0.95)
    assert math.isclose(p.decentralized / p.centralized, math.sqrt(n), rel_tol=1e-6)


def test_echelon_safety_stock_positive():
    d = generate_network(seed=42)
    e = echelon_safety_stock(d, service_level=0.95)
    assert e["dc_echelon"] > 0
    assert e["plant_echelon"] > e["dc_echelon"]  # longer lead time upstream
    assert math.isclose(e["total"], e["dc_echelon"] + e["plant_echelon"], rel_tol=1e-9)
