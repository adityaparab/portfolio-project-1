"""Data preparation subpackage: corpus fetchers and manifests (issues #18/#19).

Network work lives in the CLI entrypoints only; selection, tiering, and
manifest building are pure functions covered by offline unit tests.
"""

from invoiceops_agent.data.voxel51 import classify_quality_tier, select_samples

__all__ = ["classify_quality_tier", "select_samples"]
