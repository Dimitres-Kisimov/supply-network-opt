"""Supply-network optimization on synthetic, seeded data.

Modules:
  data         seeded network generator
  facility     capacitated facility-location MILP + greedy baseline
  flow         min-cost multi-echelon flow (graph solver + LP cross-check)
  safetystock  multi-echelon safety stock and risk pooling
  pipeline     end-to-end orchestration
  exports      executive PDF + Excel deliverables
"""

from supplynet.pipeline import PipelineResult, run_pipeline

__all__ = ["PipelineResult", "run_pipeline"]
__version__ = "1.0.0"
