"""Multi-echelon safety stock and the risk-pooling (square-root-law) effect.

Safety stock at a stocking point that faces demand with standard deviation
``sigma`` per period and a replenishment ``lead_time`` (in periods) at target
service level ``sl`` is the base-stock formula::

    SS = z(sl) * sqrt(lead_time) * sigma

where ``z(sl)`` is the standard-normal quantile. Assuming demand across zones is
independent, pooling n zones into one stocking point replaces
``sum_i sigma_i`` with ``sqrt(sum_i sigma_i^2)`` -- the classic risk-pooling /
square-root law: total safety stock falls by roughly ``sqrt(n)`` for equal zones.

These are standard textbook models. They assume normal, independent demand and a
fixed lead time; real demand is neither perfectly normal nor independent, so
treat the numbers as model-based estimates.
"""

from dataclasses import dataclass

import numpy as np
from scipy.stats import norm

from supplynet.data import NetworkData


def z_score(service_level: float) -> float:
    """Standard-normal quantile (safety factor) for a target service level."""
    if not 0.0 < service_level < 1.0:
        raise ValueError("service_level must be strictly between 0 and 1")
    return float(norm.ppf(service_level))


def base_stock_safety(sigma: float, lead_time: float, service_level: float) -> float:
    """Base-stock safety stock for a single stocking point."""
    return z_score(service_level) * np.sqrt(lead_time) * sigma


@dataclass
class PoolingResult:
    service_level: float
    z: float
    decentralized: float  # one stock point per customer zone
    centralized: float  # all demand pooled into a single stock point
    network: float  # pooled by the actually-opened DC assignment
    reduction_pct: float  # decentralized -> centralized
    network_reduction_pct: float  # decentralized -> network


def _pooled_std(sigmas: np.ndarray) -> float:
    """Std of the sum of independent demands = sqrt(sum of variances)."""
    return float(np.sqrt(np.sum(sigmas ** 2)))


def pooling_analysis(
    data: NetworkData,
    service_level: float = 0.95,
    assignment: np.ndarray | None = None,
) -> PoolingResult:
    """Compare decentralized, fully centralized, and network-pooled safety stock.

    A single lead time (the demand-weighted average across zones) is used so the
    three scenarios differ only by how demand is pooled, isolating the pooling
    effect.
    """
    sigma = data.customers["demand_std"].to_numpy()
    lt = data.customers["lead_time"].to_numpy()
    mean = data.customers["demand_mean"].to_numpy()
    z = z_score(service_level)

    # Demand-weighted average lead time keeps the comparison apples-to-apples.
    avg_lt = float(np.average(lt, weights=mean))
    sqrt_lt = np.sqrt(avg_lt)

    decentralized = float(z * sqrt_lt * np.sum(sigma))
    centralized = float(z * sqrt_lt * _pooled_std(sigma))

    if assignment is not None:
        # Pool each customer's variance into the DC that serves the most of it.
        served_by = np.argmax(assignment, axis=0)
        network_pooled = 0.0
        for dc_idx in np.unique(served_by):
            members = np.where(served_by == dc_idx)[0]
            network_pooled += _pooled_std(sigma[members])
        network = float(z * sqrt_lt * network_pooled)
    else:
        network = centralized

    reduction_pct = 100.0 * (decentralized - centralized) / decentralized
    network_reduction_pct = 100.0 * (decentralized - network) / decentralized

    return PoolingResult(
        service_level=service_level,
        z=z,
        decentralized=decentralized,
        centralized=centralized,
        network=network,
        reduction_pct=reduction_pct,
        network_reduction_pct=network_reduction_pct,
    )


def echelon_safety_stock(
    data: NetworkData, service_level: float = 0.95
) -> dict[str, float]:
    """Two-echelon safety stock: pooled DC echelon + an upstream plant echelon.

    The plant echelon faces fully aggregated demand (maximum pooling); the DC
    echelon is shown fully centralized for a like-for-like echelon comparison.
    """
    sigma = data.customers["demand_std"].to_numpy()
    lt = data.customers["lead_time"].to_numpy()
    mean = data.customers["demand_mean"].to_numpy()
    z = z_score(service_level)

    avg_lt = float(np.average(lt, weights=mean))
    dc_echelon = float(z * np.sqrt(avg_lt) * _pooled_std(sigma))
    # Upstream plant echelon: a longer replenishment lead time, same pooled sigma.
    plant_lt = avg_lt + 7.0
    plant_echelon = float(z * np.sqrt(plant_lt) * _pooled_std(sigma))

    return {
        "z": z,
        "avg_lead_time": avg_lt,
        "dc_echelon": dc_echelon,
        "plant_echelon": plant_echelon,
        "total": dc_echelon + plant_echelon,
    }
