"""Phased build plan: WHEN to open each DC, and what the timing is worth (NPV).

:mod:`supplynet.growth` answers *at what demand level* the next DC starts to pay.
It stops one step short of the question a board actually votes on: **in which
year do we open it, and what does it cost us to open early -- or to refuse to
close anything?** This module puts a calendar and a discount rate on the growth
staircase and prices four build policies over one horizon.

Every year ``t`` carries demand ``(1 + g)^t`` times today's, and each policy is
priced by re-solving the *existing* capacitated facility MILP at that demand
with the policy's continuity constraint expressed through the ``force_open`` /
``force_closed`` pins the resilience and growth modules already use:

  1. FREE REDESIGN -- unconstrained each year. It may close a site it opened
     last year, which no real network does cheaply, so it is a **lower bound**,
     not a plan.
  2. STAGED BUILD -- the plan. Each year the MILP is re-solved with everything
     already open pinned open and every other candidate free, so the network can
     only ever grow. A DC joins the year it first pays *or* the year capacity
     forces it, whichever comes first.
  3. BUILD AHEAD -- the staged plan's final design opened in year 0 and held.
  4. NO EXPANSION -- today's committed network held forever; infeasible from its
     capacity wall onward, and reported as failing rather than priced.

Because a policy's feasible set only ever shrinks down that list, three cost
orderings hold at **every** year by construction and are asserted in the tests:
free <= staged <= build-ahead, and free <= staged <= frozen wherever frozen is
feasible. One anchor needs no solver at all: the year today's network runs out
of capacity is the first ``t`` with ``(1 + g)^t`` past the growth module's
committed wall, and it must equal the year the frozen policy goes infeasible.

IMPORTANT -- honesty. Both timing inputs are ILLUSTRATIVE ASSUMPTIONS, not
forecasts: demand grows at a constant annual rate (uniformly across zones, as in
the growth module) and cash is discounted at a constant rate. Costs here are the
model's per-period fixed + outbound transport, charged at the START of each year
(year 0 undiscounted) -- an annuity-due convention. Critically, this package
models fixed cost as a RECURRING operating cost, not one-time capex, and a DC
opens instantly with no construction lead time. Under that cost structure
building ahead can never pay on cost alone; the premium it reports is the price
of readiness, and a model carrying capex, construction lead times or land
escalation could reverse its sign. Planning estimates on synthetic data.
"""

from dataclasses import dataclass, field

from supplynet.data import NetworkData
from supplynet.facility import FacilitySolution, solve_facility_milp
from supplynet.growth import scale_demand

# --- Timing assumptions (ILLUSTRATIVE -- labelled everywhere they surface) ---
DEFAULT_ANNUAL_GROWTH = 0.06  # constant demand growth, per year
DEFAULT_DISCOUNT_RATE = 0.10  # constant discount rate (cost of capital), per year
DEFAULT_HORIZON_YEARS = 10  # years 0..9, year 0 = today

# Policy keys in reporting order: loosest continuity constraint first.
POLICY_ORDER = ("free", "staged", "ahead", "frozen")
POLICY_LABELS = {
    "free": "Free redesign (may close an opened site)",
    "staged": "Staged build (open-only; never close)",
    "ahead": "Build ahead (final design opened in year 0)",
    "frozen": "No expansion (today's network held)",
}


@dataclass
class PhaseYear:
    """One year of one policy: the network it runs and what that year costs."""

    year: int
    growth: float  # (1 + annual_growth) ** year
    demand_units: float
    opened: list[str]
    added: list[str]  # DCs opened in THIS year (empty when nothing changes)
    # DCs CLOSED in this year. Always empty for the staged/build-ahead/frozen
    # policies, which cannot close a site; only the free-redesign lower bound
    # ever fills it, and that churn is exactly why it is not a plan.
    removed: list[str]
    n_opened: int
    cost: float  # that year's fixed + outbound transport ($)
    discount_factor: float  # (1 + discount_rate) ** -year
    present_value: float  # cost * discount_factor


