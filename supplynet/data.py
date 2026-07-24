"""Seeded, deterministic synthetic-data generator for the supply network.

All figures here are SYNTHETIC and generated from a fixed random seed. They are
meant to exercise the optimization models, not to describe any real company.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

# Per-unit transport rates (cost per unit per km). Last-mile (DC -> customer) is
# deliberately more expensive than inbound (plant -> DC), as it usually is.
INBOUND_RATE = 0.018
OUTBOUND_RATE = 0.041


@dataclass
class NetworkData:
    """A fully specified synthetic supply network.

    plants:    plant_id, x, y, capacity, prod_cost
    dcs:       dc_id, x, y, capacity, fixed_cost
    customers: cust_id, x, y, demand_mean, demand_std, lead_time
    plant_dc_cost:  (n_plants x n_dcs) per-unit inbound transport cost
    dc_cust_cost:   (n_dcs x n_customers) per-unit outbound transport cost
    """

    plants: pd.DataFrame
    dcs: pd.DataFrame
    customers: pd.DataFrame
    plant_dc_cost: np.ndarray
    dc_cust_cost: np.ndarray
    seed: int

    @property
    def n_plants(self) -> int:
        return len(self.plants)

    @property
    def n_dcs(self) -> int:
        return len(self.dcs)

    @property
    def n_customers(self) -> int:
        return len(self.customers)

    @property
    def total_demand(self) -> float:
        return float(self.customers["demand_mean"].sum())


def _distance_matrix(a: pd.DataFrame, b: pd.DataFrame) -> np.ndarray:
    """Euclidean distance between every row of a and every row of b (km grid)."""
    ax = a["x"].to_numpy()[:, None]
    ay = a["y"].to_numpy()[:, None]
    bx = b["x"].to_numpy()[None, :]
    by = b["y"].to_numpy()[None, :]
    return np.sqrt((ax - bx) ** 2 + (ay - by) ** 2)


def generate_network(
    seed: int = 42,
    n_plants: int = 3,
    n_dcs: int = 8,
    n_customers: int = 30,
    grid: float = 100.0,
) -> NetworkData:
    """Generate a deterministic synthetic network on a ``grid`` x ``grid`` km map.

    The generator is seeded, so the same arguments always return identical data.
    """
    rng = np.random.default_rng(seed)

    # Customer zones: demand mean/std and a replenishment lead time (days).
    cust = pd.DataFrame(
        {
            "cust_id": [f"C{j:02d}" for j in range(n_customers)],
            "x": rng.uniform(0, grid, n_customers),
            "y": rng.uniform(0, grid, n_customers),
            "demand_mean": rng.integers(200, 1000, n_customers).astype(float),
            "lead_time": rng.integers(2, 10, n_customers).astype(float),
        }
    )
    # Demand std as a coefficient of variation (0.20-0.45) times the mean.
    cov = rng.uniform(0.20, 0.45, n_customers)
    cust["demand_std"] = (cust["demand_mean"] * cov).round(1)

    total_demand = float(cust["demand_mean"].sum())

    # Candidate DC locations: fixed opening cost + throughput capacity.
    dcs = pd.DataFrame(
        {
            "dc_id": [f"DC{i}" for i in range(n_dcs)],
            "x": rng.uniform(0, grid, n_dcs),
            "y": rng.uniform(0, grid, n_dcs),
            "capacity": rng.integers(4000, 9000, n_dcs).astype(float),
            "fixed_cost": rng.integers(60_000, 160_000, n_dcs).astype(float),
        }
    )

    # Plants: production capacity comfortably covers total demand, plus a
    # per-unit production cost that varies by site.
    plants = pd.DataFrame(
        {
            "plant_id": [f"P{k}" for k in range(n_plants)],
            "x": rng.uniform(0, grid, n_plants),
            "y": rng.uniform(0, grid, n_plants),
            "capacity": np.linspace(
                total_demand * 0.5, total_demand * 0.9, n_plants
            ).round(),
            "prod_cost": rng.uniform(3.0, 6.0, n_plants).round(2),
        }
    )

    plant_dc_cost = _distance_matrix(plants, dcs) * INBOUND_RATE
    dc_cust_cost = _distance_matrix(dcs, cust) * OUTBOUND_RATE

    return NetworkData(
        plants=plants,
        dcs=dcs,
        customers=cust,
        plant_dc_cost=plant_dc_cost,
        dc_cust_cost=dc_cust_cost,
        seed=seed,
    )
