"""The "network atlas" design system: shared tokens + deterministic SVG plates.

Every visual deliverable -- the eight PDF pages and the three hand-drawn SVGs --
is a numbered *plate* in one atlas: the same masthead, hairline rules, type
scale, palette and honest designed captions. This module holds the pieces that
must stay identical across media:

  * the color tokens (a validated light-surface palette -- see below);
  * the plate roster (numbers + short names) so PDF pages and SVG exports
    carry consistent headers; and
  * pure-string SVG scaffolding (header, grid, footer) with **no plotting
    library, no RNG and no wall-clock**, so committed SVGs stay byte-stable.

Palette provenance: the categorical hues are the dataviz reference palette
(slot 1 blue, slot 2 orange, slot 3 aqua), machine-validated against a white
surface -- lightness band, chroma floor, adjacent-pair CVD separation >= 8
(OKLab x100) and normal-vision separation >= 15 all PASS. Series identity is
never color-alone: every series here also carries a legend entry, a direct
label or a distinct dash/marker. Emphasis is ink-vs-gray: the optimized
network wears blue/ink, baselines and dominated designs wear gray. Status red
is reserved for genuinely bad states (capacity wall, unmet demand) and always
ships with a text label.
"""

import math

# --- Surfaces and ink (light / print) ---------------------------------------
PAPER = "#ffffff"  # page + plot surface
INK = "#0b0b0b"  # primary text, strongest emphasis
INK2 = "#52514e"  # secondary text (captions, axis titles)
MUTED = "#898781"  # muted text (ticks, kickers) and de-emphasized series
GRID = "#e1e0d9"  # hairline gridlines, solid, recessive
RULE = "#c3c2b7"  # axis baselines and plate rules

# --- Series hues (validated categorical slots on white) ---------------------
BLUE = "#2a78d6"  # slot 1: the optimized network (MILP), the hero entity
BLUE_DARK = "#1c5cab"  # darker sequential step of the same blue
BLUE_SOFT = "#6da7ec"  # lighter sequential step (stacked-bar companion)
BLUE_FLOW = "#9ec5f4"  # light sequential step: flow lines on the map
ORANGE = "#eb6834"  # slot 2: plants / inbound echelon
AQUA = "#1baf7a"  # slot 3: fully-centralized pooling regime
SERIES_GRAY = "#898781"  # de-emphasis series: greedy, frozen, dominated
GRAY_FILL = "#c3c2b7"  # lighter gray step (baseline bar segments)

# --- Status (reserved -- never a series color) ------------------------------
CRITICAL = "#d03b3b"  # capacity wall, unmet demand; always labeled in text
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
