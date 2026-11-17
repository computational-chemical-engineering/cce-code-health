"""Metric profiles. A profile is frozen once released; change it by adding a new one.

python@0 is uncalibrated (weights chosen by hand). python@1 will be calibrated
against CodeScene on files a refactor campaign does not touch.

cpp@0 (C++, experimental) is uncalibrated too: thresholds and weights by analogy with
python@0, with cyclomatic complexity (lizard's CCN) in place of cognitive complexity. A
brain function there has CCN > 25 and NLOC > 70.

cpp@1 (in development, not frozen) keeps cpp@0's numbers and changes what is measured
(cpp_rules; docs/CPP1_LAYERS.md).
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

# Function metrics that carry debt, in report order (Python; each profile names its own).
METRICS = ("cognitive", "nloc", "nesting", "bumps", "params", "boolops")
CPP_METRICS = ("ccn", "nloc", "nesting", "params")
CPP1_METRICS = CPP_METRICS + ("captures",)    # captures: kernel lambdas only (cpp@1 rule 4)


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
    # Flat registration tables (Python binding modules): their length is not a smell, so
    # nloc carries no debt and does not make them brain functions. Matched on the last
    # component of the qualified name.
    table_functions: frozenset = frozenset()
    # C++ measurement rules beyond cpp@0 (docs/CPP1_LAYERS.md): "preprocess" measures one
    # branch per #if group and does not count `if constexpr` as a decision or a level;
    # "loop_credit" counts a nesting path that holds two loops or more one level less.
    cpp_rules: frozenset = frozenset()
    # "kernels" (cpp@1 rule 3): lambdas introduced by these macros, or passed in brackets to
    # these calls, are scored as functions of their own (cpp_kernels).
    kernel_macros: tuple = ()
    kernel_calls: tuple = ()
    # cpp@1 rule (4): kernel macros whose lambda captures *this (one capture).
    kernel_this_macros: tuple = ()


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
        table_functions=frozenset({"NB_MODULE", "PYBIND11_MODULE", "BOOST_PYTHON_MODULE"}),
    ),
}
# In development, not frozen: rules are added one by one (docs/CPP1_LAYERS.md) and the
# profile is released only after a Peclet run. Thresholds and weights of cpp@0, plus captures
# (> 12, weight 0.5; the capture count alone, params are charged on their own; 12 after the
# Peclet trial: > 8 flagged 17.5 % of kernels, > 12 flags 6.1 %, docs/CPP1_LAYERS.md).
_CPP0 = PROFILES["cpp@0"]
PROFILES["cpp@1"] = replace(
    _CPP0, name="cpp@1", metrics=CPP1_METRICS,
    thresholds=dict(_CPP0.thresholds, captures=12), weights=dict(_CPP0.weights, captures=0.5),
    cpp_rules=frozenset({"preprocess", "loop_credit", "kernels"}),
    kernel_macros=("KOKKOS_LAMBDA", "KOKKOS_CLASS_LAMBDA"), kernel_this_macros=("KOKKOS_CLASS_LAMBDA",),
    kernel_calls=("parallel_for", "parallel_reduce", "parallel_scan"))


def get(name: str) -> Profile:
    try:
        return PROFILES[name]
    except KeyError:
        raise ValueError(f"unknown metric profile {name!r}; known: {sorted(PROFILES)}") from None