@dataclass
class PhasePolicy:
    """One build policy priced across the whole horizon."""

    key: str
    label: str
    years: list[PhaseYear] = field(default_factory=list)
    feasible: bool = True  # servable in EVERY year of the horizon
    first_infeasible_year: int | None = None
    # NPV of the cost stream. Left at 0.0 for a policy that fails: a network
    # that cannot serve demand has no cost, it has a shortfall.
    npv: float = 0.0

    @property
    def final_opened(self) -> list[str]:
        return list(self.years[-1].opened) if self.years else []

    @property
    def build_events(self) -> list[PhaseYear]:
        """Years where this policy opened at least one new DC."""
        return [y for y in self.years if y.added]

    @property
    def change_events(self) -> list[PhaseYear]:
        """Years where the open set changed at all -- openings or closures."""
        return [y for y in self.years if y.added or y.removed]

    @property
    def n_closures(self) -> int:
        """How many site closures the policy needs across the horizon."""
        return sum(len(y.removed) for y in self.years)


@dataclass
class PhasePlan:
    annual_growth: float
    discount_rate: float
    horizon_years: int
    base_opened: list[str]
    base_demand: float
    committed_wall_growth: float  # exact: committed capacity / base demand
    # Exact, solver-free: the first year whose demand passes the committed wall.
    # None when the committed network survives the whole horizon.
    wall_year: int | None
    horizon_growth: float  # (1 + g) ** (horizon_years - 1)
    dc_ceiling_growth: float  # whole candidate pool / base demand
    plant_ceiling_growth: float  # plant echelon / base demand (NOT re-sited here)
    policies: dict[str, PhasePolicy] = field(default_factory=dict)

    @property
    def staged(self) -> PhasePolicy:
        return self.policies["staged"]

    @property
    def build_ahead_premium(self) -> float:
        """NPV cost of opening the final design today instead of staging it."""
        ahead, staged = self.policies["ahead"], self.policies["staged"]
        if not (ahead.feasible and staged.feasible):
            return 0.0
        return ahead.npv - staged.npv

    @property
    def build_ahead_premium_pct(self) -> float:
        staged = self.policies["staged"]
        if not staged.feasible or staged.npv <= 0:
            return 0.0
        return 100.0 * self.build_ahead_premium / staged.npv

    @property
    def continuity_premium(self) -> float:
        """NPV cost of never closing a site, vs the free-redesign lower bound."""
        staged, free = self.policies["staged"], self.policies["free"]
        if not (staged.feasible and free.feasible):
            return 0.0
        return staged.npv - free.npv

    @property
    def continuity_premium_pct(self) -> float:
        free = self.policies["free"]
        if not free.feasible or free.npv <= 0:
            return 0.0
        return 100.0 * self.continuity_premium / free.npv


def growth_at(annual_growth: float, year: int) -> float:
    """Demand multiplier in ``year`` under constant annual growth (year 0 = 1.0)."""
    return (1.0 + annual_growth) ** year


def discount_factor(discount_rate: float, year: int) -> float:
    """Present-value factor for ``year``; year 0 is undiscounted (annuity-due)."""
    return 1.0 / (1.0 + discount_rate) ** year


def wall_year_of(annual_growth: float, wall_growth: float, horizon_years: int) -> int | None:
    """First year whose demand passes ``wall_growth``, or None within the horizon.

    Exact and solver-free: demand in year ``t`` is ``(1 + g)^t`` times today's, so
    the committed network is full the first year that multiplier exceeds its wall.
    Cross-checks the year the frozen policy is observed to go infeasible.
    """
    if annual_growth <= 0:
        return None
    for t in range(horizon_years):
        if growth_at(annual_growth, t) > wall_growth:
            return t
    return None


