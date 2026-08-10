"""Demand-growth capacity planning: expansion triggers and network headroom.

The rest of the package optimizes the network for *today's* demand. But a
network is committed for years while demand moves, so the question a capacity
planner actually asks is: **how much growth can the committed network absorb,
and at what demand level does the next DC start to pay?** This module answers
it by sweeping a uniform demand-growth multiplier and, at every level,
re-solving the *existing* capacitated facility-location MILP twice:

  1. RE-OPTIMIZED. The unconstrained MILP on the scaled demand -- the network
     you would build if you could redesign freely at that demand level. Where
     its optimal open-DC count steps up, that growth level is an **expansion
     trigger**: the point where the next DC starts to pay.

  2. COMMITTED (frozen). The same MILP with today's cost-optimal DCs pinned
     open and every other candidate pinned closed (the ``force_open`` /
     ``force_closed`` pins the resilience module already uses), so only the
     assignment re-optimizes. The gap to the re-optimized cost is the premium
     for *not* redesigning; the growth level where the frozen network becomes
     infeasible is its **capacity wall** -- known exactly, as committed
     capacity divided by base demand.

Two exact anchors need no solver at all and cross-check the sweep: the
committed wall (committed capacity / base demand) and the physical ceiling of
any k-DC design (the k largest candidate capacities / base demand). Expansion
triggers are *economic* (where the optimal count steps up) and can only come at
or before the corresponding physical ceiling forces them.

IMPORTANT -- honesty. Growth is modeled as UNIFORM: every customer zone scales
by the same factor, so the demand mix and geography never change (real growth
is lumpy and shifts the map). Demand is deterministic, DC capacity is the only
hard operating limit (as everywhere in this package), and fixed costs and
transport rates are held flat as volumes grow -- no economies of scale, no
inflation. The plant echelon is NOT re-solved in this siting sweep; its total
capacity is reported as a separate ceiling because it can bind before the DC
candidate pool does. All figures are model-based estimates on synthetic data.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

from supplynet.data import NetworkData
from supplynet.facility import FacilitySolution, solve_facility_milp

# Demand multipliers swept, ascending. 1.00 is today's demand (the base-case
# collapse); 5% steps to double demand keep every expansion trigger visible.
GROWTH_GRID: tuple[float, ...] = (
    1.00, 1.05, 1.10, 1.15, 1.20, 1.25, 1.30, 1.35, 1.40, 1.45, 1.50,
    1.55, 1.60, 1.65, 1.70, 1.75, 1.80, 1.85, 1.90, 1.95, 2.00,
)


@dataclass
class GrowthPoint:
    """One swept demand level: the re-optimized design and the frozen network."""

    growth: float  # demand multiplier vs the base instance (1.0 = today)
    demand_units: float  # total scaled demand
    # -- re-optimized (unconstrained MILP at this demand level) --
    n_opened: int
    opened: list[str]
    cost: float  # fixed + outbound transport ($)
    fixed_cost: float
    transport_cost: float
    cost_per_unit: float  # cost / demand_units
    utilization_pct: float  # 100 * demand / capacity of the opened DCs
    reconfigured: bool  # opened set differs from the base design
    expanded: bool  # opens MORE DCs than the base design
    # -- committed (today's opened DCs pinned; only the assignment re-optimizes) --
    committed_feasible: bool
    committed_cost: float = 0.0  # 0.0 when infeasible (past the capacity wall)
    # Premium for freezing the network at this demand level: committed cost minus
    # re-optimized cost. Clamped at zero -- a frozen network can never beat the
    # re-optimized optimum, so any tiny negative is solver tolerance.
    committed_premium: float = 0.0


@dataclass
class GrowthPlan:
    base_opened: list[str]
    base_n_opened: int
    base_demand: float
    base_cost: float
    committed_capacity: float  # total capacity of today's opened DCs
    committed_wall_growth: float  # exact: committed_capacity / base_demand
    committed_headroom_pct: float  # 100 * (wall - 1): growth the frozen network absorbs
    dc_portfolio_capacity: float  # total capacity of ALL candidate DCs
    dc_ceiling_growth: float  # exact: dc_portfolio_capacity / base_demand
    # Physical ceiling of ANY design that keeps today's DC count: the base_n
    # largest candidate capacities / base demand. Growth past this level forces
    # an expansion no matter how the DCs are shuffled.
    base_count_ceiling_growth: float
    plant_capacity: float  # total plant capacity (NOT re-solved in this sweep)
    plant_ceiling_growth: float  # exact: plant_capacity / base_demand
    points: list[GrowthPoint] = field(default_factory=list)

    @property
    def first_reconfig(self) -> GrowthPoint | None:
        """The first swept level where the optimal design differs from today's."""
        return next((p for p in self.points if p.reconfigured), None)

    @property
    def first_expansion(self) -> GrowthPoint | None:
        """The first swept level where the optimal design opens MORE DCs."""
        return next((p for p in self.points if p.expanded), None)

    @property
    def expansion_triggers(self) -> list[GrowthPoint]:
        """Points where the optimal open-DC count steps up vs the previous level."""
        triggers: list[GrowthPoint] = []
        prev_n = self.base_n_opened
        for p in self.points:
            if p.n_opened > prev_n:
                triggers.append(p)
            prev_n = p.n_opened
        return triggers

    @property
    def max_committed_premium(self) -> float:
        prem = [p.committed_premium for p in self.points if p.committed_feasible]
        return max(prem) if prem else 0.0

    @property
    def last_committed_feasible(self) -> GrowthPoint | None:
        feas = [p for p in self.points if p.committed_feasible]
        return feas[-1] if feas else None


