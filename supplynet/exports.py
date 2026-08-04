"""Executive deliverables: a PDF report and an Excel workbook.

The PDF (matplotlib PdfPages) has a cover with the synthetic-data disclaimer and
headline savings, a network map of opened DCs and their flows, a cost-breakdown
bar, and a safety-stock pooling chart. The Excel workbook (openpyxl) has
Summary, Facilities, Flows and SafetyStock sheets.
"""

import os
import textwrap

import matplotlib

matplotlib.use("Agg")
# Dollar signs in our labels are literal currency, not math delimiters.
matplotlib.rcParams["text.parse_math"] = False
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402
from openpyxl import Workbook  # noqa: E402
from openpyxl.styles import Font  # noqa: E402

from supplynet.co2_sensitivity import to_csv, to_svg, tradeoff_readout  # noqa: E402
from supplynet.pipeline import PipelineResult, run_pipeline  # noqa: E402

DISCLAIMER = (
    "All data in this report is SYNTHETIC and generated from a fixed random seed. "
    "Figures are model-based estimates from a facility-location MILP, a min-cost "
    "network-flow model and textbook safety-stock formulas under stated "
    "assumptions (normal, independent demand; fixed lead times). They do not "
    "describe any real company and are not guarantees."
)


def _cover_page(pdf: PdfPages, r: PipelineResult) -> None:
    fig = plt.figure(figsize=(11, 8.5))
    fig.subplots_adjust(left=0.08, right=0.92, top=0.92, bottom=0.08)
    ax = fig.add_subplot(111)
    ax.axis("off")

    ax.text(0.5, 0.93, "Supply-Network Optimization", ha="center",
            fontsize=26, fontweight="bold")
    ax.text(0.5, 0.87, "Distribution-Center Siting, Network Flow & Safety Stock",
            ha="center", fontsize=14, color="#444444")

    headline = (
        f"Optimized network opens {r.milp.n_opened} of {r.data.n_dcs} candidate DCs "
        f"at a total fixed+transport cost of ${r.milp.total_cost:,.0f}."
    )
    saved = (
        f"That is ${r.savings_abs:,.0f} ({r.savings_pct:.1f}%) below the "
        f"greedy cheapest-open baseline (${r.greedy.total_cost:,.0f})."
    )
    pool = (
        f"Risk pooling cuts modeled safety stock by "
        f"{r.pooling.network_reduction_pct:.0f}% "
        f"(network) / {r.pooling.reduction_pct:.0f}% (full centralization) "
        f"at a {r.service_level:.0%} service level."
    )
    ax.text(0.5, 0.72, textwrap.fill(headline, 74), ha="center", fontsize=13)
    ax.text(0.5, 0.65, textwrap.fill(saved, 74), ha="center", fontsize=13,
            color="#0a6b0a")
    ax.text(0.5, 0.57, textwrap.fill(pool, 74), ha="center", fontsize=13,
            color="#0a4a8b")

    # Headline metric tiles.
    tiles = [
        (f"{r.milp.n_opened}", "DCs opened"),
        (f"{r.savings_pct:.1f}%", "cost vs baseline"),
        (f"${r.flow['lp'].total_cost:,.0f}", "min-cost flow"),
        (f"{r.pooling.network_reduction_pct:.0f}%", "safety-stock pooling"),
    ]
    for k, (val, lab) in enumerate(tiles):
        x0 = 0.06 + k * 0.235
        ax.add_patch(plt.Rectangle((x0, 0.30), 0.20, 0.16, transform=ax.transAxes,
                                   facecolor="#eef3f8", edgecolor="#9bb4cc"))
        ax.text(x0 + 0.10, 0.40, val, ha="center", fontsize=17, fontweight="bold",
                transform=ax.transAxes)
        ax.text(x0 + 0.10, 0.34, lab, ha="center", fontsize=9, color="#333333",
                transform=ax.transAxes)

    ax.text(0.5, 0.14, textwrap.fill(DISCLAIMER, 92), ha="center", va="center",
            fontsize=9, color="#777777",
            bbox=dict(boxstyle="round", facecolor="#fbfbf2", edgecolor="#ddddcc"))
    ax.text(0.5, 0.03, "Synthetic data - seed "
            f"{r.data.seed} - generated for portfolio demonstration",
            ha="center", fontsize=8, color="#999999")
    pdf.savefig(fig)
    plt.close(fig)


