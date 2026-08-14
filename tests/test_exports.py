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
    phase_csv = tmp_path / "build_schedule.csv"
    phase_svg = tmp_path / "build_schedule.svg"
    assert pdf.exists() and xlsx.exists() and csv.exists() and svg.exists()
    assert sf_csv.exists() and sf_svg.exists()
    assert growth_csv.exists() and growth_svg.exists()
    assert phase_csv.exists() and phase_svg.exists()
    # Deliverables must be substantial, not empty stubs.
    assert pdf.stat().st_size > 10_000
    assert xlsx.stat().st_size > 10_000
    assert csv.stat().st_size > 0
    assert svg.stat().st_size > 0
    assert sf_csv.stat().st_size > 0
    assert sf_svg.stat().st_size > 0
    assert growth_csv.stat().st_size > 0
    assert growth_svg.stat().st_size > 0
    assert phase_csv.stat().st_size > 0
    assert phase_svg.stat().st_size > 0
    assert set(paths) == {"pdf", "xlsx", "csv", "svg", "sf_csv", "sf_svg",
                          "growth_csv", "growth_svg", "phase_csv", "phase_svg"}

    # The SVG exports are numbered plates of the same "network atlas" as the
    # PDF pages: shared masthead kicker and a per-artifact plate stamp.
    for path, stamp in ((svg, "PLATE 05"), (sf_svg, "PLATE 07"),
                        (growth_svg, "PLATE 08"), (phase_svg, "PLATE 09")):
        text = path.read_text(encoding="utf-8")
        assert "SUPPLY-NETWORK ATLAS" in text
        assert stamp in text


def test_atlas_plate_roster_is_consistent():
    """One numbering across media: 9 plates, stamps render, palette is set."""
    from supplynet import atlas

    assert atlas.N_PLATES == 9
    assert atlas.plate_label(5) == "PLATE 05 - COST VS CO2"
    assert atlas.plate_label(8) == "PLATE 08 - GROWTH STAIRCASE"
    assert atlas.plate_label(9) == "PLATE 09 - BUILD SCHEDULE"


def test_materials_palette_hexes_are_the_validated_ones():
    """The materials palette is documented, not eyeballed.

    These three hexes are the categorical slots that were run through the
    dataviz palette validator against this atlas's white print surface on the
    all-pairs pairlist (see the provenance block in ``supplynet.atlas``); all
    five computable checks PASS there, worst all-pairs CVD dE 10.4 and
    normal-vision dE 19.2. Changing a value here without re-running the
    validator is exactly the failure this test exists to catch.
    """
    from supplynet import atlas

    assert atlas.STEEL == "#1b5586"  # slot 1: built structure
    assert atlas.KRAFT == "#a9682a"  # slot 2: goods
    assert atlas.PATINA == "#38a380"  # slot 3: full centralization
    # Attention and status stay reserved, and distinct from the series slots.
    assert atlas.AMBER == "#c07d12"
    assert atlas.CRITICAL == "#d03b3b"
    assert len({atlas.STEEL, atlas.KRAFT, atlas.PATINA, atlas.AMBER,
                atlas.CRITICAL, atlas.CONCRETE}) == 6


def test_attention_amber_never_shares_a_plate_with_kraft(tmp_path):
    """Amber and kraft collide under CVD, so they never appear together.

    Measured against the categorical slots, AMBER sits only 7.4 normal-vision
    dE from KRAFT -- below the 15 floor. It is an attention token rather than a
    fourth series precisely because of that, and the rule that keeps it honest
    is that no single plate may carry both. This asserts the rule on the actual
    generated artwork rather than on intent.
    """
    from supplynet import atlas

    r = run_pipeline(seed=42)
    write_deliverables(r, out_dir=str(tmp_path))
    for svg_path in sorted(tmp_path.glob("*.svg")):
        text = svg_path.read_text(encoding="utf-8").lower()
        has_amber = atlas.AMBER.lower() in text
        has_kraft = (atlas.KRAFT.lower() in text
                     or atlas.KRAFT_LIGHT.lower() in text)
        assert not (has_amber and has_kraft), (
            f"{svg_path.name} puts attention amber beside kraft"
        )


def test_nice_ticks_are_deterministic_and_cover_range():
    from supplynet.atlas import nice_ticks

    ticks = nice_ticks(0.0, 686_000.0, 5)
    assert ticks == nice_ticks(0.0, 686_000.0, 5)  # pure function
    assert ticks[0] >= 0.0 and ticks[-1] <= 686_000.0
    assert all(b > a for a, b in zip(ticks, ticks[1:], strict=False))
    assert nice_ticks(5.0, 5.0) == [5.0]  # degenerate range collapses safely
