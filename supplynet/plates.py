"""Matplotlib side of the "network atlas" plate system (PDF pages).

Every PDF page is a numbered plate sharing one chrome: atlas kicker + plate
stamp over a hairline rule, a bold ink title with a secondary subtitle, the
plot in the middle, and a designed footer -- another hairline rule with the
page's honest caption set as secondary ink. Colors, type scale and margins
come from :mod:`supplynet.atlas` so the PDF and the SVG exports read as one
document.
"""

import textwrap

import matplotlib
from matplotlib import pyplot as plt
from matplotlib.lines import Line2D

from supplynet.atlas import (
    ATLAS_KICKER,
    GRID,
    INK,
    INK2,
    MUTED,
    PAPER,
    RULE,
    plate_label,
)

# Consistent margins for every plate (figure coordinates).
MARGIN_L = 0.06
MARGIN_R = 0.94
HEAD_RULE_Y = 0.945
FOOT_RULE_Y = 0.095

# One type scale across all plates (points).
SIZE_KICKER = 8
SIZE_TITLE = 15
SIZE_SUBTITLE = 9.5
SIZE_PANEL = 10.5
SIZE_LABEL = 9
SIZE_TICK = 8
SIZE_CAPTION = 8.5
SIZE_ANNOT = 8

RC = {
    "figure.facecolor": PAPER,
    "axes.facecolor": PAPER,
    "font.family": "DejaVu Sans",
    "font.size": 9,
    "axes.edgecolor": RULE,
    "axes.linewidth": 0.9,
    "axes.labelcolor": INK2,
    "axes.labelsize": SIZE_LABEL,
    "axes.titlesize": SIZE_PANEL,
    "axes.titlecolor": INK,
    "axes.titleweight": "bold",
    "axes.titlelocation": "left",
    "axes.grid": False,
    "grid.color": GRID,
    "grid.linewidth": 0.7,
    "grid.linestyle": "-",  # hairline, solid, never dashed
    "xtick.color": MUTED,
    "ytick.color": MUTED,
    "xtick.labelsize": SIZE_TICK,
    "ytick.labelsize": SIZE_TICK,
    "legend.frameon": False,
    "legend.fontsize": SIZE_CAPTION,
    "legend.labelcolor": INK2,
    "lines.linewidth": 2.0,
    "lines.solid_capstyle": "round",
    "lines.solid_joinstyle": "round",
}


def apply_style() -> None:
    """Install the atlas rcParams (idempotent; called once per PDF build)."""
    matplotlib.rcParams.update(RC)


def despine(ax) -> None:
    """Recessive axes: keep only the left/bottom hairline spines."""
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


def _header(fig, no: int, title: str | None, subtitle: str | None) -> None:
    fig.text(MARGIN_L, 0.962, ATLAS_KICKER, fontsize=SIZE_KICKER, color=MUTED,
             ha="left", va="baseline")
    fig.text(MARGIN_R, 0.962, plate_label(no), fontsize=SIZE_KICKER, color=MUTED,
             ha="right", va="baseline")
    fig.add_artist(Line2D([MARGIN_L, MARGIN_R], [HEAD_RULE_Y, HEAD_RULE_Y],
                          transform=fig.transFigure, color=RULE, linewidth=0.8))
    if title:
        fig.text(MARGIN_L, 0.905, title, fontsize=SIZE_TITLE, color=INK,
                 fontweight="bold", ha="left", va="baseline")
    if subtitle:
        fig.text(MARGIN_L, 0.878, subtitle, fontsize=SIZE_SUBTITLE, color=INK2,
                 ha="left", va="baseline")


def new_plate(no: int, title: str | None = None, subtitle: str | None = None):
    """A fresh 11x8.5in figure carrying the standard plate header."""
    fig = plt.figure(figsize=(11, 8.5))
    _header(fig, no, title, subtitle)
    return fig


def footer(fig, caption: str | list[str], width: int = 140) -> None:
    """Designed footer: hairline rule + wrapped caption in secondary ink."""
    fig.add_artist(Line2D([MARGIN_L, MARGIN_R], [FOOT_RULE_Y, FOOT_RULE_Y],
                          transform=fig.transFigure, color=RULE, linewidth=0.8))
    lines: list[str] = []
    for block in [caption] if isinstance(caption, str) else caption:
        lines.extend(textwrap.wrap(block, width))
    for k, line in enumerate(lines):
        fig.text(MARGIN_L, 0.072 - 0.022 * k, line, fontsize=SIZE_CAPTION,
                 color=INK2, ha="left", va="baseline")


def money_axis(ax, axis: str = "y") -> None:
    """Format an axis as whole dollars with thousands separators."""
    from matplotlib.ticker import FuncFormatter

    formatter = FuncFormatter(lambda v, _: f"${v:,.0f}")
    (ax.yaxis if axis == "y" else ax.xaxis).set_major_formatter(formatter)


def comma_axis(ax, axis: str = "y") -> None:
    """Format an axis as whole numbers with thousands separators."""
    from matplotlib.ticker import FuncFormatter

    formatter = FuncFormatter(lambda v, _: f"{v:,.0f}")
    (ax.yaxis if axis == "y" else ax.xaxis).set_major_formatter(formatter)