def _network_map(pdf: PdfPages, r: PipelineResult) -> None:
    fig, ax = plt.subplots(figsize=(11, 8.5))
    d = r.data
    opened = set(r.milp.opened)
    assign = r.milp.assignment
    dc_ids = list(d.dcs["dc_id"])

    # Flows: opened DC -> customer, line width by shipped volume.
    max_flow = assign.max() if assign.size else 1.0
    for i, dc_id in enumerate(dc_ids):
        if dc_id not in opened:
            continue
        for j in range(d.n_customers):
            f = assign[i, j]
            if f <= 1e-6:
                continue
            ax.plot(
                [d.dcs.iloc[i]["x"], d.customers.iloc[j]["x"]],
                [d.dcs.iloc[i]["y"], d.customers.iloc[j]["y"]],
                color="#b0c4de", linewidth=0.4 + 2.5 * f / max_flow, zorder=1,
            )

    ax.scatter(d.customers["x"], d.customers["y"], s=25, c="#555555",
               marker="o", label="Customer zone", zorder=2)

    closed = d.dcs[~d.dcs["dc_id"].isin(opened)]
    open_dcs = d.dcs[d.dcs["dc_id"].isin(opened)]
    ax.scatter(closed["x"], closed["y"], s=90, facecolors="none",
               edgecolors="#aa3333", marker="s", label="DC (not opened)", zorder=3)
    ax.scatter(open_dcs["x"], open_dcs["y"], s=140, c="#1f9d55",
               marker="s", label="DC (opened)", zorder=4)
    ax.scatter(d.plants["x"], d.plants["y"], s=180, c="#8b5a00",
               marker="^", label="Plant", zorder=5)

    for _, row in open_dcs.iterrows():
        ax.annotate(row["dc_id"], (row["x"], row["y"]), fontsize=9,
                    fontweight="bold", xytext=(4, 4), textcoords="offset points")

    ax.set_title("Optimized Network: opened DCs and outbound flows", fontsize=14)
    ax.set_xlabel("x (km)")
    ax.set_ylabel("y (km)")
    ax.legend(loc="upper right", fontsize=9, framealpha=0.9)
    ax.grid(True, linestyle=":", alpha=0.4)
    pdf.savefig(fig)
    plt.close(fig)


def _cost_breakdown(pdf: PdfPages, r: PipelineResult) -> None:
    fig, ax = plt.subplots(figsize=(11, 8.5))
    labels = ["Greedy baseline", "MILP optimized"]
    fixed = [r.greedy.fixed_cost, r.milp.fixed_cost]
    transport = [r.greedy.transport_cost, r.milp.transport_cost]

    ax.bar(labels, fixed, label="Fixed opening cost", color="#4c72b0")
    ax.bar(labels, transport, bottom=fixed, label="Transport cost", color="#dd8452")

    for k in range(2):
        total = fixed[k] + transport[k]
        ax.text(k, total, f"${total:,.0f}", ha="center", va="bottom",
                fontsize=11, fontweight="bold")

    ax.set_ylabel("Cost ($)")
    ax.set_title(
        f"Cost breakdown: MILP saves ${r.savings_abs:,.0f} "
        f"({r.savings_pct:.1f}%) vs greedy baseline",
        fontsize=13,
    )
    ax.legend()
    ax.grid(True, axis="y", linestyle=":", alpha=0.4)
    pdf.savefig(fig)
    plt.close(fig)


