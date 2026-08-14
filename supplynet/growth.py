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
    """Hand-drawn, deterministic SVG of the growth plan as two stacked panels.

    Atlas plate 08. The hero panel on top is the expansion staircase -- the
    optimal open-DC count stepping up as demand grows, with each trigger
    labelled and the committed capacity wall marked. Beneath it, on the same
    growth axis, the re-optimized cost sweep against the frozen committed
    network. Two stacked panels, never a dual axis. Plain XML from the numbers
    -- no plotting library, no RNG, no timestamp -- so the committed file is
    byte-stable.
    """
    from supplynet import atlas

    w, h = 720, 640
    ml, mr = 92, 28
    pw = w - ml - mr
    # Panel frames: staircase (hero) on top, cost sweep beneath.
    t1_y, mt1, pb1 = 100, 116, 288
    t2_y, mt2, pb2 = 330, 346, 540

    pts = plan.points
    gmin = pts[0].growth
    gmax = pts[-1].growth
    grange = gmax - gmin or 1.0

    def px(g: float) -> float:
        return ml + pw * (g - gmin) / grange

    counts = [p.n_opened for p in pts]
    nlo = min(counts) - 0.5
    nhi = max(counts) + 0.8
    cmax = max(p.cost for p in pts) or 1.0
    chi = cmax * 1.06

    def py_n(n: float) -> float:
        return mt1 + (pb1 - mt1) * (1.0 - (n - nlo) / (nhi - nlo))

    def py_c(cost: float) -> float:
        return mt2 + (pb2 - mt2) * (1.0 - cost / chi)

    parts = atlas.svg_open(w, h)
    atlas.svg_header(parts, w, 8, "Demand growth: expansion triggers and the "
                     "committed capacity wall (synthetic)")

    # ---- Panel 1 (hero): the expansion staircase --------------------------
    parts.append(
        f'<text x="{ml}" y="{t1_y}" font-size="11" font-weight="bold" '
        f'fill="{atlas.INK}">The expansion staircase (MILP re-solved per level)'
        f'</text>'
    )
    atlas.svg_grid_y(parts, [float(n) for n in sorted(set(counts))], py_n,
                     ml, ml + pw, lambda t: f"{t:.0f}")
    parts.append(
        f'<text x="30" y="{(mt1 + pb1) / 2:.0f}" text-anchor="middle" '
        f'font-size="10.5" fill="{atlas.INK2}" '
        f'transform="rotate(-90 30 {(mt1 + pb1) / 2:.0f})">'
        f'Optimal number of open DCs</text>'
    )
    atlas.svg_baseline(parts, ml, ml + pw, pb1)

    # The committed capacity wall goes down first, under the data marks; its
    # panel-2 twin is drawn with that panel's chrome below.
    wall = plan.committed_wall_growth
    wall_shown = gmin <= wall <= gmax
    if wall_shown:
        wx = px(wall)
        parts.append(
            f'<line x1="{wx:.1f}" y1="{mt1}" x2="{wx:.1f}" y2="{pb1}" '
            f'stroke="{atlas.CRITICAL}" stroke-width="1.2" '
            f'stroke-dasharray="2 3"/>'
        )
        parts.append(
            f'<text x="{wx + 5:.1f}" y="{mt1 + 12:.1f}" font-size="9" '
            f'fill="{atlas.CRITICAL}">committed wall {wall:.3f}x '
            f'({plan.committed_headroom_pct:+.1f}%)</text>'
        )

    # Step path (post-step: the count holds until the next level).
    step_pts = [(px(pts[0].growth), py_n(pts[0].n_opened))]
    for prev, nxt in zip(pts, pts[1:], strict=False):
        step_pts.append((px(nxt.growth), py_n(prev.n_opened)))
        step_pts.append((px(nxt.growth), py_n(nxt.n_opened)))
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in step_pts)
    parts.append(
        f'<polyline points="{poly}" fill="none" stroke="{atlas.STEEL}" '
        f'stroke-width="2.6" stroke-linejoin="round"/>'
    )

    # Start label + one designed label per expansion trigger.
    parts.append(
        f'<circle cx="{px(pts[0].growth):.1f}" cy="{py_n(pts[0].n_opened):.1f}" '
        f'r="4.5" fill="{atlas.STEEL}" stroke="{atlas.PAPER}" stroke-width="1.8"/>'
    )
    parts.append(
        f'<text x="{px(pts[0].growth) + 7:.1f}" '
        f'y="{py_n(pts[0].n_opened) - 9:.1f}" font-size="9.5" '
        f'fill="{atlas.INK2}">{pts[0].n_opened} DCs</text>'
    )
    # Attention amber marks each trigger; the label beside it names the DC and
    # the level, so the color never carries the meaning alone. (Kraft, the only
    # hue amber sits close to, never appears on this plate.)
    for p in plan.expansion_triggers:
        x, y = px(p.growth), py_n(p.n_opened)
        parts.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="{atlas.AMBER}" '
            f'stroke="{atlas.PAPER}" stroke-width="1.8"/>'
        )
        anchor, dx = ("start", 8) if x < ml + pw * 0.78 else ("end", -8)
        parts.append(
            f'<text x="{x + dx:.1f}" y="{y + 17:.1f}" text-anchor="{anchor}" '
            f'font-size="9.5" fill="{atlas.INK2}">DC #{p.n_opened} pays at '
            f'{p.growth:.2f}x</text>'
        )

    # ---- Panel 2: cost of growth, redesign vs frozen ----------------------
    parts.append(
        f'<text x="{ml}" y="{t2_y}" font-size="11" font-weight="bold" '
        f'fill="{atlas.INK}">Cost of growth: redesign vs frozen network</text>'
    )
    atlas.svg_grid_y(parts, atlas.nice_ticks(0.0, chi, 5), py_c, ml, ml + pw,
                     atlas.money)
    parts.append(
        f'<text x="30" y="{(mt2 + pb2) / 2:.0f}" text-anchor="middle" '
        f'font-size="10.5" fill="{atlas.INK2}" '
        f'transform="rotate(-90 30 {(mt2 + pb2) / 2:.0f})">'
        f'Total network cost ($, fixed + outbound transport)</text>'
    )
    atlas.svg_baseline(parts, ml, ml + pw, pb2)
    atlas.svg_ticks_x(parts, atlas.nice_ticks(gmin, gmax, 6), px, pb2,
                      lambda t: f"{t:.1f}x")
    parts.append(
        f'<text x="{ml + pw / 2:.0f}" y="{pb2 + 36}" text-anchor="middle" '
        f'font-size="10.5" fill="{atlas.INK2}">Demand growth (x today) '
        f'-&gt;</text>'
    )

    # Panel-2 twin of the capacity wall, under the data marks.
    if wall_shown:
        parts.append(
            f'<line x1="{px(wall):.1f}" y1="{mt2}" x2="{px(wall):.1f}" '
            f'y2="{pb2}" stroke="{atlas.CRITICAL}" stroke-width="1.2" '
            f'stroke-dasharray="2 3"/>'
        )

    # Frozen committed network cost (dashed concrete), while feasible.
    committed = [p for p in pts if p.committed_feasible]
    if committed:
        com_poly = " ".join(
            f"{px(p.growth):.1f},{py_c(p.committed_cost):.1f}" for p in committed
        )
        parts.append(
            f'<polyline points="{com_poly}" fill="none" '
            f'stroke="{atlas.CONCRETE}" stroke-width="1.8" '
            f'stroke-dasharray="5 3"/>'
        )

    # Re-optimized cost curve (steel), triggers emphasized in attention amber.
    opt_poly = " ".join(f"{px(p.growth):.1f},{py_c(p.cost):.1f}" for p in pts)
    parts.append(
        f'<polyline points="{opt_poly}" fill="none" stroke="{atlas.STEEL}" '
        f'stroke-width="2.2"/>'
    )
    triggers = {id(p) for p in plan.expansion_triggers}
    for p in pts:
        is_trigger = id(p) in triggers
        r = 4.2 if is_trigger or p is pts[0] else 2.6
        parts.append(
            f'<circle cx="{px(p.growth):.1f}" cy="{py_c(p.cost):.1f}" r="{r}" '
            f'fill="{atlas.AMBER if is_trigger else atlas.STEEL}" '
            f'stroke="{atlas.PAPER}" stroke-width="1.4"/>'
        )

    # Up to the wall the frozen cost coincides with the re-optimized cost, so
    # the frozen series rides ON the steel line: hollow concrete rings on top
    # keep it visible exactly where the two series overlap.
    for p in committed:
        parts.append(
            f'<circle cx="{px(p.growth):.1f}" '
            f'cy="{py_c(p.committed_cost):.1f}" r="5.2" fill="none" '
            f'stroke="{atlas.CONCRETE}" stroke-width="1.6"/>'
        )

    # legend (upper left of the cost panel, clear of the wall line)
    lx, ly = ml + 96, mt2 + 14
    parts.append(
        f'<line x1="{lx}" y1="{ly}" x2="{lx + 22}" y2="{ly}" '
        f'stroke="{atlas.STEEL}" stroke-width="2.2"/>'
        f'<text x="{lx + 28}" y="{ly + 4}" font-size="10" fill="{atlas.INK2}">'
        f'Re-optimized at each demand level</text>'
    )
    parts.append(
        f'<line x1="{lx}" y1="{ly + 18}" x2="{lx + 22}" y2="{ly + 18}" '
        f'stroke="{atlas.CONCRETE}" stroke-width="1.8" '
        f'stroke-dasharray="5 3"/>'
        f'<text x="{lx + 28}" y="{ly + 22}" font-size="10" fill="{atlas.INK2}">'
        f'Committed network frozen (until its capacity wall)</text>'
    )

    atlas.svg_footer(parts, w, h, [
        "Uniform growth, deterministic demand, flat costs; DC capacity is the "
        "only hard limit. Synthetic, seeded data."
    ])
    return atlas.svg_close(parts)
