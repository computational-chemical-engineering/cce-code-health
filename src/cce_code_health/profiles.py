"""Metric profiles. A profile is frozen once released; change it by adding a new one.

python@0 is uncalibrated (weights chosen by hand). python@1 will be calibrated
against CodeScene on files a refactor campaign does not touch.

cpp@0 (C++, experimental) is uncalibrated too: thresholds and weights by analogy with
python@0, with cyclomatic complexity (lizard's CCN) in place of cognitive complexity. A
brain function there has CCN > 25 and NLOC > 70.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Function metrics that carry debt, in report order (Python; each profile names its own).
METRICS = ("cognitive", "nloc", "nesting", "bumps", "params", "boolops")
CPP_METRICS = ("ccn", "nloc", "nesting", "params")


@dataclass(frozen=True)
class Profile:
    name: str
    # Debt per function = sum_m weight[m] * max(0, value[m] - threshold[m]).
    thresholds: dict = field(default_factory=dict)
    weights: dict = field(default_factory=dict)
    # A "brain" function has brain_metric > brain_threshold and nloc > brain_nloc
    # (gate: no new brain functions).
    brain_threshold: int = 40
    brain_nloc: int = 70
    varargs_params: int = 3        # *args and **kwargs each count as this many params
    large_file_nloc: int = 1000
    huge_file_nloc: int = 2000
    dup_window: int = 8            # normalised lines per duplicate window
    dup_fraction: float = 0.10     # file smell above this duplicated-line fraction
    language: str = "python"       # backend; config.EXTENSIONS maps file suffixes to languages
    metrics: tuple = METRICS       # debt-carrying function metrics, in report order
    brain_metric: str = "cognitive"


PROFILES = {
    "python@0": Profile(
        name="python@0",
        thresholds=dict(cognitive=15, nloc=70, nesting=3, bumps=2, params=5, boolops=2),
        weights=dict(cognitive=1.0, nloc=0.1, nesting=3.0, bumps=2.0, params=1.0, boolops=1.0),
    ),
    "cpp@0": Profile(
        name="cpp@0",
        thresholds=dict(ccn=10, nloc=70, nesting=3, params=5),
        weights=dict(ccn=1.0, nloc=0.1, nesting=3.0, params=1.0),
        brain_threshold=25, language="cpp", metrics=CPP_METRICS, brain_metric="ccn",
    ),
}


def get(name: str) -> Profile:
    try:
        return PROFILES[name]
    except KeyError:
        raise ValueError(f"unknown metric profile {name!r}; known: {sorted(PROFILES)}") from None