def _pooling_chart(pdf: PdfPages, r: PipelineResult) -> None:
    fig, ax = plt.subplots(figsize=(11, 8.5))
    p = r.pooling
    labels = [
        "Decentralized\n(1 stock point / zone)",
        f"Network\n({r.milp.n_opened} opened DCs)",
        "Fully centralized\n(single DC)",
    ]
    values = [p.decentralized, p.network, p.centralized]
    colors = ["#c44e52", "#dd8452", "#55a868"]
    bars = ax.bar(labels, values, color=colors)
    for bar, v in zip(bars, values, strict=True):
        ax.text(bar.get_x() + bar.get_width() / 2, v, f"{v:,.0f}",
                ha="center", va="bottom", fontsize=11, fontweight="bold")

    ax.set_ylabel("Total safety stock (units)")
    ax.set_title(
        f"Risk pooling at {p.service_level:.0%} service (z={p.z:.2f}): "
        f"-{p.network_reduction_pct:.0f}% network, "
        f"-{p.reduction_pct:.0f}% full centralization",
        fontsize=13,
    )
    ax.grid(True, axis="y", linestyle=":", alpha=0.4)
    pdf.savefig(fig)
    plt.close(fig)


def _co2_pareto(pdf: PdfPages, r: PipelineResult) -> None:
    fig, ax = plt.subplots(figsize=(11, 8.5))
    s = r.co2

    # Pareto frontier connector (sorted by cost).
    front = sorted(s.pareto, key=lambda d: d.cost)
    if len(front) >= 2:
        ax.plot([d.cost for d in front], [d.co2_t for d in front],
                color="#1f9d55", linestyle="--", linewidth=1.5, zorder=1,
                label="Pareto frontier")

    for d in s.designs:
        color = "#1f9d55" if d.is_pareto else "#c44e52"
        ax.scatter(d.cost, d.co2_t, s=140 if d is s.cost_optimal else 90,
                   c=color, edgecolors="#222222", zorder=3)
        ax.annotate(f"{d.n_dc} DCs", (d.cost, d.co2_t), fontsize=9,
                    xytext=(6, 6), textcoords="offset points")

    opt = s.cost_optimal
    ax.annotate("cost-optimal", (opt.cost, opt.co2_t), fontsize=9,
                color="#0a4a8b", xytext=(6, -14), textcoords="offset points")

    ax.set_xlabel("Total cost ($) - lower is better")
    ax.set_ylabel("Modeled outbound CO2 (tonnes) - lower is better")
    ax.set_title(
        "Cost vs CO2 by network density (illustrative emission factor)",
        fontsize=13,
    )
    ax.grid(True, linestyle=":", alpha=0.4)

    note = "  ".join([
        "Point label = number of open DCs.",
        f"CO2 factor {s.emission_factor_kg_per_tkm:.2f} kg/t-km "
        f"({s.unit_weight_t:.2f} t/unit) is ILLUSTRATIVE, not certified.",
    ])
    read = tradeoff_readout(s)
    caption = textwrap.fill(read[1], 96) if len(read) > 1 else ""
    ax.text(0.5, -0.13, note, transform=ax.transAxes, ha="center",
            fontsize=8, color="#777777")
    if caption:
        ax.text(0.5, -0.18, textwrap.fill(caption, 96), transform=ax.transAxes,
                ha="center", fontsize=9, color="#333333")
    fig.subplots_adjust(bottom=0.22)
    if front:
        ax.legend(loc="upper right", fontsize=9)
    pdf.savefig(fig)
    plt.close(fig)


