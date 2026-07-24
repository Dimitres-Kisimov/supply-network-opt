"""Tests for the facility-location MILP and greedy baseline."""

import numpy as np

from supplynet.data import generate_network
from supplynet.facility import solve_facility_greedy, solve_facility_milp


def test_milp_no_worse_than_greedy():
    d = generate_network(seed=42)
    milp = solve_facility_milp(d)
    greedy = solve_facility_greedy(d)
    # The MILP is optimal for this instance, so it cannot cost more.
    assert milp.total_cost <= greedy.total_cost + 1e-6


def test_milp_serves_all_demand_within_capacity():
    d = generate_network(seed=42)
    milp = solve_facility_milp(d)
    demand = d.customers["demand_mean"].to_numpy()
    # Every customer's demand is fully met.
    assert np.allclose(milp.assignment.sum(axis=0), demand, atol=1e-4)
    # No open DC exceeds its capacity; closed DCs ship nothing.
    cap = d.dcs["capacity"].to_numpy()
    shipped = milp.assignment.sum(axis=1)
    assert (shipped <= cap + 1e-4).all()
    opened = set(milp.opened)
    for i, dc_id in enumerate(d.dcs["dc_id"]):
        if dc_id not in opened:
            assert shipped[i] <= 1e-6


def test_milp_cost_components_consistent():
    d = generate_network(seed=42)
    milp = solve_facility_milp(d)
    recomputed = float((milp.assignment * d.dc_cust_cost).sum())
    assert abs(recomputed - milp.transport_cost) < 1e-4
    assert abs(milp.fixed_cost + milp.transport_cost - milp.total_cost) < 1e-4


def test_greedy_is_feasible():
    d = generate_network(seed=7)
    greedy = solve_facility_greedy(d)
    demand = d.customers["demand_mean"].to_numpy()
    assert np.allclose(greedy.assignment.sum(axis=0), demand, atol=1e-4)
    assert greedy.n_opened >= 1
