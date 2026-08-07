"""Tests for the disruption-resilience (single-DC-outage N-1) screen + recovery."""

from supplynet.data import generate_network
from supplynet.facility import solve_facility_milp
from supplynet.resilience import (
    ResilienceReport,
    resilience_analysis,
    resilience_readout,
)


def _report(seed: int = 42) -> ResilienceReport:
    d = generate_network(seed=seed)
    m = solve_facility_milp(d)
    return resilience_analysis(d, m)


def test_one_scenario_per_opened_dc():
    d = generate_network(seed=42)
    m = solve_facility_milp(d)
    r = resilience_analysis(d, m)
    assert [s.failed_dc for s in r.scenarios] == list(m.opened)
    # Each scenario's survivors are exactly the other opened DCs.
    for s in r.scenarios:
        assert set(s.survivors) == set(m.opened) - {s.failed_dc}
        assert s.failed_dc not in s.survivors


def test_fill_rate_and_unmet_are_consistent():
    r = _report(42)
    for s in r.scenarios:
        # served + unmet == total demand, and fill rate is their ratio.
        assert abs(s.served_units + s.unmet_units - s.total_demand) < 1e-4
        assert abs(s.fill_rate_pct - 100.0 * s.served_units / s.total_demand) < 1e-6
        assert 0.0 <= s.fill_rate_pct <= 100.0 + 1e-9
        # A DC is "resilient" exactly when nothing is left unmet.
        assert s.resilient == (s.unmet_units <= 1e-6)


def test_served_never_exceeds_surviving_capacity():
    r = _report(42)
    for s in r.scenarios:
        # Capacity is the binding limit: cannot serve more than survivors hold.
        assert s.served_units <= s.surviving_capacity + 1e-4


def test_seed42_network_is_not_n1_resilient():
    # The lean cost-optimal 3-DC network cannot absorb any single DC loss.
    r = _report(42)
    assert not r.n_1_resilient
    assert r.n_critical == 3
    worst = r.worst
    assert worst.failed_dc == "DC1"
    # Worst single loss serves well under full demand (documented ~59.6%).
    assert 59.0 < worst.fill_rate_pct < 60.0
    assert worst.unmet_units > 7000.0


def test_recovery_restores_full_service_at_a_premium():
    r = _report(42)
    for s in r.scenarios:
        assert s.recovery_possible
        # A critical outage must activate at least one standby to recover.
        assert s.recovery_activated
        # Activated DCs are standbys, never a survivor or the failed DC.
        assert s.failed_dc not in s.recovery_activated
        assert not (set(s.recovery_activated) & set(s.survivors))
        # Restoring full service costs something here (positive premium).
        assert s.recovery_premium > 0.0


def test_recovery_premium_matches_added_fixed_plus_transport_delta():
    d = generate_network(seed=42)
    m = solve_facility_milp(d)
    r = resilience_analysis(d, m)
    for s in r.scenarios:
        expected = s.recovery_added_fixed + (s.recovery_transport - r.committed_transport)
        assert abs(s.recovery_premium - expected) < 1e-4


def test_recovered_network_excludes_failed_and_keeps_survivors():
    d = generate_network(seed=42)
    m = solve_facility_milp(d)
    r = resilience_analysis(d, m)
    dc_ids = list(d.dcs["dc_id"])
    for s in r.scenarios:
        survivor_idx = {dc_ids.index(x) for x in s.survivors}
        failed_idx = dc_ids.index(s.failed_dc)
        # Re-solve the recovery MILP directly and check the constraints held.
        rec = solve_facility_milp(d, force_open=survivor_idx, force_closed={failed_idx})
        assert s.failed_dc not in rec.opened
        assert set(s.survivors).issubset(set(rec.opened))


def test_is_deterministic():
    a = _report(42)
    b = _report(42)
    assert [s.fill_rate_pct for s in a.scenarios] == [s.fill_rate_pct for s in b.scenarios]
    assert [s.recovery_premium for s in a.scenarios] == [
        s.recovery_premium for s in b.scenarios
    ]


def test_readout_is_populated_and_honest():
    r = _report(42)
    lines = resilience_readout(r)
    assert len(lines) >= 2
    # It must not oversell: a synthetic/estimate caveat is present.
    assert any("synthetic" in ln.lower() or "estimate" in ln.lower() for ln in lines)
    # For seed 42 it must state the network is not N-1 resilient.
    assert any("NOT N-1 resilient" in ln for ln in lines)


def test_force_open_closed_are_backward_compatible():
    # The new facility params default to the unconstrained optimum unchanged.
    d = generate_network(seed=42)
    base = solve_facility_milp(d)
    same = solve_facility_milp(d, force_open=None, force_closed=None)
    assert same.opened == base.opened
    assert abs(same.total_cost - base.total_cost) < 1e-6