def _price(
    data: NetworkData,
    growth: float,
    force_open: set[int] | None = None,
    force_closed: set[int] | None = None,
) -> FacilitySolution | None:
    """Cost the network at ``growth`` under the given pins; None if infeasible."""
    scaled = scale_demand(data, growth)
    try:
        return solve_facility_milp(
            scaled, force_open=force_open, force_closed=force_closed
        )
    except RuntimeError:
        return None


def _run_policy(
    key: str,
    data: NetworkData,
    dc_ids: list[str],
    base_idx: set[int],
    annual_growth: float,
    discount_rate: float,
    horizon_years: int,
    fixed_design: set[int] | None = None,
) -> PhasePolicy:
    """Price one policy year by year, stopping honestly at the first failure.

    ``key`` selects the continuity constraint: ``free`` pins nothing, ``staged``
    pins whatever is already open, and ``frozen``/``ahead`` pin ``fixed_design``
    open with every other candidate closed.
    """
    policy = PhasePolicy(key=key, label=POLICY_LABELS[key])
    all_idx = set(range(data.n_dcs))
    open_idx: set[int] = set(base_idx if key == "staged" else ())
    prev_open: set[int] = set(base_idx)

    for t in range(horizon_years):
        growth = growth_at(annual_growth, t)
        if key == "free":
            sol = _price(data, growth)
        elif key == "staged":
            sol = _price(data, growth, force_open=open_idx)
        else:  # "ahead" / "frozen": one design pinned open, the rest pinned shut
            design = fixed_design or set()
            sol = _price(data, growth, force_open=design,
                         force_closed=all_idx - design)

        if sol is None:
            policy.feasible = False
            policy.first_infeasible_year = t
            policy.npv = 0.0
            return policy

        now_open = {dc_ids.index(d) for d in sol.opened}
        if key == "staged":
            # force_open guarantees a superset: the network never closes a site.
            assert open_idx <= now_open, "staged build closed an opened DC"
            open_idx = now_open
        # Changes are always measured against the network in service last year;
        # in year 0 that is today's committed design, so a build-ahead plan
        # correctly reports its whole up-front build as a year-0 opening.
        added = sorted(now_open - prev_open)
        removed = sorted(prev_open - now_open)
        prev_open = now_open

        df = discount_factor(discount_rate, t)
        scaled_demand = data.total_demand * growth
        policy.years.append(
            PhaseYear(
                year=t,
                growth=growth,
                demand_units=scaled_demand,
                opened=sorted(sol.opened),
                added=[dc_ids[i] for i in added],
                removed=[dc_ids[i] for i in removed],
                n_opened=sol.n_opened,
                cost=sol.total_cost,
                discount_factor=df,
                present_value=sol.total_cost * df,
            )
        )

    policy.npv = sum(y.present_value for y in policy.years)
    return policy


def run_phase_plan(
    data: NetworkData,
    base: FacilitySolution | None = None,
    annual_growth: float = DEFAULT_ANNUAL_GROWTH,
    discount_rate: float = DEFAULT_DISCOUNT_RATE,
    horizon_years: int = DEFAULT_HORIZON_YEARS,
) -> PhasePlan:
    """Price four build policies over a dated horizon and return the plan.

    ``base`` is today's committed cost-optimal design; solved here when ``None``.
    The staged policy is run first because the build-ahead policy is *defined* as
    opening the staged plan's final design in year 0 -- the two differ only in
    timing, which is exactly the comparison the NPV is meant to price.
    """
    if annual_growth < 0:
        raise ValueError(f"annual growth must be >= 0, got {annual_growth}")
    if discount_rate < 0:
        raise ValueError(f"discount rate must be >= 0, got {discount_rate}")
    if horizon_years < 1:
        raise ValueError(f"horizon must be at least 1 year, got {horizon_years}")
    if base is None:
        base = solve_facility_milp(data)

    dc_ids = list(data.dcs["dc_id"])
    capacity = data.dcs["capacity"].to_numpy()
    base_idx = {dc_ids.index(d) for d in base.opened}
    committed_capacity = float(sum(capacity[i] for i in base_idx))
    wall_growth = committed_capacity / data.total_demand

    def run(key: str, fixed_design: set[int] | None = None) -> PhasePolicy:
        return _run_policy(
            key, data, dc_ids, base_idx, annual_growth, discount_rate,
            horizon_years, fixed_design=fixed_design,
        )

    staged = run("staged")
    final_design = {dc_ids.index(d) for d in staged.final_opened} or set(base_idx)
    policies = {
        "free": run("free"),
        "staged": staged,
        "ahead": run("ahead", fixed_design=final_design),
        "frozen": run("frozen", fixed_design=set(base_idx)),
    }

    return PhasePlan(
        annual_growth=annual_growth,
        discount_rate=discount_rate,
        horizon_years=horizon_years,
        base_opened=list(base.opened),
        base_demand=data.total_demand,
        committed_wall_growth=wall_growth,
        wall_year=wall_year_of(annual_growth, wall_growth, horizon_years),
        horizon_growth=growth_at(annual_growth, horizon_years - 1),
        dc_ceiling_growth=float(capacity.sum()) / data.total_demand,
        plant_ceiling_growth=float(data.plants["capacity"].sum()) / data.total_demand,
        policies=policies,
    )


