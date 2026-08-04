"""CO2-aware variant and a cost / CO2 / service sensitivity (Pareto) sweep.

Cost is not the only objective a real network planner weighs. A design that
looks cheapest on fixed + transport cost can quietly ship a lot of tonne-km,
and tonne-km is what turns into freight CO2. This module puts a second,
labelled number next to every design: its transport CO2. It then sweeps the
network density (the number of open DCs), re-solving the *existing* capacitated
facility-location MILP at each density, so we can see how cost, CO2 and a
last-mile service proxy trade off against each other.

    CO2 accounting (OUTBOUND / last-mile leg, the leg the siting decision drives)

        tonne_km[i, j] = units_shipped[i, j] * UNIT_WEIGHT_T * distance_km[i, j]
        co2_kg[i, j]   = tonne_km[i, j] * EMISSION_FACTOR_KG_PER_TKM

    distance_km is recovered exactly from the model's own outbound cost matrix
    (cost = distance * OUTBOUND_RATE), so no geometry is re-invented here.

IMPORTANT — honesty. ``UNIT_WEIGHT_T`` and ``EMISSION_FACTOR_KG_PER_TKM`` below
are ILLUSTRATIVE planning placeholders, not certified emission figures. Real
freight emission factors depend on vehicle class, load factor, fuel and
country grid, and would come from a source such as GLEC / EN 16258 / DEFRA.
Swap the two constants for audited factors before treating any CO2 number here
as anything other than a model-based estimate on synthetic data. Only the
outbound (DC -> customer) leg is counted, to stay like-for-like with the
facility model's own objective, which also prices only that leg.
"""

from dataclasses import dataclass, field

import numpy as np

from supplynet.data import OUTBOUND_RATE, NetworkData
from supplynet.facility import FacilitySolution, solve_facility_milp

# --- Illustrative CO2 planning factors (NOT certified figures) --------------
# Tonnes of freight per synthetic "unit". Placeholder for a real product weight.
UNIT_WEIGHT_T = 0.10
# Kg CO2e per tonne-km. A round road-freight ballpark; real values (~0.05-0.15,
# GLEC/DEFRA) vary with vehicle, load and fuel. Illustrative only.
EMISSION_FACTOR_KG_PER_TKM = 0.10
# A design is "in service" for a customer if its delivery hop is within this
# radius. Used only as an intuitive last-mile responsiveness proxy.
SERVICE_RADIUS_KM = 40.0


def outbound_distance_km(data: NetworkData) -> np.ndarray:
    """Recover the (n_dc x n_cust) outbound distance matrix from the cost matrix.

    ``dc_cust_cost = distance_km * OUTBOUND_RATE`` by construction in ``data``,
    so dividing back out gives the exact distances without re-deriving geometry.
    """
    return data.dc_cust_cost / OUTBOUND_RATE


def co2_per_unit_kg(data: NetworkData) -> np.ndarray:
    """Kg CO2e emitted per unit shipped on each DC -> customer lane."""
    return outbound_distance_km(data) * UNIT_WEIGHT_T * EMISSION_FACTOR_KG_PER_TKM


@dataclass
class DesignPoint:
    """One evaluated network design (a cost-optimal design at a fixed DC count)."""

    n_dc: int
    opened: list[str]
    cost: float  # true fixed + outbound transport cost ($)
    co2_t: float  # modeled outbound freight CO2 (tonnes)
    avg_delivery_km: float  # demand-weighted mean last-mile distance (lower = better)
    service_within_radius_pct: float  # % of demand delivered within SERVICE_RADIUS_KM
    is_pareto: bool = False  # non-dominated on (cost, CO2)?


@dataclass
class Co2Sensitivity:
    service_radius_km: float
    unit_weight_t: float
    emission_factor_kg_per_tkm: float
    designs: list[DesignPoint] = field(default_factory=list)

    @property
    def pareto(self) -> list["DesignPoint"]:
        return [d for d in self.designs if d.is_pareto]

    @property
    def cost_optimal(self) -> "DesignPoint":
        return min(self.designs, key=lambda d: d.cost)

    @property
    def min_co2(self) -> "DesignPoint":
        return min(self.designs, key=lambda d: d.co2_t)


