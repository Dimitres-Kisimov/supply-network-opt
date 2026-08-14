"""Tests for the phased build plan (timing + NPV over the growth staircase).

The strong tests here are the ones that need no solver to know the answer:
the discount arithmetic, the capacity-wall year (a closed form that must agree
with the year the frozen policy is observed to fail), and the cost orderings
that hold by construction because each policy is the same MILP under a strictly
tighter continuity constraint.
"""

import pytest

from supplynet.data import generate_network
from supplynet.facility import solve_facility_milp
from supplynet.growth import run_growth_plan
from supplynet.phasing import (
    DEFAULT_ANNUAL_GROWTH,
    DEFAULT_DISCOUNT_RATE,
    POLICY_ORDER,
    discount_factor,
    growth_at,
    phasing_readout,
    run_phase_plan,
    schedule_text,
    to_csv,
    to_svg,
    wall_year_of,
)

SEED = 42


@pytest.fixture(scope="module")
def data():
    return generate_network(seed=SEED)


@pytest.fixture(scope="module")
def base(data):
    return solve_facility_milp(data)


@pytest.fixture(scope="module")
def plan(data, base):
    return run_phase_plan(data, base=base)


# --- the parts that need no solver ------------------------------------------

def test_growth_and_discount_are_exact():
    assert growth_at(0.06, 0) == 1.0
    assert growth_at(0.06, 2) == pytest.approx(1.1236)
    assert discount_factor(0.10, 0) == 1.0
    assert discount_factor(0.10, 1) == pytest.approx(1 / 1.10)
    assert discount_factor(0.10, 3) == pytest.approx(1 / 1.331)
    # A zero discount rate leaves every year at face value.
    assert all(discount_factor(0.0, t) == 1.0 for t in range(5))


def test_wall_year_is_the_first_year_past_the_wall():
    # 1.06^1 = 1.060 <= 1.088 < 1.1236 = 1.06^2, so the wall lands in year 2.
    assert wall_year_of(0.06, 1.0878635, 10) == 2
    # A wall beyond the horizon is reported as None rather than clamped.
    assert wall_year_of(0.06, 5.0, 10) is None
    # Flat demand never reaches any wall.
    assert wall_year_of(0.0, 1.05, 10) is None


def test_run_phase_plan_rejects_nonsense_inputs(data, base):
    for kwargs in ({"annual_growth": -0.01}, {"discount_rate": -0.5},
                   {"horizon_years": 0}):
        with pytest.raises(ValueError):
            run_phase_plan(data, base=base, **kwargs)


# --- structure of the plan ---------------------------------------------------

def test_plan_shape_and_defaults(plan):
    assert plan.annual_growth == DEFAULT_ANNUAL_GROWTH
    assert plan.discount_rate == DEFAULT_DISCOUNT_RATE
    assert set(plan.policies) == set(POLICY_ORDER)
    assert plan.horizon_growth == pytest.approx(
        growth_at(plan.annual_growth, plan.horizon_years - 1)
    )
    for key in POLICY_ORDER:
        policy = plan.policies[key]
        if policy.feasible:
            assert len(policy.years) == plan.horizon_years
            assert [y.year for y in policy.years] == list(range(plan.horizon_years))


def test_npv_is_the_sum_of_discounted_years(plan):
    for key in POLICY_ORDER:
        policy = plan.policies[key]
        if not policy.feasible:
            # A policy that cannot serve demand is reported as failing, not priced.
            assert policy.npv == 0.0
            assert policy.first_infeasible_year is not None
            continue
        assert policy.npv == pytest.approx(
            sum(y.cost * discount_factor(plan.discount_rate, y.year)
                for y in policy.years)
        )
        for y in policy.years:
            assert y.present_value == pytest.approx(y.cost * y.discount_factor)
        # Year 0 is today: undiscounted, at today's demand.
        assert policy.years[0].discount_factor == 1.0
        assert policy.years[0].growth == 1.0


def test_wall_year_matches_the_year_frozen_actually_fails(plan):
    """The closed form and the solver must agree on when capacity runs out."""
    frozen = plan.policies["frozen"]
    if plan.wall_year is None:
        assert frozen.feasible
    else:
        assert not frozen.feasible
        assert frozen.first_infeasible_year == plan.wall_year


def test_wall_year_agrees_with_the_growth_module(data, base, plan):
    """Phasing and growth must report the same committed capacity wall."""
    growth = run_growth_plan(data, base=base, growth_grid=(1.0,))
    assert plan.committed_wall_growth == pytest.approx(growth.committed_wall_growth)


# --- the orderings that hold by construction --------------------------------

def test_staged_build_never_closes_a_site(plan):
    """Open-only is the whole point of the staged policy."""
    staged = plan.policies["staged"]
    assert staged.n_closures == 0
    for prev, nxt in zip(staged.years, staged.years[1:], strict=False):
        assert set(prev.opened) <= set(nxt.opened)
        assert nxt.removed == []
    # It starts from today's committed network, never smaller.
    assert set(plan.base_opened) <= set(staged.years[0].opened)