def scale_demand(data: NetworkData, growth: float) -> NetworkData:
    """Return a copy of the network with every zone's demand scaled by ``growth``.

    Uniform growth: ``demand_mean`` and ``demand_std`` both scale by the same
    factor, so each zone's coefficient of variation -- and the demand mix across
    zones -- is unchanged. Locations, costs, capacities and lead times are shared
    with the original instance, not copied.
    """
    if growth <= 0:
        raise ValueError(f"growth multiplier must be positive, got {growth}")
    cust = data.customers.copy()
    cust["demand_mean"] = cust["demand_mean"] * growth
    cust["demand_std"] = cust["demand_std"] * growth
    return NetworkData(
        plants=data.plants,
        dcs=data.dcs,
        customers=cust,
        plant_dc_cost=data.plant_dc_cost,
        dc_cust_cost=data.dc_cust_cost,
        seed=data.seed,
    )


def best_k_capacity(data: NetworkData, k: int) -> float:
    """Total capacity of the ``k`` largest candidate DCs -- the physical ceiling
    of any k-DC design, independent of cost."""
    caps = sorted(data.dcs["capacity"].to_numpy(), reverse=True)
    return float(sum(caps[:k]))


def run_growth_plan(
    data: NetworkData,
    base: FacilitySolution | None = None,
    growth_grid: Sequence[float] = GROWTH_GRID,
) -> GrowthPlan:
    """Sweep demand growth and trace the re-optimized vs frozen network.

    ``base`` is the committed cost-optimal facility solution being grown out of;
    when ``None`` it is solved here. At each swept multiplier the demand is
    scaled uniformly and the existing facility MILP is re-solved unconstrained
    (the expansion view) and with the committed DCs pinned (the frozen view).
    Levels beyond the whole candidate pool's capacity are infeasible even
    re-optimized; the sweep stops there (the exact ceiling is reported).
    """
    if base is None:
        base = solve_facility_milp(data)

    dc_ids = list(data.dcs["dc_id"])
    capacity = data.dcs["capacity"].to_numpy()
    base_idx = [dc_ids.index(dc) for dc in base.opened]
    base_set = set(base.opened)
    others = set(range(data.n_dcs)) - set(base_idx)

    base_demand = data.total_demand
    committed_capacity = float(sum(capacity[i] for i in base_idx))
    dc_portfolio_capacity = float(capacity.sum())
    plant_capacity = float(data.plants["capacity"].sum())

    points: list[GrowthPoint] = []
    for growth in sorted(set(growth_grid)):
        scaled = scale_demand(data, growth)
        try:
            sol = solve_facility_milp(scaled)
        except RuntimeError:
            # Beyond the whole candidate pool's capacity: every later (larger)
            # level is infeasible too, so the sweep ends here.
            break

        opened_cap = float(sum(capacity[dc_ids.index(dc)] for dc in sol.opened))
        try:
            frozen = solve_facility_milp(
                scaled, force_open=set(base_idx), force_closed=others
            )
            committed_feasible = True
            committed_cost = frozen.total_cost
            committed_premium = max(0.0, frozen.total_cost - sol.total_cost)
        except RuntimeError:
            # Past the committed capacity wall: the frozen network cannot serve.
            committed_feasible = False
            committed_cost = 0.0
            committed_premium = 0.0

        points.append(
            GrowthPoint(
                growth=growth,
                demand_units=scaled.total_demand,
                n_opened=sol.n_opened,
                opened=list(sol.opened),
                cost=sol.total_cost,
                fixed_cost=sol.fixed_cost,
                transport_cost=sol.transport_cost,
                cost_per_unit=sol.total_cost / scaled.total_demand,
                utilization_pct=100.0 * scaled.total_demand / opened_cap,
                reconfigured=set(sol.opened) != base_set,
                expanded=sol.n_opened > base.n_opened,
                committed_feasible=committed_feasible,
                committed_cost=committed_cost,
                committed_premium=committed_premium,
            )
        )

    return GrowthPlan(
        base_opened=list(base.opened),
        base_n_opened=base.n_opened,
        base_demand=base_demand,
        base_cost=base.total_cost,
        committed_capacity=committed_capacity,
        committed_wall_growth=committed_capacity / base_demand,
        committed_headroom_pct=100.0 * (committed_capacity / base_demand - 1.0),
        dc_portfolio_capacity=dc_portfolio_capacity,
        dc_ceiling_growth=dc_portfolio_capacity / base_demand,
        base_count_ceiling_growth=best_k_capacity(data, base.n_opened) / base_demand,
        plant_capacity=plant_capacity,
        plant_ceiling_growth=plant_capacity / base_demand,
        points=points,
    )


