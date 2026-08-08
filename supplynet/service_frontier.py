"""Inventory service-level frontier: safety stock / inventory cost vs service.

The rest of the package fixes the service level at 95%. But the service target is
itself a lever with a price. Base-stock safety stock scales with the standard-
normal quantile ``z(service_level)``, and ``z`` climbs ever more steeply as the
target approaches 100%, so the *last* points of service cost far more inventory
than the first. This module sweeps the target service level and traces, for the
committed network, three curves a planner weighs:

  * safety stock (units) under each pooling regime -- decentralized (one stock
    point per zone), pooled by the actually-opened DCs (the "network"), and fully
    centralized;
  * the capital that safety stock ties up in dollars, and its annual carrying
    cost, via a labelled holding-cost assumption; and
  * the *marginal* inventory carrying cost of each additional point of service.

It reuses the existing ``pooling_analysis`` engine unchanged: every frontier point
is one ``pooling_analysis`` call at that service level, so at 95% the frontier
reproduces the package's headline pooling numbers exactly (a base-case collapse).

It also reads the square-root risk-pooling law the *other* way round -- as the
service the same inventory buys. Holding the safety stock a *decentralized*
network needs for the base service level, a *pooled* network reaches a strictly
higher service level: the units the square-root law frees up become extra
service. Inventory saved and service bought are two views of one textbook model.

IMPORTANT -- honesty. ``UNIT_VALUE_USD`` and ``HOLDING_RATE_PER_YEAR`` below are
ILLUSTRATIVE planning placeholders, not a real company's product value or cost of
capital; swap them for audited rates before quoting any dollar figure. The
safety-stock model assumes normal, independent demand and fixed lead times (the
same assumptions as ``safetystock``), so the pooling gain is an optimistic bound:
real demand is partially correlated, which erodes the square-root benefit.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
from scipy.stats import norm

from supplynet.data import NetworkData
from supplynet.safetystock import pooling_analysis

# --- Illustrative inventory-cost planning factors (NOT certified figures) ----
# Standing value of one synthetic "unit" of inventory ($). Placeholder.
UNIT_VALUE_USD = 50.0
# Annual inventory carrying rate (capital + storage + obsolescence + shrink) as a
# fraction of held value. A round textbook ballpark (~0.15-0.30); illustrative.
HOLDING_RATE_PER_YEAR = 0.25
# The target service level whose decentralized safety stock defines the fixed
# inventory budget for the "pooling buys service" read.
BASE_SERVICE_LEVEL = 0.95
# Service targets swept, ascending. Includes the package's 0.95 base case, and a
# denser tail where z(sl) -- and so the marginal cost of service -- accelerates.
SERVICE_GRID: tuple[float, ...] = (0.80, 0.85, 0.90, 0.95, 0.975, 0.99)


@dataclass
class FrontierPoint:
    """One target service level and the inventory it implies for the network."""

    service_level: float
    z: float
    ss_decentralized: float  # safety stock (units), one stock point per zone
    ss_network: float  # safety stock (units), pooled by the opened DCs
    ss_centralized: float  # safety stock (units), fully centralized
    inv_value_network: float  # capital tied up by the network safety stock ($)
    holding_cost_network: float  # annual carrying cost of that stock ($/yr)
    # Marginal carrying cost of moving up to this service level from the previous
    # swept level, per +1 service point ($/yr per point). 0.0 at the first point.
    marginal_cost_per_point: float = 0.0


@dataclass
class ServiceFrontier:
    unit_value_usd: float
    holding_rate_per_year: float
    base_service_level: float
    # Same-inventory-buys-more-service (the pooling dividend). The decentralized
    # safety stock for ``base_service_level`` is the fixed budget; on exactly that
    # many units the pooled regimes reach a higher safety factor ``z`` and hence a
    # higher modeled service level / lower stockout probability. z is exact; the
    # service level itself lands deep in the normal tail (unreliable there), so it
    # is reported as a direction, with an explicit caveat, not as an SLA.
    budget_units: float
    pooling_z_network: float
    pooling_z_centralized: float
    pooling_service_network: float
    pooling_service_centralized: float
    pooling_stockout_network: float
    pooling_stockout_centralized: float
    points: list[FrontierPoint] = field(default_factory=list)

    @property
    def base_point(self) -> FrontierPoint:
        """The swept point at (closest to) the base service level."""
        return min(self.points, key=lambda p: abs(p.service_level - self.base_service_level))

    @property
    def steepest(self) -> FrontierPoint:
        """The service increment with the highest marginal carrying cost."""
        return max(self.points, key=lambda p: p.marginal_cost_per_point)

    @property
    def marginal_ratio_top_to_bottom(self) -> float:
        """How many times pricier the last point of service is vs the first.

        Ratio of the top swept increment's marginal $/point to the first non-zero
        increment's. Large because ``z(sl)`` is convex: the last points cost most.
        """
        marginals = [p.marginal_cost_per_point for p in self.points if p.marginal_cost_per_point > 0]
        if len(marginals) < 2:
            return 1.0
        return marginals[-1] / marginals[0]


def _regime_constants(data: NetworkData, assignment: np.ndarray | None) -> tuple[float, float, float]:
    """Return the z-independent safety-stock constants (K) for each pooling regime.

    Base-stock safety stock is ``z(sl) * K`` where ``K = sqrt(avg_lead_time) *
    (pooled) sigma`` depends only on demand and pooling, not on the service level.
    We recover each ``K`` by dividing a single ``pooling_analysis`` result by its
    ``z``, so the constants stay exactly consistent with that engine.
    """
    ref = pooling_analysis(data, service_level=BASE_SERVICE_LEVEL, assignment=assignment)
    k_dec = ref.decentralized / ref.z
    k_net = ref.network / ref.z
    k_cen = ref.centralized / ref.z
    return k_dec, k_net, k_cen


def run_service_frontier(
    data: NetworkData,
    assignment: np.ndarray | None = None,
    service_levels: Sequence[float] = SERVICE_GRID,
    base_service_level: float = BASE_SERVICE_LEVEL,
    unit_value_usd: float = UNIT_VALUE_USD,
    holding_rate_per_year: float = HOLDING_RATE_PER_YEAR,
) -> ServiceFrontier:
    """Sweep the target service level and trace the inventory it costs.

    ``assignment`` is the committed facility solution's DC->customer matrix; when
    given, the "network" regime pools each zone's variance into its serving DC
    (as ``pooling_analysis`` does). When ``None``, the network regime equals full
    centralization, matching ``pooling_analysis``'s own fallback.
    """
    levels = sorted(set(service_levels))
    points: list[FrontierPoint] = []
    prev: FrontierPoint | None = None
    for sl in levels:
        p = pooling_analysis(data, service_level=sl, assignment=assignment)
        inv_value = p.network * unit_value_usd
        holding = inv_value * holding_rate_per_year
        marginal = 0.0
        if prev is not None:
            d_points = 100.0 * (sl - prev.service_level)
            if d_points > 0:
                marginal = (holding - prev.holding_cost_network) / d_points
        point = FrontierPoint(
            service_level=sl,
            z=p.z,
            ss_decentralized=p.decentralized,
            ss_network=p.network,
            ss_centralized=p.centralized,
            inv_value_network=inv_value,
            holding_cost_network=holding,
            marginal_cost_per_point=marginal,
        )
        points.append(point)
        prev = point

    # Pooling dividend: fix the inventory budget to the decentralized safety stock
    # for the base service level, then read what service the pooled regimes reach
    # on those same units. z scales linearly with the pooling constant K, so the
    # freed units lift z (hence the service level) by the pooling ratio.
    k_dec, k_net, k_cen = _regime_constants(data, assignment)
    z_base = float(norm.ppf(base_service_level))
    budget_units = z_base * k_dec
    z_net = budget_units / k_net if k_net > 0 else z_base
    z_cen = budget_units / k_cen if k_cen > 0 else z_base

    return ServiceFrontier(
        unit_value_usd=unit_value_usd,
        holding_rate_per_year=holding_rate_per_year,
        base_service_level=base_service_level,
        budget_units=budget_units,
        pooling_z_network=z_net,
        pooling_z_centralized=z_cen,
        pooling_service_network=float(norm.cdf(z_net)),
        pooling_service_centralized=float(norm.cdf(z_cen)),
        pooling_stockout_network=float(norm.sf(z_net)),
        pooling_stockout_centralized=float(norm.sf(z_cen)),
        points=points,
    )


def _fmt_stockout(p: float) -> str:
    """Human, bounded stockout-probability string (never a fake-precise tail)."""
    if p < 1e-4:
        return "<0.01%"
    return f"{p:.2%}"


def frontier_readout(frontier: ServiceFrontier) -> list[str]:
    """Plain-language, honest read of the service-level frontier. Returns lines."""
    lines: list[str] = []
    base = frontier.base_point
    top = frontier.points[-1]
    lines.append(
        f"At {base.service_level:.0%} service the network holds "
        f"{base.ss_network:,.0f} units of safety stock "
        f"(${base.holding_cost_network:,.0f}/yr carrying cost, modeled); "
        f"raising the target to {top.service_level:.1%} takes it to "
        f"{top.ss_network:,.0f} units (${top.holding_cost_network:,.0f}/yr)."
    )
    steep = frontier.steepest
    lines.append(
        f"Diminishing returns: the top service increment costs "
        f"${steep.marginal_cost_per_point:,.0f}/yr per point, about "
        f"{frontier.marginal_ratio_top_to_bottom:.1f}x the cost per point of the "
        f"first increment -- the last points of service are the priciest."
    )
    base_stockout = 1.0 - frontier.base_service_level
    lines.append(
        f"Pooling dividend (service view): the {frontier.budget_units:,.0f} units a "
        f"decentralized network needs for {frontier.base_service_level:.0%} service, "
        f"pooled into the opened DCs, would cut the modeled stockout probability "
        f"from {base_stockout:.0%} to {_fmt_stockout(frontier.pooling_stockout_network)} "
        f"(safety factor z {norm.ppf(frontier.base_service_level):.2f} -> "
        f"{frontier.pooling_z_network:.2f}) -- the square-root law read as service "
        f"bought, not just inventory saved. That far into the normal tail the model "
        f"is unreliable, so treat it as directional, not a service guarantee."
    )
    lines.append(
        "Inventory dollars use an ILLUSTRATIVE unit value "
        f"(${frontier.unit_value_usd:,.0f}/unit) and carrying rate "
        f"({frontier.holding_rate_per_year:.0%}/yr), not certified figures; the "
        "square-root pooling gain assumes independent demand and is an optimistic "
        "bound."
    )
    return lines


def to_csv(frontier: ServiceFrontier) -> str:
    """Serialize the frontier to a deterministic CSV string (no wall-clock)."""
    header = (
        "# Supply-network inventory service-level frontier (synthetic data). "
        "Dollar figures use an ILLUSTRATIVE unit value "
        f"(${frontier.unit_value_usd:,.0f}/unit) and carrying rate "
        f"({frontier.holding_rate_per_year:.0%}/yr); not certified. Safety-stock "
        "pooling assumes independent demand.\n"
    )
    cols = (
        "service_level,z,ss_decentralized_units,ss_network_units,"
        "ss_centralized_units,inv_value_network_usd,holding_cost_network_usd_per_yr,"
        "marginal_usd_per_service_point"
    )
    lines = [header.rstrip("\n"), cols]
    for p in frontier.points:
        lines.append(
            f"{p.service_level:.4f},{p.z:.4f},{p.ss_decentralized:.2f},"
            f"{p.ss_network:.2f},{p.ss_centralized:.2f},{p.inv_value_network:.2f},"
            f"{p.holding_cost_network:.2f},{p.marginal_cost_per_point:.2f}"
        )
    return "\n".join(lines) + "\n"


def to_svg(frontier: ServiceFrontier) -> str:
    """Hand-drawn, deterministic SVG of carrying cost vs service level.

    Two curves -- decentralized and network (pooled) annual carrying cost -- across
    the swept service levels. Built as plain XML from the numbers, with no plotting
    library, no RNG and no timestamp, so the committed file is byte-stable.
    """
    w, h = 640, 420
    ml, mr, mt, mb = 84, 24, 48, 60  # margins
    pw, ph = w - ml - mr, h - mt - mb

    pts = frontier.points
    rate = frontier.holding_rate_per_year
    val = frontier.unit_value_usd
    dec_cost = [p.ss_decentralized * val * rate for p in pts]
    net_cost = [p.holding_cost_network for p in pts]
    slmin = pts[0].service_level
    slmax = pts[-1].service_level
    slrange = slmax - slmin or 1.0
    cmax = max(dec_cost) or 1.0

    def px(sl: float) -> float:
        return ml + pw * (sl - slmin) / slrange

    def py(cost: float) -> float:
        return mt + ph * (1.0 - cost / cmax)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}" font-family="Segoe UI, Arial, sans-serif">',
        f'<rect width="{w}" height="{h}" fill="#ffffff"/>',
        f'<text x="{w / 2:.0f}" y="24" text-anchor="middle" font-size="16" '
        f'font-weight="bold" fill="#1a1a1a">Inventory carrying cost vs service '
        f'level (synthetic; illustrative cost factors)</text>',
        # axes
        f'<line x1="{ml}" y1="{mt}" x2="{ml}" y2="{mt + ph}" stroke="#333" '
        f'stroke-width="1.2"/>',
        f'<line x1="{ml}" y1="{mt + ph}" x2="{ml + pw}" y2="{mt + ph}" '
        f'stroke="#333" stroke-width="1.2"/>',
        f'<text x="{ml + pw / 2:.0f}" y="{h - 20}" text-anchor="middle" '
        f'font-size="12" fill="#333">Target service level -&gt; (higher costs more)'
        f'</text>',
        f'<text x="18" y="{mt + ph / 2:.0f}" text-anchor="middle" font-size="12" '
        f'fill="#333" transform="rotate(-90 18 {mt + ph / 2:.0f})">'
        f'Safety-stock carrying cost ($/yr, modeled)</text>',
        # axis end labels
        f'<text x="{ml}" y="{mt + ph + 16}" text-anchor="start" font-size="10" '
        f'fill="#666">{slmin:.0%}</text>',
        f'<text x="{ml + pw}" y="{mt + ph + 16}" text-anchor="end" font-size="10" '
        f'fill="#666">{slmax:.1%}</text>',
        f'<text x="{ml - 6}" y="{mt + 8:.0f}" text-anchor="end" font-size="10" '
        f'fill="#666">${cmax:,.0f}</text>',
        f'<text x="{ml - 6}" y="{mt + ph:.0f}" text-anchor="end" font-size="10" '
        f'fill="#666">$0</text>',
    ]

    dec_poly = " ".join(f"{px(p.service_level):.1f},{py(c):.1f}" for p, c in zip(pts, dec_cost, strict=True))
    net_poly = " ".join(f"{px(p.service_level):.1f},{py(c):.1f}" for p, c in zip(pts, net_cost, strict=True))
    parts.append(
        f'<polyline points="{dec_poly}" fill="none" stroke="#c44e52" '
        f'stroke-width="1.8" stroke-dasharray="5 3"/>'
    )
    parts.append(
        f'<polyline points="{net_poly}" fill="none" stroke="#1f9d55" '
        f'stroke-width="2.0"/>'
    )
    for p, c in zip(pts, dec_cost, strict=True):
        parts.append(
            f'<circle cx="{px(p.service_level):.1f}" cy="{py(c):.1f}" r="3.2" '
            f'fill="#c44e52"/>'
        )
    for p, c in zip(pts, net_cost, strict=True):
        parts.append(
            f'<circle cx="{px(p.service_level):.1f}" cy="{py(c):.1f}" r="4" '
            f'fill="#1f9d55" stroke="#222" stroke-width="0.7"/>'
        )
        parts.append(
            f'<text x="{px(p.service_level):.1f}" y="{py(c) - 8:.1f}" '
            f'text-anchor="middle" font-size="9" fill="#222">{p.service_level:.0%}</text>'
        )

    # legend
    lx, ly = ml + 12, mt + 6
    parts.append(
        f'<line x1="{lx}" y1="{ly}" x2="{lx + 22}" y2="{ly}" stroke="#1f9d55" '
        f'stroke-width="2.0"/>'
        f'<text x="{lx + 28}" y="{ly + 4}" font-size="10" fill="#333">'
        f'Network (pooled by opened DCs)</text>'
    )
    parts.append(
        f'<line x1="{lx}" y1="{ly + 16}" x2="{lx + 22}" y2="{ly + 16}" '
        f'stroke="#c44e52" stroke-width="1.8" stroke-dasharray="5 3"/>'
        f'<text x="{lx + 28}" y="{ly + 20}" font-size="10" fill="#333">'
        f'Decentralized (one stock point / zone)</text>'
    )
    parts.append(
        f'<text x="{ml}" y="{h - 6}" font-size="9" fill="#999">Curves rise convexly'
        f': the last points of service cost the most. Synthetic data; cost factors '
        f'illustrative.</text>'
    )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"
