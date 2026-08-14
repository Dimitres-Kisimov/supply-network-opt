"""The "network atlas" design system: shared tokens + deterministic SVG plates.

Every visual deliverable -- the nine PDF pages and the four hand-drawn SVGs --
is a numbered *plate* in one atlas: the same masthead, hairline rules, type
scale, palette and honest designed captions. This module holds the pieces that
must stay identical across media:

  * the color tokens (a validated light-surface materials palette -- see below);
  * the plate roster (numbers + short names) so PDF pages and SVG exports
    carry consistent headers; and
  * pure-string SVG scaffolding (header, grid, footer) with **no plotting
    library, no RNG and no wall-clock**, so committed SVGs stay byte-stable.

PALETTE -- a materials palette, because the subject is physical. A supply
network is concrete yards, steel racking, kraft-board pallets, painted hazard
lines and trucks on asphalt, so the atlas is toned in those materials rather
than in abstract technical blues. Each material carries one job and keeps it
across every plate:

  * CONCRETE (neutral greys) -- the ground the network sits on: gridlines,
    plate rules, customer zones, and any baseline or dominated design that must
    recede;
  * STEEL -- built structure and the decision itself: the opened DCs, the
    optimized network, the hero series on every plate;
  * KRAFT -- goods: the plants that make them, the transport cost of moving
    them, and (in its light step) the flow lines that carry them;
  * PATINA (weathered copper) -- the fully-centralized pooling regime, the one
    alternative design that is neither the hero nor a baseline;
  * AMBER -- painted safety yellow, reserved for ATTENTION: an expansion
    trigger, a build year, a threshold. Always beside a text label;
  * SIGNAL RED (``CRITICAL``) -- reserved for genuinely bad states only
    (capacity wall, unmet demand). Always beside a text label.

Provenance -- machine-validated, not eyeballed. The three categorical slots
``STEEL, KRAFT, PATINA`` were run through the dataviz palette validator against
this atlas's own white print surface, on the *all-pairs* pairlist (the network
map is a scatter form, where any two marks can end up neighbours)::

    validate_palette.js "#1b5586,#a9682a,#38a380" --surface "#ffffff" \
        --mode light --pairs all
    [PASS] Lightness band      all 3 inside L 0.43-0.77
    [PASS] Chroma floor        all 3 >= 0.1
    [PASS] CVD separation      worst all-pairs #38a380 <-> #a9682a
                               dE 10.4 (deutan) . tritan 20.3
    [PASS] Normal-vision floor worst all-pairs #38a380 <-> #a9682a dE 19.2
    [PASS] Contrast vs surface all 3 >= 3:1
    -> ALL CHECKS PASS

AMBER is a status-style ATTENTION token, not a fourth series: measured against
the categorical slots it collides with KRAFT (normal-vision dE 7.4), so the two
never appear on the same plate, and amber always ships with a text label -- the
documented icon-plus-label mitigation. ``tests/test_exports.py`` asserts both
the hexes and the no-amber-beside-kraft rule on the generated artwork.

Series identity is never color-alone: every series also carries a legend entry,
a direct label or a distinct dash/marker, and every mark colour clears 3:1 on
the white surface.
"""

import math

# --- Surfaces and ink (light / print) ---------------------------------------
PAPER = "#ffffff"  # page + plot surface: paper
INK = "#0b0b0b"  # asphalt black: primary text, strongest emphasis
INK2 = "#52514e"  # secondary text (captions, axis titles)
MUTED = "#898781"  # muted text (ticks, kickers)
GRID = "#e3e2df"  # concrete dust: hairline gridlines, solid, recessive
RULE = "#c2c1bc"  # concrete edge: axis baselines and plate rules

# --- Materials (validated categorical slots on the white surface) -----------
STEEL = "#1b5586"  # slot 1: built structure -- opened DCs, the optimized network
STEEL_DARK = "#123c5e"  # darker steel step (fixed opening cost, deep emphasis)
KRAFT = "#a9682a"  # slot 2: goods -- plants, and the cost of moving product
KRAFT_LIGHT = "#d3a878"  # light kraft step: goods in motion (flow lines)
PATINA = "#38a380"  # slot 3: weathered copper -- fully-centralized pooling
CONCRETE = "#82817d"  # de-emphasis series: greedy, frozen, dominated, zones
CONCRETE_FILL = "#c2c1bc"  # lighter concrete step (baseline bar segments)

# --- Attention and status (reserved -- never a plain series color) ----------
AMBER = "#c07d12"  # painted safety yellow: triggers, build years, thresholds
CRITICAL = "#d03b3b"  # signal red: capacity wall, unmet demand; always labeled
GOOD_TEXT = "#006300"  # success text token (savings line on the cover)

SVG_FONT = "Segoe UI, Arial, sans-serif"

# --- The plate roster: one numbering across PDF and SVG ---------------------
ATLAS_KICKER = "SUPPLY-NETWORK ATLAS"
PLATES = {
    1: "SUMMARY",
    2: "NETWORK MAP",
    3: "COST BREAKDOWN",
    4: "RISK POOLING",
    5: "COST VS CO2",
    6: "N-1 RESILIENCE",
    7: "SERVICE FRONTIER",
    8: "GROWTH STAIRCASE",
    9: "BUILD SCHEDULE",
}
N_PLATES = len(PLATES)