def growth_readout(plan: GrowthPlan) -> list[str]:
    """Plain-language, honest read of the growth plan. Returns lines."""
    lines: list[str] = []
    lines.append(
        f"The committed network ({', '.join(plan.base_opened)}; capacity "
        f"{plan.committed_capacity:,.0f} units) has {plan.committed_headroom_pct:+.1f}% "
        f"demand-growth headroom: at {plan.committed_wall_growth:.3f}x today's "
        f"{plan.base_demand:,.0f} units it is physically full and must change."
    )

    if plan.max_committed_premium < 1.0:
        lines.append(
            "Up to that wall, freezing today's network costs nothing extra -- the "
            "committed design stays cost-optimal, so capacity (not economics) "
            "forces the first change."
        )
    else:
        lines.append(
            f"Freezing today's network while demand grows costs up to "
            f"${plan.max_committed_premium:,.0f} per period versus re-optimizing, "
            "before the capacity wall makes freezing infeasible."
        )

    rec = plan.first_reconfig
    exp = plan.first_expansion
    if rec is not None and (exp is None or rec.growth < exp.growth):
        added = sorted(set(rec.opened) - set(plan.base_opened))
        removed = sorted(set(plan.base_opened) - set(rec.opened))
        if added and removed and rec.n_opened == plan.base_n_opened:
            lines.append(
                f"The cheapest first response to growth (at {rec.growth:.2f}x) is a "
                f"reshuffle, not a new DC: swap {', '.join(removed)} out for "
                f"{', '.join(added)} and stay at {rec.n_opened} DCs."
            )

    if exp is not None:
        # Any design keeping today's DC count physically caps out at this level;
        # a trigger below it is economic, at it the trigger is forced by capacity.
        ceiling = plan.base_count_ceiling_growth
        forced = "with" if exp.growth >= ceiling else "before"
        lines.append(
            f"Expansion trigger: DC #{exp.n_opened} first pays at {exp.growth:.2f}x "
            f"demand ({exp.demand_units:,.0f} units) -- {forced} the physical "
            f"ceiling of any {plan.base_n_opened}-DC design ({ceiling:.2f}x). The "
            f"optimal design becomes {', '.join(exp.opened)}."
        )
    if plan.points:
        top = plan.points[-1]
        lines.append(
            f"By {top.growth:.2f}x demand the optimal design opens {top.n_opened} "
            f"DCs at ${top.cost:,.0f} (${top.cost_per_unit:.2f}/unit vs "
            f"${plan.base_cost / plan.base_demand:.2f}/unit today)."
        )
    lines.append(
        f"Ceilings: the full candidate DC pool caps at "
        f"{plan.dc_ceiling_growth:.2f}x today's demand "
        f"({plan.dc_portfolio_capacity:,.0f} units); the plant echelon (NOT "
        f"re-solved in this siting sweep) totals {plan.plant_ceiling_growth:.2f}x "
        "and can bind first -- a real expansion plan must widen both echelons."
    )
    lines.append(
        "Growth is modeled as UNIFORM (every zone scales alike, mix unchanged) "
        "with deterministic demand, flat costs and rates (no scale economies), "
        "and DC capacity as the only hard limit -- planning estimates on "
        "synthetic data, not forecasts."
    )
    return lines


