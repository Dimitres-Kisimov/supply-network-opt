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

    # The SVG exports are numbered plates of the same "network atlas" as the
    # PDF pages: shared masthead kicker and a per-artifact plate stamp.
    for path, stamp in ((svg, "PLATE 05"), (sf_svg, "PLATE 07"),
                        (growth_svg, "PLATE 08")):
        text = path.read_text(encoding="utf-8")
        assert "SUPPLY-NETWORK ATLAS" in text
        assert stamp in text


def test_atlas_plate_roster_is_consistent():
    """One numbering across media: 8 plates, stamps render, palette is set."""
    from supplynet import atlas

    assert atlas.N_PLATES == 8
    assert atlas.plate_label(5) == "PLATE 05 - COST VS CO2"
    assert atlas.plate_label(8) == "PLATE 08 - GROWTH STAIRCASE"
    # The series hues are the validated categorical slots (light surface).
    assert atlas.BLUE == "#2a78d6"
    assert atlas.ORANGE == "#eb6834"
    assert atlas.AQUA == "#1baf7a"


def test_nice_ticks_are_deterministic_and_cover_range():
    from supplynet.atlas import nice_ticks

    ticks = nice_ticks(0.0, 686_000.0, 5)
    assert ticks == nice_ticks(0.0, 686_000.0, 5)  # pure function
    assert ticks[0] >= 0.0 and ticks[-1] <= 686_000.0
    assert all(b > a for a, b in zip(ticks, ticks[1:], strict=False))
    assert nice_ticks(5.0, 5.0) == [5.0]  # degenerate range collapses safely