def schedule_text(policy: PhasePolicy) -> str:
    """One-line change schedule, e.g. ``yr2 +DC0; yr5 +DC3 -DC6``.

    Closures are shown with a leading minus so the free-redesign lower bound
    cannot pass its churn off as a build plan.
    """
    events = policy.change_events
    if not events:
        return "no change in the horizon"
    out = []
    for y in events:
        bits = []
        if y.added:
            bits.append("+" + ", ".join(y.added))
        if y.removed:
            bits.append("-" + ", ".join(y.removed))
        out.append(f"yr{y.year} " + " ".join(bits))
    return "; ".join(out)


def phasing_readout(plan: PhasePlan) -> list[str]:
    """Plain-language, honest read of the phased build plan. Returns lines."""
    g_pct = 100.0 * plan.annual_growth
    r_pct = 100.0 * plan.discount_rate
    last_year = plan.horizon_years - 1
    staged = plan.policies["staged"]
    frozen = plan.policies["frozen"]
    ahead = plan.policies["ahead"]
    free = plan.policies["free"]
    lines: list[str] = []

    lines.append(
        f"Timing assumes demand grows {g_pct:.0f}%/yr and cash discounts at "
        f"{r_pct:.0f}%/yr over {plan.horizon_years} years -- both ILLUSTRATIVE "
        "ASSUMPTIONS, not forecasts. Year 0 is today and is charged "
        "undiscounted; year t is discounted by (1+r)^-t."
    )

    if plan.wall_year is not None:
        lines.append(
            f"Today's network ({', '.join(plan.base_opened)}) runs out of "
            f"capacity in year {plan.wall_year}: demand reaches "
            f"{growth_at(plan.annual_growth, plan.wall_year):.3f}x, past its "
            f"{plan.committed_wall_growth:.3f}x wall. Holding it unchanged is "
            "not a plan -- it is a shortfall, so it is reported as failing "
            "rather than priced."
        )
    else:
        lines.append(
            f"Today's network ({', '.join(plan.base_opened)}) clears the whole "
            f"{plan.horizon_years}-year horizon: at {g_pct:.0f}%/yr demand only "
            f"reaches {plan.horizon_growth:.3f}x against its "
            f"{plan.committed_wall_growth:.3f}x wall."
        )
        if frozen.feasible:
            lines.append(
                f"Held unchanged, it costs ${frozen.npv:,.0f} NPV over the horizon."
            )

    if staged.feasible:
        lines.append(
            f"Staged build schedule ({schedule_text(staged)}) reaches "
            f"{staged.years[-1].n_opened} DCs "
            f"({', '.join(staged.final_opened)}) by year {last_year}, at "
            f"${staged.npv:,.0f} NPV."
        )
    else:
        lines.append(
            f"Even opening every candidate DC, demand outruns the network in "
            f"year {staged.first_infeasible_year}: no build schedule serves this "
            "horizon."
        )

    if staged.feasible and ahead.feasible:
        lines.append(
            f"Opening that final design today instead of staging it costs "
            f"${ahead.npv:,.0f} NPV -- a build-ahead premium of "
            f"${plan.build_ahead_premium:,.0f} "
            f"({plan.build_ahead_premium_pct:+.1f}%). In THIS model fixed cost is "
            "a recurring operating cost and a DC opens instantly, so building "
            "ahead can never pay on cost alone: that premium is the price of "
            "readiness, not a mistake the model caught."
        )

    if staged.feasible and free.feasible:
        prem = plan.continuity_premium
        if prem >= 1.0:
            free_final = free.years[-1]
            lines.append(
                f"Never closing a site costs ${prem:,.0f} NPV "
                f"({plan.continuity_premium_pct:+.1f}%) versus a free redesign "
                f"that shuts {free.n_closures} site(s) across the horizon to "
                f"end on {free_final.n_opened} DCs "
                f"({', '.join(free_final.opened)}). That gap is the price of "
                "continuity, and the free line is a lower bound rather than a "
                "plan -- this model charges nothing to close a live DC, which no "
                "real network gets to do."
            )
        else:
            lines.append(
                "Continuity is free here: the staged build never has to keep a "
                "site the free redesign would have closed, so both land on the "
                "same NPV."
            )

    lines.append(
        f"Scope: only the DC echelon is re-sited. By year {last_year} demand is "
        f"{plan.horizon_growth:.2f}x today's, against a "
        f"{plan.dc_ceiling_growth:.2f}x candidate-pool ceiling and a "
        f"{plan.plant_ceiling_growth:.2f}x plant ceiling (plants are NOT "
        "re-solved here) -- a real programme has to widen both echelons."
    )
    lines.append(
        "Growth stays UNIFORM (every zone scales alike) with deterministic "
        "demand and flat costs and rates; there is no capex, no construction "
        "lead time and no land escalation in this model, any of which could "
        "change the early-vs-late answer. Planning estimates on synthetic data."
    )
    return lines