def test_policy_costs_are_ordered_every_single_year(plan):
    """free <= staged <= build-ahead, and staged <= frozen, year by year.

    Each policy is the same MILP under a strictly tighter pin set, so these are
    not empirical observations -- they must hold at every year or the pins are
    being applied wrongly.
    """
    free = plan.policies["free"]
    staged = plan.policies["staged"]
    ahead = plan.policies["ahead"]
    frozen = plan.policies["frozen"]
    tol = 1e-6

    for t in range(plan.horizon_years):
        assert free.years[t].cost <= staged.years[t].cost + tol
        # Build-ahead holds the staged plan's FINAL design, which is always a
        # feasible choice for the staged model at year t, so staged never loses.
        assert staged.years[t].cost <= ahead.years[t].cost + tol
        if frozen.feasible or t < (frozen.first_infeasible_year or 0):
            assert staged.years[t].cost <= frozen.years[t].cost + tol


def test_npvs_and_premiums_are_ordered_and_consistent(plan):
    free = plan.policies["free"]
    staged = plan.policies["staged"]
    ahead = plan.policies["ahead"]
    assert free.npv <= staged.npv + 1e-6
    assert staged.npv <= ahead.npv + 1e-6
    assert plan.build_ahead_premium == pytest.approx(ahead.npv - staged.npv)
    assert plan.continuity_premium == pytest.approx(staged.npv - free.npv)
    assert plan.build_ahead_premium >= -1e-6
    assert plan.continuity_premium >= -1e-6
    assert plan.build_ahead_premium_pct == pytest.approx(
        100.0 * plan.build_ahead_premium / staged.npv
    )


def test_build_ahead_opens_the_staged_final_design_in_year_zero(plan):
    """The two policies differ only in timing -- that is what makes the NPV a
    price for timing rather than for a different network."""
    staged = plan.policies["staged"]
    ahead = plan.policies["ahead"]
    assert set(ahead.years[0].opened) == set(staged.final_opened)
    # And it never changes again: one build, held.
    for y in ahead.years[1:]:
        assert y.added == [] and y.removed == []


def test_seed_42_headline_numbers(plan):
    """The concrete finding this plate reports, pinned so it cannot drift."""
    staged = plan.policies["staged"]
    assert plan.base_opened == ["DC1", "DC4", "DC6"]
    assert plan.wall_year == 2
    # Today's 3 DCs, then two staged openings inside the 10-year horizon.
    assert staged.years[0].n_opened == 3
    assert [y.year for y in staged.build_events] == [2, 6]
    assert staged.final_opened == ["DC0", "DC1", "DC3", "DC4", "DC6"]
    assert plan.policies["frozen"].feasible is False
    # Building the year-9 network today is materially dearer than staging it.
    assert plan.build_ahead_premium > 0.0
    assert plan.build_ahead_premium_pct > 10.0


# --- determinism and serialization ------------------------------------------

def test_plan_is_deterministic(data, base):
    a = run_phase_plan(data, base=base)
    b = run_phase_plan(data, base=base)
    assert to_csv(a) == to_csv(b)
    assert to_svg(a) == to_svg(b)
    for key in POLICY_ORDER:
        assert a.policies[key].npv == pytest.approx(b.policies[key].npv)


def test_csv_is_parseable_and_carries_the_assumptions(plan):
    text = to_csv(plan)
    assert text.endswith("\n")
    assert "ILLUSTRATIVE ASSUMPTIONS" in text
    assert "not capex" in text
    lines = text.splitlines()
    assert lines[0].startswith("#")
    header = lines[1].split(",")
    assert header[0] == "policy" and "present_value_usd" in header
    # Every data row has exactly as many fields as the header (no stray commas
    # from a policy label or a DC list leaking into the grid).
    for line in lines[2:]:
        if not line or line.startswith("#"):
            continue
        if line.split(",")[0] == "policy":  # the NPV-summary sub-header
            break
        assert len(line.split(",")) == len(header)


def test_svg_is_a_stable_atlas_plate(plan):
    svg = to_svg(plan)
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
    assert "PLATE 09 - BUILD SCHEDULE" in svg
    assert "SUPPLY-NETWORK ATLAS" in svg
    # No wall-clock, no RNG: the artwork is a pure function of the numbers.
    assert to_svg(plan) == svg


def test_readout_states_every_assumption_it_relies_on(plan):
    text = " ".join(phasing_readout(plan))
    assert "ILLUSTRATIVE ASSUMPTIONS" in text
    assert "not forecasts" in text
    # The honest limits: recurring cost, no capex, no construction lead time.
    assert "recurring operating cost" in text
    assert "no construction lead time" in text
    assert "UNIFORM" in text
    # The plant echelon is not re-sited here and the readout must say so.
    assert "plant ceiling" in text


def test_schedule_text_shows_closures_not_just_openings(plan):
    """The free-redesign lower bound must not read like a build plan."""
    assert schedule_text(plan.policies["frozen"]) == "no change in the horizon"
    staged_text = schedule_text(plan.policies["staged"])
    assert "+DC" in staged_text and "-DC" not in staged_text
    free = plan.policies["free"]
    if free.n_closures:
        assert "-DC" in schedule_text(free)


def test_zero_growth_horizon_is_a_flat_plan(data, base):
    """With flat demand nothing is ever forced, and discounting still applies."""
    plan = run_phase_plan(data, base=base, annual_growth=0.0,
                          discount_rate=0.10, horizon_years=4)
    assert plan.wall_year is None
    staged = plan.policies["staged"]
    frozen = plan.policies["frozen"]
    assert frozen.feasible
    assert staged.n_closures == 0
    assert staged.build_events == []
    # Every year is the same network at the same cost, only discounted.
    costs = {round(y.cost, 6) for y in staged.years}
    assert len(costs) == 1
    assert staged.npv == pytest.approx(
        staged.years[0].cost * sum(1 / 1.10**t for t in range(4))
    )
