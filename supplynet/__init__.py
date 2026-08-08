"""Supply-network optimization on synthetic, seeded data.

Modules:
  data         seeded network generator
  facility     capacitated facility-location MILP + greedy baseline
  flow         min-cost multi-echelon flow (graph solver + LP cross-check)
  safetystock  multi-echelon safety stock and risk pooling
  co2_sensitivity  CO2-aware variant + cost/CO2/service sensitivity (Pareto)
  resilience   single-DC-outage (N-1) contingency screen + cheapest recovery
  service_frontier  inventory service-level frontier (safety stock/cost vs service)
  pipeline     end-to-end orchestration
  exports      executive PDF + Excel deliverables + CO2 CSV/SVG
"""

from supplynet.pipeline import PipelineResult, run_pipeline

__all__ = ["PipelineResult", "run_pipeline"]
__version__ = "1.0.0"