def to_csv(plan: PhasePlan) -> str:
    """Serialize the phased build plan to deterministic CSV (no wall-clock)."""
    header = (
        "# Supply-network phased build plan (synthetic data). Demand grows "
        f"{100.0 * plan.annual_growth:.1f}%/yr and cash discounts at "
        f"{100.0 * plan.discount_rate:.1f}%/yr -- both ILLUSTRATIVE ASSUMPTIONS, "
        "not forecasts. Year 0 is today, charged undiscounted. Cost is the "
        "model's per-period fixed + outbound transport: a RECURRING operating "
        "cost, not capex, and a DC opens with no construction lead time. Rows "
        "stop at a policy's first infeasible year, which is recorded with blank "
        "cost columns.\n"
    )
    cols = (
        "policy,policy_label,year,growth_x,demand_units,n_dcs,opened,added,"
        "closed,annual_cost_usd,discount_factor,present_value_usd,feasible"
    )
    lines = [header.rstrip("\n"), cols]
    for key in POLICY_ORDER:
        policy = plan.policies[key]
        for y in policy.years:
            lines.append(
                f"{key},{policy.label},{y.year},{y.growth:.4f},"
                f"{y.demand_units:.1f},{y.n_opened},{'|'.join(y.opened)},"
                f"{'|'.join(y.added)},{'|'.join(y.removed)},{y.cost:.2f},"
                f"{y.discount_factor:.6f},{y.present_value:.2f},yes"
            )
        if not policy.feasible:
            t = policy.first_infeasible_year
            growth = growth_at(plan.annual_growth, t)
            lines.append(
                f"{key},{policy.label},{t},{growth:.4f},"
                f"{plan.base_demand * growth:.1f},,,,,,,,no"
            )
    lines.append("")
    lines.append("# NPV summary (blank where a policy never completes the horizon)")
    lines.append(
        "policy,policy_label,npv_usd,feasible,first_infeasible_year,"
        "site_closures_required"
    )
    for key in POLICY_ORDER:
        policy = plan.policies[key]
        npv = f"{policy.npv:.2f}" if policy.feasible else ""
        bad = "" if policy.feasible else str(policy.first_infeasible_year)
        lines.append(
            f"{key},{policy.label},{npv},{'yes' if policy.feasible else 'no'},"
            f"{bad},{policy.n_closures}"
        )
    return "\n".join(lines) + "\n"