def plate_label(no: int) -> str:
    """The right-hand header stamp, e.g. ``PLATE 05 . COST VS CO2``."""
    return f"PLATE {no:02d} - {PLATES[no]}"


def nice_ticks(lo: float, hi: float, n: int = 5) -> list[float]:
    """Deterministic 'nice number' axis ticks covering [lo, hi] (1/2/2.5/5 steps)."""
    span = hi - lo
    if span <= 0:
        return [lo]
    raw = span / max(n - 1, 1)
    mag = 10.0 ** math.floor(math.log10(raw))
    step = 10.0 * mag
    for mult in (1.0, 2.0, 2.5, 5.0, 10.0):
        if mult * mag >= raw:
            step = mult * mag
            break
    first = math.ceil(lo / step - 1e-9) * step
    ticks = []
    t = first
    while t <= hi + 1e-9 * span:
        ticks.append(round(t, 10))
        t += step
    return ticks


def money(v: float) -> str:
    """Compact, honest money tick: $310k / $1.2M (full figures live in the CSVs)."""
    if abs(v) >= 1_000_000:
        s = f"${v / 1_000_000:.1f}M"
        return s.replace(".0M", "M")
    if abs(v) >= 1_000:
        s = f"${v / 1_000:.0f}k"
        return s
    return f"${v:,.0f}"


# --- SVG plate scaffolding (pure strings, byte-stable) -----------------------

# Shared geometry: header band above the plot, footer band below it.
HEADER_H = 76  # kicker + rule + title
FOOTER_H = 44  # rule + caption line(s)


def svg_open(w: int, h: int) -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}" font-family="{SVG_FONT}">',
        f'<rect width="{w}" height="{h}" fill="{PAPER}"/>',
    ]


def svg_header(parts: list[str], w: int, plate_no: int, title: str) -> None:
    """Masthead: atlas kicker left, plate stamp right, hairline rule, title."""
    parts.append(
        f'<text x="28" y="24" font-size="9" letter-spacing="2" '
        f'fill="{MUTED}">{ATLAS_KICKER}</text>'
    )
    parts.append(
        f'<text x="{w - 28}" y="24" text-anchor="end" font-size="9" '
        f'letter-spacing="2" fill="{MUTED}">{plate_label(plate_no)}</text>'
    )
    parts.append(
        f'<line x1="28" y1="33" x2="{w - 28}" y2="33" stroke="{RULE}" stroke-width="1"/>'
    )
    parts.append(
        f'<text x="28" y="58" font-size="15" font-weight="bold" '
        f'fill="{INK}">{title}</text>'
    )


def svg_footer(parts: list[str], w: int, h: int, caption_lines: list[str]) -> None:
    """Designed footer: hairline rule + honest caption lines in secondary ink."""
    y_rule = h - FOOTER_H + 4
    parts.append(
        f'<line x1="28" y1="{y_rule}" x2="{w - 28}" y2="{y_rule}" '
        f'stroke="{RULE}" stroke-width="1"/>'
    )
    for k, line in enumerate(caption_lines):
        parts.append(
            f'<text x="28" y="{y_rule + 16 + 13 * k}" font-size="9.5" '
            f'fill="{INK2}">{line}</text>'
        )


def svg_grid_y(
    parts: list[str],
    ticks: list[float],
    py,
    x0: float,
    x1: float,
    fmt,
) -> None:
    """Horizontal hairline gridlines with muted labels left of the plot."""
    for t in ticks:
        y = py(t)
        parts.append(
            f'<line x1="{x0}" y1="{y:.1f}" x2="{x1}" y2="{y:.1f}" '
            f'stroke="{GRID}" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{x0 - 8}" y="{y + 3.5:.1f}" text-anchor="end" '
            f'font-size="9.5" fill="{MUTED}">{fmt(t)}</text>'
        )


def svg_ticks_x(
    parts: list[str],
    ticks: list[float],
    px,
    y_base: float,
    fmt,
) -> None:
    """X tick marks + muted labels under the plot baseline."""
    for t in ticks:
        x = px(t)
        parts.append(
            f'<line x1="{x:.1f}" y1="{y_base}" x2="{x:.1f}" y2="{y_base + 4}" '
            f'stroke="{RULE}" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{x:.1f}" y="{y_base + 16}" text-anchor="middle" '
            f'font-size="9.5" fill="{MUTED}">{fmt(t)}</text>'
        )


def svg_baseline(parts: list[str], x0: float, x1: float, y: float) -> None:
    parts.append(
        f'<line x1="{x0}" y1="{y:.1f}" x2="{x1}" y2="{y:.1f}" '
        f'stroke="{RULE}" stroke-width="1.2"/>'
    )


def svg_close(parts: list[str]) -> str:
    parts.append("</svg>")
    return "\n".join(parts) + "\n"