def evaluate_design(data: NetworkData, sol: FacilitySolution) -> tuple[float, float, float]:
    """Return (CO2 tonnes, avg delivery km, % demand within radius) for a design.

    Uses the solved assignment (units per DC->customer lane) plus the recovered
    distances, so the CO2 is an exact function of what the solver actually chose.
    """
    dist = outbound_distance_km(data)
    per_unit = co2_per_unit_kg(data)
    assign = sol.assignment  # (n_dc x n_cust) units

    co2_kg = float((assign * per_unit).sum())
    total_units = float(assign.sum())
    weighted_km = float((assign * dist).sum())
    avg_km = weighted_km / total_units if total_units else 0.0
    within = float(assign[dist <= SERVICE_RADIUS_KM].sum())
    within_pct = 100.0 * within / total_units if total_units else 0.0
    return co2_kg / 1000.0, avg_km, within_pct


def _mark_pareto(designs: list[DesignPoint]) -> None:
    """Flag designs not dominated on (cost, CO2): lower cost AND lower CO2 is better."""
    for d in designs:
        dominated = any(
            (o.cost <= d.cost and o.co2_t <= d.co2_t)
            and (o.cost < d.cost or o.co2_t < d.co2_t)
            for o in designs
        )
        d.is_pareto = not dominated


def run_co2_sensitivity(data: NetworkData) -> Co2Sensitivity:
    """Sweep the number of open DCs and evaluate cost, CO2 and service at each.

    For every feasible DC count ``k`` we re-solve the existing facility-location
    MILP forced to open exactly ``k`` DCs, then attach the CO2 and service
    numbers. Infeasible counts (too little capacity to cover demand) are skipped.
    """
    designs: list[DesignPoint] = []
    for k in range(1, data.n_dcs + 1):
        try:
            sol = solve_facility_milp(data, n_open=k)
        except RuntimeError:
            # Not enough capacity in any k DCs to serve all demand; skip.
            continue
        co2_t, avg_km, within_pct = evaluate_design(data, sol)
        designs.append(
            DesignPoint(
                n_dc=k,
                opened=list(sol.opened),
                cost=sol.total_cost,
                co2_t=co2_t,
                avg_delivery_km=avg_km,
                service_within_radius_pct=within_pct,
            )
        )
    _mark_pareto(designs)
    return Co2Sensitivity(
        service_radius_km=SERVICE_RADIUS_KM,
        unit_weight_t=UNIT_WEIGHT_T,
        emission_factor_kg_per_tkm=EMISSION_FACTOR_KG_PER_TKM,
        designs=designs,
    )