def to_csv(plan: GrowthPlan) -> str:
    """Serialize the growth sweep to a deterministic CSV string (no wall-clock)."""
    header = (
        "# Supply-network demand-growth expansion plan (synthetic data). Growth is "
        "UNIFORM (every zone scales by the same factor); demand deterministic; "
        "fixed costs and rates held flat. Committed columns are blank past the "
        f"committed capacity wall ({plan.committed_wall_growth:.4f}x demand).\n"
    )
    cols = (
        "growth,demand_units,n_dcs_optimal,opened,cost_usd,cost_per_unit_usd,"
        "capacity_utilization_pct,reconfigured,expanded,committed_feasible,"
        "committed_cost_usd,committed_premium_usd"
    )
    lines = [header.rstrip("\n"), cols]
    for p in plan.points:
        committed_cost = f"{p.committed_cost:.2f}" if p.committed_feasible else ""
        committed_prem = f"{p.committed_premium:.2f}" if p.committed_feasible else ""
        lines.append(
            f"{p.growth:.2f},{p.demand_units:.1f},{p.n_opened},"
            f"{'|'.join(p.opened)},{p.cost:.2f},{p.cost_per_unit:.4f},"
            f"{p.utilization_pct:.2f},{'yes' if p.reconfigured else 'no'},"
            f"{'yes' if p.expanded else 'no'},"
            f"{'yes' if p.committed_feasible else 'no'},"
            f"{committed_cost},{committed_prem}"
        )
    return "\n".join(lines) + "\n"


