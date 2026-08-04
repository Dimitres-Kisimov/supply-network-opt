"""Command-line entry point.

    python -m supplynet                 print the analysis report (ASCII only)
    python -m supplynet --deliverables  also write the PDF + Excel deliverables
"""

import argparse
import os
import sys

# Windows console safety: never let a non-UTF-8 console crash on output.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

from supplynet.co2_sensitivity import tradeoff_readout  # noqa: E402
from supplynet.pipeline import run_pipeline  # noqa: E402


def _report(r) -> str:
    p = r.pooling
    d = r.data
    lines = [
        "=" * 66,
        f"SUPPLY-NETWORK OPTIMIZATION  (synthetic, seed {d.seed})",
        "=" * 66,
        "Data is SYNTHETIC and seeded; results are model-based estimates.",
        "",
        f"Network: {d.n_plants} plants, {d.n_dcs} candidate DCs, "
        f"{d.n_customers} customer zones, demand {d.total_demand:,.0f} units",
        "",
        "-- Facility location (capacitated MILP vs greedy baseline) --",
        f"  MILP opens {r.milp.n_opened} DCs: {', '.join(r.milp.opened)}  "
        f"[{r.milp.status}]",
        f"  MILP total cost   : ${r.milp.total_cost:,.0f}  "
        f"(fixed ${r.milp.fixed_cost:,.0f} + transport ${r.milp.transport_cost:,.0f})",
        f"  Greedy baseline   : ${r.greedy.total_cost:,.0f}  "
        f"(opens {r.greedy.n_opened} DCs)",
        f"  MILP saves        : ${r.savings_abs:,.0f}  "
        f"({r.savings_pct:.1f}% below baseline)",
        "",
        "-- Min-cost multi-echelon flow (plant -> DC -> customer) --",
        f"  Graph solver cost : ${r.flow['graph'].total_cost:,.2f}",
        f"  LP  solver  cost  : ${r.flow['lp'].total_cost:,.2f}",
        f"  Cross-check gap   : ${r.flow['abs_gap']:.4f} (abs), "
        f"{r.flow['rel_gap']:.2e} (rel)",
        "",
        "-- Multi-echelon safety stock and risk pooling --",
        f"  Service level {p.service_level:.0%}  ->  z = {p.z:.3f}",
        f"  Decentralized     : {p.decentralized:,.0f} units "
        f"(one stock point per zone)",
        f"  Network pooled    : {p.network:,.0f} units "
        f"({r.milp.n_opened} opened DCs)  -{p.network_reduction_pct:.1f}%",
        f"  Fully centralized : {p.centralized:,.0f} units (single DC)  "
        f"-{p.reduction_pct:.1f}%",
        "",
        "-- CO2-aware sensitivity (cost vs CO2 vs last-mile service) --",
        "  Emission factor is ILLUSTRATIVE "
        f"({r.co2.emission_factor_kg_per_tkm:.2f} kg CO2e/tonne-km, "
        f"{r.co2.unit_weight_t:.2f} t/unit), not certified.",
        f"  {'#DCs':>4}  {'cost($)':>10}  {'CO2(t)':>7}  {'avg km':>7}  "
        f"{'<=' + str(int(r.co2.service_radius_km)) + 'km%':>7}  pareto",
    ]
    for pt in r.co2.designs:
        lines.append(
            f"  {pt.n_dc:>4}  {pt.cost:>10,.0f}  {pt.co2_t:>7.2f}  "
            f"{pt.avg_delivery_km:>7.1f}  {pt.service_within_radius_pct:>7.1f}  "
            f"{'*' if pt.is_pareto else ' '}"
        )
    lines.append("")
    lines.extend("  " + s for s in tradeoff_readout(r.co2))
    lines.append("=" * 66)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="supplynet",
        description="Supply-network optimization on synthetic, seeded data.",
    )
    parser.add_argument("--deliverables", action="store_true",
                        help="write the PDF and Excel deliverables to deliverables/")
    parser.add_argument("--seed", type=int, default=42, help="random seed")
    parser.add_argument("--service-level", type=float, default=0.95,
                        help="target service level for safety stock (0-1)")
    parser.add_argument("--out-dir", default="deliverables",
                        help="output directory for deliverables")
    args = parser.parse_args(argv)

    r = run_pipeline(seed=args.seed, service_level=args.service_level)
    print(_report(r))

    if args.deliverables:
        from supplynet.exports import write_deliverables

        paths = write_deliverables(r, out_dir=args.out_dir)
        print("")
        print("Deliverables written:")
        for kind, path in paths.items():
            size_kb = os.path.getsize(path) / 1024
            print(f"  {kind:<5} {path}  ({size_kb:.1f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
