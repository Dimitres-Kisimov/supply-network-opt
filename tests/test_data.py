"""Tests for the seeded data generator."""

import numpy as np

from supplynet.data import generate_network


def test_data_is_deterministic():
    a = generate_network(seed=42)
    b = generate_network(seed=42)
    assert list(a.customers["demand_mean"]) == list(b.customers["demand_mean"])
    assert np.allclose(a.dc_cust_cost, b.dc_cust_cost)
    assert np.allclose(a.plant_dc_cost, b.plant_dc_cost)
    assert list(a.dcs["fixed_cost"]) == list(b.dcs["fixed_cost"])


def test_different_seeds_differ():
    a = generate_network(seed=1)
    b = generate_network(seed=2)
    assert list(a.customers["demand_mean"]) != list(b.customers["demand_mean"])


def test_shapes_and_capacity_cover_demand():
    d = generate_network(seed=42)
    assert d.plant_dc_cost.shape == (d.n_plants, d.n_dcs)
    assert d.dc_cust_cost.shape == (d.n_dcs, d.n_customers)
    # Total DC capacity and total plant capacity both cover demand.
    assert d.dcs["capacity"].sum() >= d.total_demand
    assert d.plants["capacity"].sum() >= d.total_demand


def test_costs_positive_and_demand_std_reasonable():
    d = generate_network(seed=42)
    assert (d.dc_cust_cost >= 0).all()
    assert (d.plant_dc_cost >= 0).all()
    assert (d.customers["demand_std"] > 0).all()
    # CoV between 0.20 and 0.45 by construction.
    cov = d.customers["demand_std"] / d.customers["demand_mean"]
    assert cov.between(0.19, 0.46).all()