def to_svg(plan: PhasePlan) -> str:
    """Hand-drawn, deterministic SVG of the phased build plan (atlas plate 09).

    Two stacked panels on one shared year axis -- never a dual axis. The hero
    panel on top is the dated build staircase: the staged plan's open-DC count
    stepping up year by year, every opening marked in attention amber with the
    DC named, and the year today's network runs out of capacity marked in signal
    red. Beneath it, the present value of each year's network cost for the
    staged plan against the build-ahead plan, so the shape of the premium -- paid
    up front, converging late -- is visible rather than asserted. Plain XML from
    the numbers: no plotting library, no RNG, no timestamp, so the committed file
    is byte-stable.
    """
    from supplynet import atlas

    w, h = 720, 640
    ml, mr = 92, 28
    pw = w - ml - mr
    t1_y, mt1, pb1 = 100, 116, 288
    t2_y, mt2, pb2 = 330, 346, 540

    staged = plan.policies["staged"]
    ahead = plan.policies["ahead"]
    years = staged.years
    ymin = 0
    ymax = max(plan.horizon_years - 1, 1)

    def px(year: float) -> float:
        return ml + pw * (year - ymin) / (ymax - ymin)

    counts = [y.n_opened for y in years]
    nlo = min(counts) - 0.5
    nhi = max(counts) + 0.8

    def py_n(n: float) -> float:
        return mt1 + (pb1 - mt1) * (1.0 - (n - nlo) / (nhi - nlo))

    pv_series = [y.present_value for y in years]
    if ahead.feasible:
        pv_series = pv_series + [y.present_value for y in ahead.years]
    vhi = (max(pv_series) if pv_series else 1.0) * 1.10

    def py_v(v: float) -> float:
        return mt2 + (pb2 - mt2) * (1.0 - v / vhi)

    parts = atlas.svg_open(w, h)
    atlas.svg_header(
        parts, w,
        9,
        "Phased build plan: when each DC opens and what the timing is worth",
    )

    # ---- Panel 1 (hero): the dated build staircase -------------------------
    parts.append(
        f'<text x="{ml}" y="{t1_y}" font-size="11" font-weight="bold" '
        f'fill="{atlas.INK}">The build schedule (staged: open-only, MILP '
        f're-solved per year)</text>'
    )
    atlas.svg_grid_y(parts, [float(n) for n in sorted(set(counts))], py_n,
                     ml, ml + pw, lambda t: f"{t:.0f}")
    parts.append(
        f'<text x="30" y="{(mt1 + pb1) / 2:.0f}" text-anchor="middle" '
        f'font-size="10.5" fill="{atlas.INK2}" '
        f'transform="rotate(-90 30 {(mt1 + pb1) / 2:.0f})">'
        f'Open DCs in service</text>'
    )
    atlas.svg_baseline(parts, ml, ml + pw, pb1)

    # The year today's network is full goes down first, under the data marks.
    wall_year = plan.wall_year
    if wall_year is not None:
        wx = px(wall_year)
        for y0, y1 in ((mt1, pb1), (mt2, pb2)):
            parts.append(
                f'<line x1="{wx:.1f}" y1="{y0}" x2="{wx:.1f}" y2="{y1}" '
                f'stroke="{atlas.CRITICAL}" stroke-width="1.2" '
                f'stroke-dasharray="2 3"/>'
            )
        parts.append(
            f'<text x="{wx + 5:.1f}" y="{mt1 + 12:.1f}" font-size="9" '
            f'fill="{atlas.CRITICAL}">today\'s network full in year '
            f'{wall_year} ({plan.committed_wall_growth:.3f}x wall)</text>'
        )

    # Step path (post-step: the count holds until the next opening).
    step_pts = [(px(years[0].year), py_n(years[0].n_opened))]
    for prev, nxt in zip(years, years[1:], strict=False):
        step_pts.append((px(nxt.year), py_n(prev.n_opened)))
        step_pts.append((px(nxt.year), py_n(nxt.n_opened)))
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in step_pts)
    parts.append(
        f'<polyline points="{poly}" fill="none" stroke="{atlas.STEEL}" '
        f'stroke-width="2.6" stroke-linejoin="round"/>'
    )

    # Start label, then one amber, directly-labelled mark per opening year.
    parts.append(
        f'<circle cx="{px(years[0].year):.1f}" cy="{py_n(years[0].n_opened):.1f}" '
        f'r="4.5" fill="{atlas.STEEL}" stroke="{atlas.PAPER}" stroke-width="1.8"/>'
    )
    parts.append(
        f'<text x="{px(years[0].year) + 7:.1f}" '
        f'y="{py_n(years[0].n_opened) - 9:.1f}" font-size="9.5" '
        f'fill="{atlas.INK2}">{years[0].n_opened} DCs today</text>'
    )
    for y in staged.build_events:
        x, yy = px(y.year), py_n(y.n_opened)
        parts.append(
            f'<circle cx="{x:.1f}" cy="{yy:.1f}" r="5" fill="{atlas.AMBER}" '
            f'stroke="{atlas.PAPER}" stroke-width="1.8"/>'
        )
        anchor, dx = ("start", 8) if x < ml + pw * 0.72 else ("end", -8)
        parts.append(
            f'<text x="{x + dx:.1f}" y="{yy + 18:.1f}" text-anchor="{anchor}" '
            f'font-size="9.5" fill="{atlas.INK2}">year {y.year}: open '
            f'{", ".join(y.added)}</text>'
        )

    # ---- Panel 2: present value of each year's cost, staged vs build ahead --
    parts.append(
        f'<text x="{ml}" y="{t2_y}" font-size="11" font-weight="bold" '
        f'fill="{atlas.INK}">What the timing costs: present value per year</text>'
    )
    atlas.svg_grid_y(parts, atlas.nice_ticks(0.0, vhi, 5), py_v, ml, ml + pw,
                     atlas.money)
    parts.append(
        f'<text x="30" y="{(mt2 + pb2) / 2:.0f}" text-anchor="middle" '
        f'font-size="10.5" fill="{atlas.INK2}" '
        f'transform="rotate(-90 30 {(mt2 + pb2) / 2:.0f})">'
        f'Present value of that year\'s cost ($)</text>'
    )
    atlas.svg_baseline(parts, ml, ml + pw, pb2)
    atlas.svg_ticks_x(parts, [float(t) for t in range(plan.horizon_years)], px,
                      pb2, lambda t: f"{t:.0f}")
    parts.append(
        f'<text x="{ml + pw / 2:.0f}" y="{pb2 + 36}" text-anchor="middle" '
        f'font-size="10.5" fill="{atlas.INK2}">Year from today '
        f'(0 = today) -&gt;</text>'
    )

    # Build-ahead stream first, in de-emphasis concrete: it is the comparison,
    # not the plan.
    if ahead.feasible:
        ahead_poly = " ".join(
            f"{px(y.year):.1f},{py_v(y.present_value):.1f}" for y in ahead.years
        )
        parts.append(
            f'<polyline points="{ahead_poly}" fill="none" '
            f'stroke="{atlas.CONCRETE}" stroke-width="1.8" '
            f'stroke-dasharray="5 3"/>'
        )
        for y in ahead.years:
            parts.append(
                f'<circle cx="{px(y.year):.1f}" cy="{py_v(y.present_value):.1f}" '
                f'r="2.8" fill="{atlas.CONCRETE}" stroke="{atlas.PAPER}" '
                f'stroke-width="1.2"/>'
            )

    staged_poly = " ".join(
        f"{px(y.year):.1f},{py_v(y.present_value):.1f}" for y in years
    )
    parts.append(
        f'<polyline points="{staged_poly}" fill="none" stroke="{atlas.STEEL}" '
        f'stroke-width="2.2"/>'
    )
    build_years = {y.year for y in staged.build_events}
    for y in years:
        opening = y.year in build_years
        parts.append(
            f'<circle cx="{px(y.year):.1f}" cy="{py_v(y.present_value):.1f}" '
            f'r="{5.0 if opening else 2.8}" '
            f'fill="{atlas.AMBER if opening else atlas.STEEL}" '
            f'stroke="{atlas.PAPER}" stroke-width="1.4"/>'
        )

    # Legend low in the cost panel, carrying each policy's NPV total. Both
    # streams sit in the upper band of their own scale (the axis starts at zero
    # and is headroomed off the maximum), so the floor of the panel is the one
    # region no mark can reach -- and it starts clear of the wall rule, which
    # runs the full height of the panel.
    lx = ml + 40 if wall_year is None else max(ml + 40, px(wall_year) + 16)
    ly = pb2 - 74
    parts.append(
        f'<line x1="{lx}" y1="{ly}" x2="{lx + 22}" y2="{ly}" '
        f'stroke="{atlas.STEEL}" stroke-width="2.2"/>'
        f'<text x="{lx + 28}" y="{ly + 4}" font-size="10" fill="{atlas.INK2}">'
        f'Staged build (open-only) - {atlas.money(staged.npv)} NPV</text>'
    )
    if ahead.feasible:
        parts.append(
            f'<line x1="{lx}" y1="{ly + 18}" x2="{lx + 22}" y2="{ly + 18}" '
            f'stroke="{atlas.CONCRETE}" stroke-width="1.8" '
            f'stroke-dasharray="5 3"/>'
            f'<text x="{lx + 28}" y="{ly + 22}" font-size="10" '
            f'fill="{atlas.INK2}">Build ahead in year 0 - '
            f'{atlas.money(ahead.npv)} NPV '
            f'({plan.build_ahead_premium_pct:+.1f}%)</text>'
        )
    parts.append(
        f'<circle cx="{lx + 11}" cy="{ly + 36}" r="5" fill="{atlas.AMBER}" '
        f'stroke="{atlas.PAPER}" stroke-width="1.8"/>'
        f'<text x="{lx + 28}" y="{ly + 40}" font-size="10" fill="{atlas.INK2}">'
        f'A DC opens this year</text>'
    )

    atlas.svg_footer(parts, w, h, [
        f"Demand +{100.0 * plan.annual_growth:.0f}%/yr and a "
        f"{100.0 * plan.discount_rate:.0f}%/yr discount rate are ILLUSTRATIVE "
        "ASSUMPTIONS, not forecasts. Fixed cost is a recurring operating cost,",
        "not capex, and a DC opens with no construction lead time - so building "
        "ahead buys readiness, never cost. Synthetic, seeded data.",
    ])
    return atlas.svg_close(parts)


__all__ = [
    "DEFAULT_ANNUAL_GROWTH",
    "DEFAULT_DISCOUNT_RATE",
    "DEFAULT_HORIZON_YEARS",
    "POLICY_LABELS",
    "POLICY_ORDER",
    "PhasePlan",
    "PhasePolicy",
    "PhaseYear",
    "discount_factor",
    "growth_at",
    "phasing_readout",
    "run_phase_plan",
    "schedule_text",
    "to_csv",
    "to_svg",
    "wall_year_of",
]
