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
    csv = tmp_path / "co2_sensitivity.csv"
    svg = tmp_path / "co2_cost_frontier.svg"
    sf_csv = tmp_path / "service_frontier.csv"
    sf_svg = tmp_path / "service_frontier.svg"
    growth_csv = tmp_path / "growth_plan.csv"
    growth_svg = tmp_path / "growth_expansion.svg"
    assert pdf.exists() and xlsx.exists() and csv.exists() and svg.exists()
    assert sf_csv.exists() and sf_svg.exists()
    assert growth_csv.exists() and growth_svg.exists()
    # Deliverables must be substantial, not empty stubs.
    assert pdf.stat().st_size > 10_000
    assert xlsx.stat().st_size > 10_000
    assert csv.stat().st_size > 0
    assert svg.stat().st_size > 0
    assert sf_csv.stat().st_size > 0
    assert sf_svg.stat().st_size > 0
    assert growth_csv.stat().st_size > 0
    assert growth_svg.stat().st_size > 0
    assert set(paths) == {"pdf", "xlsx", "csv", "svg", "sf_csv", "sf_svg",
                          "growth_csv", "growth_svg"}