def tradeoff_readout(sens: Co2Sensitivity) -> list[str]:
    """Plain-language read of the cost/CO2 trade-off. Returns a list of lines."""
    lines: list[str] = []
    base = sens.cost_optimal
    lines.append(
        f"Cost-optimal design opens {base.n_dc} DCs "
        f"({', '.join(base.opened)}): ${base.cost:,.0f}, "
        f"{base.co2_t:.2f} t CO2 (modeled), "
        f"{base.service_within_radius_pct:.0f}% of demand within "
        f"{sens.service_radius_km:.0f} km."
    )

    # The next-denser design above the cost optimum: what does one more DC buy?
    denser = [d for d in sens.designs if d.n_dc == base.n_dc + 1]
    if denser:
        nxt = denser[0]
        d_cost = nxt.cost - base.cost
        d_cost_pct = 100.0 * d_cost / base.cost
        d_co2_pct = 100.0 * (base.co2_t - nxt.co2_t) / base.co2_t if base.co2_t else 0.0
        verb = "cuts" if d_co2_pct >= 0 else "raises"
        lines.append(
            f"Adding a {nxt.n_dc}th DC {verb} modeled CO2 by "
            f"{abs(d_co2_pct):.1f}% ({base.co2_t:.2f} -> {nxt.co2_t:.2f} t) and "
            f"lifts within-{sens.service_radius_km:.0f}km service "
            f"{base.service_within_radius_pct:.0f}% -> "
            f"{nxt.service_within_radius_pct:.0f}%, "
            f"for ${d_cost:,.0f} ({d_cost_pct:+.1f}%) more total cost."
        )

    # The greenest design on the frontier vs the cost optimum.
    green = sens.min_co2
    if green.n_dc != base.n_dc:
        g_cost = green.cost - base.cost
        g_cost_pct = 100.0 * g_cost / base.cost
        g_co2_pct = 100.0 * (base.co2_t - green.co2_t) / base.co2_t if base.co2_t else 0.0
        lines.append(
            f"Greenest design opens {green.n_dc} DCs: modeled CO2 "
            f"{g_co2_pct:.1f}% below the cost optimum "
            f"({green.co2_t:.2f} t) but ${g_cost:,.0f} ({g_cost_pct:+.1f}%) "
            f"more costly -- the trade-off a planner has to price."
        )
    else:
        lines.append(
            "The cost-optimal design is also the lowest-CO2 design in this "
            "instance: no cost is traded away to cut emissions here."
        )
    lines.append(
        "CO2 uses an ILLUSTRATIVE emission factor "
        f"({sens.emission_factor_kg_per_tkm:.2f} kg CO2e/tonne-km, "
        f"{sens.unit_weight_t:.2f} t/unit), not a certified figure."
    )
    return lines


def to_csv(sens: Co2Sensitivity) -> str:
    """Serialize the sweep to a deterministic CSV string (no wall-clock)."""
    header = (
        "# Supply-network CO2 / cost / service sensitivity (synthetic data). "
        "CO2 uses an ILLUSTRATIVE emission factor "
        f"({sens.emission_factor_kg_per_tkm:.2f} kg CO2e/tonne-km, "
        f"{sens.unit_weight_t:.2f} t/unit); not a certified figure.\n"
    )
    cols = (
        "n_dcs,opened,cost_usd,co2_tonnes,avg_delivery_km,"
        f"pct_demand_within_{int(sens.service_radius_km)}km,pareto_optimal"
    )
    lines = [header.rstrip("\n"), cols]
    for d in sens.designs:
        lines.append(
            f"{d.n_dc},{'|'.join(d.opened)},{d.cost:.2f},{d.co2_t:.4f},"
            f"{d.avg_delivery_km:.2f},{d.service_within_radius_pct:.2f},"
            f"{'yes' if d.is_pareto else 'no'}"
        )
    return "\n".join(lines) + "\n"