def to_svg(plan: GrowthPlan) -> str:
    """Hand-drawn, deterministic SVG of cost vs demand growth.

    Re-optimized total cost across the sweep (open-DC count labelled where it
    steps up), the frozen committed network's cost until its capacity wall, and
    the wall itself as a vertical line. Plain XML from the numbers -- no plotting
    library, no RNG, no timestamp -- so the committed file is byte-stable.
    """
    w, h = 640, 420
    ml, mr, mt, mb = 84, 24, 48, 60  # margins
    pw, ph = w - ml - mr, h - mt - mb

    pts = plan.points
    gmin = pts[0].growth
    gmax = pts[-1].growth
    grange = gmax - gmin or 1.0
    cmax = max(p.cost for p in pts) or 1.0

    def px(g: float) -> float:
        return ml + pw * (g - gmin) / grange

    def py(cost: float) -> float:
        return mt + ph * (1.0 - cost / cmax)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}" font-family="Segoe UI, Arial, sans-serif">',
        f'<rect width="{w}" height="{h}" fill="#ffffff"/>',
        f'<text x="{w / 2:.0f}" y="24" text-anchor="middle" font-size="16" '
        f'font-weight="bold" fill="#1a1a1a">Demand growth: expansion triggers and '
        f'the committed capacity wall (synthetic)</text>',
        # axes
        f'<line x1="{ml}" y1="{mt}" x2="{ml}" y2="{mt + ph}" stroke="#333" '
        f'stroke-width="1.2"/>',
        f'<line x1="{ml}" y1="{mt + ph}" x2="{ml + pw}" y2="{mt + ph}" '
        f'stroke="#333" stroke-width="1.2"/>',
        f'<text x="{ml + pw / 2:.0f}" y="{h - 20}" text-anchor="middle" '
        f'font-size="12" fill="#333">Demand growth (x today) -&gt;</text>',
        f'<text x="18" y="{mt + ph / 2:.0f}" text-anchor="middle" font-size="12" '
        f'fill="#333" transform="rotate(-90 18 {mt + ph / 2:.0f})">'
        f'Total network cost ($, fixed + outbound transport)</text>',
        # axis end labels
        f'<text x="{ml}" y="{mt + ph + 16}" text-anchor="start" font-size="10" '
        f'fill="#666">{gmin:.2f}x</text>',
        f'<text x="{ml + pw}" y="{mt + ph + 16}" text-anchor="end" font-size="10" '
        f'fill="#666">{gmax:.2f}x</text>',
        f'<text x="{ml - 6}" y="{mt + 8:.0f}" text-anchor="end" font-size="10" '
        f'fill="#666">${cmax:,.0f}</text>',
        f'<text x="{ml - 6}" y="{mt + ph:.0f}" text-anchor="end" font-size="10" '
        f'fill="#666">$0</text>',
    ]

    # Committed capacity wall (vertical line), if it falls inside the swept range.
    wall = plan.committed_wall_growth
    if gmin <= wall <= gmax:
        wx = px(wall)
        parts.append(
            f'<line x1="{wx:.1f}" y1="{mt}" x2="{wx:.1f}" y2="{mt + ph}" '
            f'stroke="#c44e52" stroke-width="1.2" stroke-dasharray="2 3"/>'
        )
        parts.append(
            f'<text x="{wx + 4:.1f}" y="{mt + ph - 8:.1f}" font-size="9" '
            f'fill="#c44e52">committed wall {wall:.3f}x '
            f'({plan.committed_headroom_pct:+.1f}%)</text>'
        )

    # Frozen committed network cost, while feasible.
    committed = [p for p in pts if p.committed_feasible]
    if committed:
        com_poly = " ".join(
            f"{px(p.growth):.1f},{py(p.committed_cost):.1f}" for p in committed
        )
        parts.append(
            f'<polyline points="{com_poly}" fill="none" stroke="#c44e52" '
            f'stroke-width="1.8" stroke-dasharray="5 3"/>'
        )
        for p in committed:
            parts.append(
                f'<circle cx="{px(p.growth):.1f}" cy="{py(p.committed_cost):.1f}" '
                f'r="3.2" fill="#c44e52"/>'
            )

    # Re-optimized cost curve with the open-DC count labelled where it steps up.
    opt_poly = " ".join(f"{px(p.growth):.1f},{py(p.cost):.1f}" for p in pts)
    parts.append(
        f'<polyline points="{opt_poly}" fill="none" stroke="#1f9d55" '
        f'stroke-width="2.0"/>'
    )
    triggers = {id(p) for p in plan.expansion_triggers}
    for p in pts:
        is_trigger = id(p) in triggers
        r = 4.5 if is_trigger or p is pts[0] else 2.6
        parts.append(
            f'<circle cx="{px(p.growth):.1f}" cy="{py(p.cost):.1f}" r="{r}" '
            f'fill="#1f9d55" stroke="#222" stroke-width="0.7"/>'
        )
        if is_trigger or p is pts[0]:
            parts.append(
                f'<text x="{px(p.growth):.1f}" y="{py(p.cost) - 9:.1f}" '
                f'text-anchor="middle" font-size="9" fill="#222">'
                f'{p.n_opened} DCs</text>'
            )

    # legend
    lx, ly = ml + 12, mt + 6
    parts.append(
        f'<line x1="{lx}" y1="{ly}" x2="{lx + 22}" y2="{ly}" stroke="#1f9d55" '
        f'stroke-width="2.0"/>'
        f'<text x="{lx + 28}" y="{ly + 4}" font-size="10" fill="#333">'
        f'Re-optimized at each demand level (label = DCs opened)</text>'
    )
    parts.append(
        f'<line x1="{lx}" y1="{ly + 16}" x2="{lx + 22}" y2="{ly + 16}" '
        f'stroke="#c44e52" stroke-width="1.8" stroke-dasharray="5 3"/>'
        f'<text x="{lx + 28}" y="{ly + 20}" font-size="10" fill="#333">'
        f'Committed network frozen (until its capacity wall)</text>'
    )
    parts.append(
        f'<text x="{ml}" y="{h - 6}" font-size="9" fill="#999">Uniform growth, '
        f'deterministic demand, flat costs; DC capacity is the only hard limit. '
        f'Synthetic, seeded data.</text>'
    )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"
