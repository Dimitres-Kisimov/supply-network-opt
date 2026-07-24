"""Tests for the pipeline orchestration and deliverable exports."""

from supplynet.exports import write_deliverables
from supplynet.pipeline import run_pipeline


def test_pipeline_runs_and_is_consistent():
    r = run_pipeline(seed=42)
    assert r.milp.n_opened >= 1
    assert r.savings_abs >= 0
    assert 0 <= r.savings_pct <= 100
    # Opened index round-trip matches the reported ids.
    ids = list(r.data.dcs["dc_id"])
    assert [ids[i] for i in r.opened_idx] == r.milp.opened


def test_deliverables_written_non_empty(tmp_path):
    r = run_pipeline(seed=42)
    paths = write_deliverables(r, out_dir=str(tmp_path))
    pdf = tmp_path / "supply_network_report.pdf"
    xlsx = tmp_path / "supply_network_workbook.xlsx"
    assert pdf.exists() and xlsx.exists()
    # Both deliverables must be substantial, not empty stubs.
    assert pdf.stat().st_size > 10_000
    assert xlsx.stat().st_size > 10_000
    assert set(paths) == {"pdf", "xlsx"}