def to_svg(sens: Co2Sensitivity) -> str:
    """Hand-drawn, deterministic SVG scatter of cost vs CO2 (Pareto highlighted).

    Built as plain XML from the swept numbers -- no plotting library, no RNG and
    no timestamp, so the committed file is byte-stable across runs.
    """
    w, h = 640, 420
    ml, mr, mt, mb = 78, 24, 48, 64  # margins
    pw, ph = w - ml - mr, h - mt - mb

    costs = [d.cost for d in sens.designs]
    co2s = [d.co2_t for d in sens.designs]
    cmin, cmax = min(costs), max(costs)
    emin, emax = min(co2s), max(co2s)
    crange = cmax - cmin or 1.0
    erange = emax - emin or 1.0

    def px(cost: float) -> float:
        return ml + pw * (cost - cmin) / crange

    def py(co2: float) -> float:
        # higher CO2 at top
        return mt + ph * (1.0 - (co2 - emin) / erange)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}" font-family="Segoe UI, Arial, sans-serif">',
        f'<rect width="{w}" height="{h}" fill="#ffffff"/>',
        f'<text x="{w / 2:.0f}" y="24" text-anchor="middle" font-size="16" '
        f'font-weight="bold" fill="#1a1a1a">Cost vs CO2 by network density '
        f'(synthetic; illustrative CO2 factor)</text>',
        # axes
        f'<line x1="{ml}" y1="{mt}" x2="{ml}" y2="{mt + ph}" stroke="#333" '
        f'stroke-width="1.2"/>',
        f'<line x1="{ml}" y1="{mt + ph}" x2="{ml + pw}" y2="{mt + ph}" '
        f'stroke="#333" stroke-width="1.2"/>',
        f'<text x="{ml + pw / 2:.0f}" y="{h - 22}" text-anchor="middle" '
        f'font-size="12" fill="#333">Total cost ($) - lower is better -&gt;</text>',
        f'<text x="18" y="{mt + ph / 2:.0f}" text-anchor="middle" font-size="12" '
        f'fill="#333" transform="rotate(-90 18 {mt + ph / 2:.0f})">'
        f'Modeled CO2 (t) - lower is better -&gt;</text>',
    ]

    # axis end labels
    parts.append(
        f'<text x="{ml}" y="{mt + ph + 16}" text-anchor="start" font-size="10" '
        f'fill="#666">${cmin:,.0f}</text>'
    )
    parts.append(
        f'<text x="{ml + pw}" y="{mt + ph + 16}" text-anchor="end" font-size="10" '
        f'fill="#666">${cmax:,.0f}</text>'
    )
    parts.append(
        f'<text x="{ml - 6}" y="{mt + ph:.0f}" text-anchor="end" font-size="10" '
        f'fill="#666">{emin:.2f}</text>'
    )
    parts.append(
        f'<text x="{ml - 6}" y="{mt + 8:.0f}" text-anchor="end" font-size="10" '
        f'fill="#666">{emax:.2f}</text>'
    )

    # connect Pareto points (sorted by cost) with a light line
    pareto = sorted(sens.pareto, key=lambda d: d.cost)
    if len(pareto) >= 2:
        poly = " ".join(f"{px(d.cost):.1f},{py(d.co2_t):.1f}" for d in pareto)
        parts.append(
            f'<polyline points="{poly}" fill="none" stroke="#1f9d55" '
            f'stroke-width="1.6" stroke-dasharray="5 3"/>'
        )

    cost_opt = sens.cost_optimal
    for d in sens.designs:
        cx, cy = px(d.cost), py(d.co2_t)
        color = "#1f9d55" if d.is_pareto else "#c44e52"
        r = 7 if d is cost_opt else 5
        parts.append(
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r}" fill="{color}" '
            f'fill-opacity="0.85" stroke="#222" stroke-width="0.8"/>'
        )
        parts.append(
            f'<text x="{cx:.1f}" y="{cy - 9:.1f}" text-anchor="middle" '
            f'font-size="10" fill="#222">{d.n_dc}</text>'
        )
    # mark the cost optimum
    parts.append(
        f'<text x="{px(cost_opt.cost):.1f}" y="{py(cost_opt.co2_t) + 18:.1f}" '
        f'text-anchor="middle" font-size="9" fill="#0a4a8b">cost-opt</text>'
    )

    # legend
    ly = mt + 6
    parts.append(
        f'<circle cx="{ml + pw - 150}" cy="{ly}" r="5" fill="#1f9d55"/>'
        f'<text x="{ml + pw - 140}" y="{ly + 4}" font-size="10" fill="#333">'
        f'Pareto-optimal</text>'
    )
    parts.append(
        f'<circle cx="{ml + pw - 150}" cy="{ly + 16}" r="5" fill="#c44e52"/>'
        f'<text x="{ml + pw - 140}" y="{ly + 20}" font-size="10" fill="#333">'
        f'dominated</text>'
    )
    parts.append(
        '<text x="'
        f'{ml}" y="{h - 6}" font-size="9" fill="#999">Point label = number of '
        'open DCs. Synthetic, seeded data; CO2 factor illustrative.</text>'
    )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"
