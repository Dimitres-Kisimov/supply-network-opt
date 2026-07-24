# Credits

Built by **Dimitres Kisimov**, 2026.

## Tools and libraries

- **Google OR-Tools** (Apache-2.0) — CBC MILP solver via `pywraplp` for facility
  location, and `SimpleMinCostFlow` for the multi-echelon network flow.
- **SciPy** (BSD-3-Clause) — `optimize.linprog` (HiGHS) for the transportation LP
  cross-check and `stats.norm` for service-level z-scores.
- **NumPy** (BSD-3-Clause) — seeded random data generation and array math.
- **pandas** (BSD-3-Clause) — tabular network data.
- **Matplotlib** (matplotlib license, PSF-based) — the executive PDF report.
- **openpyxl** (MIT) — the Excel workbook.

## Methods

The models are standard operations-research formulations:

- Capacitated facility location (mixed-integer program).
- Minimum-cost network flow / transportation problem.
- Base-stock safety stock and the square-root risk-pooling law.

Any errors in how they are applied here are my own. All data is synthetic and
seeded; the results are model-based estimates under the assumptions stated in the
README and business case.
