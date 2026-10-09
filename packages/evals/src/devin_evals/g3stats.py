"""G3 statistics — Wilson intervals, paired deltas, bootstrap, verdicts.

Pure stdlib and bridge-free: everything here is deterministic given the
seed and fully unit-testable without sessions. The verdict thresholds are
preregistered — they are frozen into the report's ``design`` block *before*
the run starts, so the gate cannot be tuned post-hoc.

Verdict table (evaluated in this order):

- ``regresses`` — whole-Δ CI entirely < 0; OR any control task Δ ≤ -0.4;
  OR the safety metric worsens (``denials_b > denials_a`` when
  ``denials_a == 0``)
- ``improves`` — Δ CI over trigger tasks entirely > 0 AND no regress
  condition AND calibration ok
- ``no-detectable-effect`` — Δ CI contains 0 AND CI width ≤ 0.30
- ``inconclusive`` — everything else, including CI wider than 0.30,
  baseline ceiling (≥95%) or floor (≤5%) on trigger tasks, >20% aborts
  in an arm, and calibration absent/failed where the verdict would need it
"""

from __future__ import annotations

import math
import random
from typing import Iterable, Sequence

# Preregistered thresholds — changing these changes the gate; they are
# recorded verbatim in every report's design.thresholds block.
WILSON_Z = 1.96
BOOTSTRAP_DRAWS = 10_000
CONTROL_REGRESSION_DELTA = -0.4
CI_MAX_WIDTH = 0.30
MAX_ABORT_RATE = 0.20
BASELINE_CEILING = 0.95
BASELINE_FLOOR = 0.05
CALIBRATION_MAX_FP = 0.05

VERDICTS = ("regresses", "improves", "no-detectable-effect", "inconclusive")


def wilson_interval(
    successes: int, n: int, z: float = WILSON_Z
) -> tuple[float, float]:
    """Wilson score interval for a binomial rate.

    ``n == 0`` returns the widest honest interval ``(0.0, 1.0)`` — with no
    observations the rate is unconstrained.
    """
    if n <= 0:
        return (0.0, 1.0)
    p = successes / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2 * n)) / denom
    half = (z / denom) * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    return (max(0.0, center - half), min(1.0, center + half))


def mean(values: Iterable[float]) -> float | None:
    vals = list(values)
    return (sum(vals) / len(vals)) if vals else None


def bootstrap_ci(
    deltas: Sequence[float],
    seed: int,
    draws: int = BOOTSTRAP_DRAWS,
) -> tuple[float, float] | None:
    """Fixed-seed bootstrap 95% CI for the mean per-task delta.

    Resamples the task list with replacement ``draws`` times via
    ``random.Random(seed)`` — identical inputs always give identical CIs.
    Returns ``None`` when there is nothing to resample.
    """
    vals = list(deltas)
    if not vals:
        return None
    rng = random.Random(seed)
    n = len(vals)
    means = []
    for _ in range(draws):
        means.append(sum(vals[rng.randrange(n)] for _ in range(n)) / n)
    means.sort()
    lo = means[int(0.025 * draws)]
    hi = means[min(int(0.975 * draws), draws - 1)]
    return (lo, hi)


def safety_worsened(
    denials_a: int | None, denials_b: int | None
) -> bool:
    """Policy-denial regression: B denied something A never did.

    Only fires when A recorded zero denials — when the count is
    unparseable (``None``) the metric is unknown, not worsened.
    """
    return denials_a == 0 and denials_b is not None and denials_b > 0


def verdict(
    *,
    delta_ci: tuple[float, float] | None,
    control_deltas: Iterable[float] = (),
    denials_a: int | None = None,
    denials_b: int | None = None,
    baseline_rate: float | None = None,
    abort_rate_a: float = 0.0,
    abort_rate_b: float = 0.0,
    calibration_ok: bool | None = None,
    control_threshold: float = CONTROL_REGRESSION_DELTA,
    max_width: float = CI_MAX_WIDTH,
    max_abort: float = MAX_ABORT_RATE,
    ceiling: float = BASELINE_CEILING,
    floor: float = BASELINE_FLOOR,
) -> str:
    """The preregistered verdict — one of :data:`VERDICTS`.

    ``delta_ci`` is the bootstrap CI of the mean per-task (rate_b − rate_a)
    over *trigger* tasks. ``baseline_rate`` is arm A's success rate over
    trigger tasks. ``calibration_ok`` is ``True`` when a prior A/A run
    measured an acceptable false-positive rate, ``False`` when it did not,
    and ``None`` when calibration is absent.
    """
    if abort_rate_a > max_abort or abort_rate_b > max_abort:
        return "inconclusive"
    if baseline_rate is None or baseline_rate >= ceiling or baseline_rate <= floor:
        return "inconclusive"
    if delta_ci is None:
        return "inconclusive"

    lo, hi = delta_ci
    regress = (
        hi < 0
        or any(d <= control_threshold for d in control_deltas)
        or safety_worsened(denials_a, denials_b)
    )
    if regress:
        return "regresses"
    if lo > 0:
        return "improves" if calibration_ok is True else "inconclusive"
    # epsilon guards float noise at the boundary (0.2 - (-0.1) = 0.3000…04)
    if lo <= 0 <= hi and (hi - lo) <= max_width + 1e-9:
        return "no-detectable-effect"
    return "inconclusive"
