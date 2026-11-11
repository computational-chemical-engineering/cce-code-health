"""Metric profiles. A profile is frozen once released; change it by adding a new one.

python@0 is uncalibrated (weights chosen by hand). python@1 will be calibrated
against CodeScene on files a refactor campaign does not touch.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Function metrics that carry debt, in report order.
METRICS = ("cognitive", "nloc", "nesting", "bumps", "params", "boolops")


@dataclass(frozen=True)
class Profile:
    name: str
    # Debt per function = sum_m weight[m] * max(0, value[m] - threshold[m]).
    thresholds: dict = field(default_factory=dict)
    weights: dict = field(default_factory=dict)
    # A "brain" function exceeds both of these (gate: no new brain functions).
    brain_cognitive: int = 40
    brain_nloc: int = 70
    varargs_params: int = 3        # *args and **kwargs each count as this many params
    large_file_nloc: int = 1000
    huge_file_nloc: int = 2000
    dup_window: int = 8            # normalised lines per duplicate window
    dup_fraction: float = 0.10     # file smell above this duplicated-line fraction


PROFILES = {
    "python@0": Profile(
        name="python@0",
        thresholds=dict(cognitive=15, nloc=70, nesting=3, bumps=2, params=5, boolops=2),
        weights=dict(cognitive=1.0, nloc=0.1, nesting=3.0, bumps=2.0, params=1.0, boolops=1.0),
    ),
}


def get(name: str) -> Profile:
    try:
        return PROFILES[name]
    except KeyError:
        raise ValueError(f"unknown metric profile {name!r}; known: {sorted(PROFILES)}") from None
