# Re-export the package version: the dashboard ships inside devin-metrics,
# so a second hardcoded constant here drifts on every release.
from devin_metrics import __version__

__all__ = ["__version__"]
