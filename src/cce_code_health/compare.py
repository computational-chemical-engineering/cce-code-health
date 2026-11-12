"""Compare reference outputs: bitwise, or numerically within a declared tolerance.

Tolerance (declared by a human in config, never by a loop): {rel, abs, ulp}. An element
passes if |h - b| <= abs + rel * |b|, or (ulp given) |h - b| <= ulp * spacing(|b|).
NaNs must be at the same positions. Files: `.npy` (needs numpy) or text, where every
number is compared within tolerance and the remaining text must be identical.
"""
from __future__ import annotations

import io
import math
import re

NUMBER = re.compile(rb"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?|[-+]?(?:nan|inf)\b", re.I)


def _numbers(data: bytes, name: str):
    if name.endswith(".npy"):
        import numpy as np  # optional dependency
        return np.load(io.BytesIO(data), allow_pickle=False).ravel().astype(float).tolist(), b""
    return [float(m) for m in NUMBER.findall(data)], NUMBER.sub(b"#", data)


def deviation(b: float, h: float) -> tuple[float, float, float]:
    """(absolute, relative, ulps) deviation of h from b."""
    if math.isnan(b) or math.isnan(h):
        return (0.0, 0.0, 0.0) if math.isnan(b) and math.isnan(h) else (math.inf,) * 3
    if b == h:
        return 0.0, 0.0, 0.0
    d = abs(h - b)
    return d, d / abs(b) if b else math.inf, d / math.ulp(abs(b)) if math.isfinite(b) else math.inf


def compare(base: bytes, head: bytes, name: str, tol: dict | None) -> tuple[bool, str]:
    """(ok, detail). tol None means bitwise; detail always reports the largest deviations."""
    if base == head:
        return True, "bitwise equal"
    try:
        nb, sb = _numbers(base, name)
        nh, sh = _numbers(head, name)
    except Exception as exc:
        return False, f"differs (not comparable numerically: {exc})"
    if sb != sh or len(nb) != len(nh):
        return False, f"differs in structure ({len(nb)} vs {len(nh)} numbers)"
    worst = [0.0, 0.0, 0.0]
    ok = tol is not None
    t_abs, t_rel, t_ulp = ((tol or {}).get(k, 0) for k in ("abs", "rel", "ulp"))
    for b, h in zip(nb, nh):
        d = deviation(b, h)
        worst = [max(w, x) for w, x in zip(worst, d)]
        if ok and not (d[0] <= t_abs + t_rel * abs(b) or (t_ulp and d[2] <= t_ulp)):
            ok = False
    detail = f"max abs {worst[0]:.3g}, rel {worst[1]:.3g}, ulp {worst[2]:.3g}"
    return ok, ("within tolerance: " if ok else "outside tolerance: " if tol else "not bitwise: ") + detail