def build_pdf(r: PipelineResult, path: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with PdfPages(path) as pdf:
        _cover_page(pdf, r)
        _network_map(pdf, r)
        _cost_breakdown(pdf, r)
        _pooling_chart(pdf, r)
        _co2_pareto(pdf, r)
        meta = pdf.infodict()
        meta["Title"] = "Supply-Network Optimization (synthetic)"
        meta["Author"] = "Dimitres Kisimov"
    return path


def build_excel(r: PipelineResult, path: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    wb = Workbook()
    bold = Font(bold=True)

    ws = wb.active
    ws.title = "Summary"
    ws["A1"] = "Supply-Network Optimization - Summary"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = "SYNTHETIC data, seed " + str(r.data.seed) + " - model-based estimates"
    rows = [
        ("Metric", "Value"),
        ("Plants / candidate DCs / customers",
         f"{r.data.n_plants} / {r.data.n_dcs} / {r.data.n_customers}"),
        ("Total demand (units)", round(r.data.total_demand, 1)),
        ("DCs opened (MILP)", r.milp.n_opened),
        ("Opened DC ids", ", ".join(r.milp.opened)),
        ("MILP total cost ($)", round(r.milp.total_cost, 2)),
        ("MILP fixed cost ($)", round(r.milp.fixed_cost, 2)),
        ("MILP transport cost ($)", round(r.milp.transport_cost, 2)),
        ("Greedy baseline cost ($)", round(r.greedy.total_cost, 2)),
        ("Savings vs baseline ($)", round(r.savings_abs, 2)),
        ("Savings vs baseline (%)", round(r.savings_pct, 2)),
        ("Min-cost flow (graph) ($)", round(r.flow["graph"].total_cost, 2)),
        ("Min-cost flow (LP) ($)", round(r.flow["lp"].total_cost, 2)),
        ("Flow graph-vs-LP abs gap ($)", round(r.flow["abs_gap"], 6)),
        ("Service level", r.service_level),
        ("Safety-stock z", round(r.pooling.z, 4)),
        ("Safety stock decentralized (units)", round(r.pooling.decentralized, 1)),
        ("Safety stock network (units)", round(r.pooling.network, 1)),
        ("Safety stock centralized (units)", round(r.pooling.centralized, 1)),
        ("Pooling reduction network (%)", round(r.pooling.network_reduction_pct, 2)),
        ("Pooling reduction full (%)", round(r.pooling.reduction_pct, 2)),
    ]
    for ridx, (a, b) in enumerate(rows, start=4):
        ws.cell(ridx, 1, a)
        ws.cell(ridx, 2, b)
        if ridx == 4:
            ws.cell(ridx, 1).font = bold
            ws.cell(ridx, 2).font = bold
    ws.column_dimensions["A"].width = 38
    ws.column_dimensions["B"].width = 30

    # Facilities sheet.
    wf = wb.create_sheet("Facilities")
    wf.append(["dc_id", "x", "y", "capacity", "fixed_cost", "opened",
               "throughput_used"])
    for c in wf[1]:
        c.font = bold
    used = r.milp.assignment.sum(axis=1)
    for i, row in r.data.dcs.iterrows():
        wf.append([
            row["dc_id"], round(float(row["x"]), 2), round(float(row["y"]), 2),
            float(row["capacity"]), float(row["fixed_cost"]),
            "yes" if row["dc_id"] in set(r.milp.opened) else "no",
            round(float(used[i]), 1),
        ])
    for col in "ABCDEFG":
        wf.column_dimensions[col].width = 14

    # Flows sheet (multi-echelon arc flows from the graph solver).
    wflow = wb.create_sheet("Flows")
    wflow.append(["arc", "flow_units"])
    for c in wflow[1]:
        c.font = bold
    for label, val in r.flow["graph"].arc_flows:
        wflow.append([label, round(val, 1)])
    wflow.column_dimensions["A"].width = 34
    wflow.column_dimensions["B"].width = 14

    # SafetyStock sheet.
    wss = wb.create_sheet("SafetyStock")
    wss.append(["scenario", "safety_stock_units", "note"])
    for c in wss[1]:
        c.font = bold
    p = r.pooling
    wss.append(["decentralized", round(p.decentralized, 1),
                "one stock point per customer zone"])
    wss.append(["network", round(p.network, 1),
                f"pooled by the {r.milp.n_opened} opened DCs"])
    wss.append(["centralized", round(p.centralized, 1), "single pooled DC"])
    wss.append([])
    wss.append(["echelon", "safety_stock_units", "lead_time_days"])
    wss.append(["DC echelon", round(r.echelon["dc_echelon"], 1),
                round(r.echelon["avg_lead_time"], 2)])
    wss.append(["Plant echelon", round(r.echelon["plant_echelon"], 1),
                round(r.echelon["avg_lead_time"] + 7.0, 2)])
    wss.column_dimensions["A"].width = 20
    wss.column_dimensions["B"].width = 20
    wss.column_dimensions["C"].width = 34

    # Customers sheet: the full synthetic demand table.
    wc = wb.create_sheet("Customers")
    wc.append(["cust_id", "x", "y", "demand_mean", "demand_std", "lead_time",
               "served_by"])
    for c in wc[1]:
        c.font = bold
    served_by = r.milp.assignment.argmax(axis=0)
    dc_ids = list(r.data.dcs["dc_id"])
    for j, row in r.data.customers.iterrows():
        wc.append([
            row["cust_id"], round(float(row["x"]), 2), round(float(row["y"]), 2),
            float(row["demand_mean"]), float(row["demand_std"]),
            float(row["lead_time"]), dc_ids[int(served_by[j])],
        ])
    for col in "ABCDEFG":
        wc.column_dimensions[col].width = 13

    # CO2 sensitivity sheet: cost-optimal design at each network density.
    wco2 = wb.create_sheet("CO2Sensitivity")
    s = r.co2
    wco2["A1"] = "CO2 / cost / service sensitivity (sweep over # open DCs)"
    wco2["A1"].font = Font(bold=True, size=12)
    wco2["A2"] = (
        "Emission factor ILLUSTRATIVE: "
        f"{s.emission_factor_kg_per_tkm:.2f} kg CO2e/tonne-km, "
        f"{s.unit_weight_t:.2f} t/unit - NOT a certified figure. Outbound leg only."
    )
    hdr = ["n_dcs", "opened", "cost_usd", "co2_tonnes", "avg_delivery_km",
           f"pct_demand_within_{int(s.service_radius_km)}km", "pareto_optimal"]
    wco2.append([])
    wco2.append(hdr)
    for c in wco2[4]:
        c.font = bold
    for d in s.designs:
        wco2.append([
            d.n_dc, ", ".join(d.opened), round(d.cost, 2), round(d.co2_t, 4),
            round(d.avg_delivery_km, 2), round(d.service_within_radius_pct, 2),
            "yes" if d.is_pareto else "no",
        ])
    wco2.append([])
    for line in tradeoff_readout(s):
        wco2.append([line])
    wco2.column_dimensions["A"].width = 16
    for col in "BCDEFG":
        wco2.column_dimensions[col].width = 16

    # Assignment sheet: opened-DC x customer shipped-units matrix.
    wa = wb.create_sheet("Assignment")
    header = ["dc_id \\ cust", *list(r.data.customers["cust_id"])]
    wa.append(header)
    for c in wa[1]:
        c.font = bold
    for i, dc_id in enumerate(dc_ids):
        if dc_id not in set(r.milp.opened):
            continue
        wa.append([dc_id, *[round(float(v), 1) for v in r.milp.assignment[i]]])
    wa.column_dimensions["A"].width = 14

    wb.save(path)
    return path


def write_deliverables(r: PipelineResult | None = None, out_dir: str = "deliverables") -> dict:
    """Write both deliverables to ``out_dir`` and return their paths."""
    if r is None:
        r = run_pipeline()
    os.makedirs(out_dir, exist_ok=True)
    pdf_path = os.path.join(out_dir, "supply_network_report.pdf")
    xlsx_path = os.path.join(out_dir, "supply_network_workbook.xlsx")
    csv_path = os.path.join(out_dir, "co2_sensitivity.csv")
    svg_path = os.path.join(out_dir, "co2_cost_frontier.svg")
    build_pdf(r, pdf_path)
    build_excel(r, xlsx_path)
    with open(csv_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(to_csv(r.co2))
    with open(svg_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(to_svg(r.co2))
    return {"pdf": pdf_path, "xlsx": xlsx_path, "csv": csv_path, "svg": svg_path}
