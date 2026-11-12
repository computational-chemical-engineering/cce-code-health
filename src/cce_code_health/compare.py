"""Compare reference outputs: bitwise, or numerically within a declared tolerance.

Tolerance (declared by a human in config, never by a loop): {rel, abs, ulp}; any other key
is an error. A float element passes if |h - b| <= abs + rel * |b|, or (ulp given)
|h - b| <= ulp * spacing(|b|). Non-finite values pass only if both are NaN or both are the
same signed infinity. Files: `.npy` (needs numpy) or text.

.npy: shape and dtype must be identical (else "structure"); integer and bool arrays are
compared exactly; complex arrays compare real and imaginary parts separately; the ulp is
measured in the array's own dtype.

Text: every number (not preceded or followed by a letter, digit or underscore) is compared;
tokens without '.', 'e' or 'E' (integers) exactly, the others within tolerance; the
remaining text must be identical. A file that is not valid UTF-8 and not `.npy` is
"binary file, not comparable" unless bitwise equal.
"""
from __future__ import annotations

import io
import math
import re

NUMBER = re.compile(rb"(?<![A-Za-z0-9_])(?:[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?|[-+]?(?:nan|inf))(?![A-Za-z0-9_])",
                    re.I)
INTEGER = re.compile(rb"[-+]?\d+")
TOLERANCE_KEYS = ("rel", "abs", "ulp")


def deviation(b: float, h: float) -> tuple[float, float, float]:
    """(absolute, relative, ulps) deviation of h from b, spacing of b in float64."""
    return _dev(b, h, math.ulp(abs(b)) if math.isfinite(b) else math.inf)


def _dev(b: float, h: float, spacing: float) -> tuple[float, float, float]:
    if math.isnan(b) or math.isnan(h):
        return (0.0, 0.0, 0.0) if math.isnan(b) and math.isnan(h) else (math.inf,) * 3
    if b == h:
        return 0.0, 0.0, 0.0
    if not (math.isfinite(b) and math.isfinite(h)):
        return (math.inf,) * 3
    d = abs(h - b)
    return d, d / abs(b) if b else math.inf, d / spacing if spacing else math.inf


def _floats(nb, nh, spacing, tol) -> tuple[bool, list[float]]:
    """Compare two equally long float sequences; spacing[i] is the ulp of |nb[i]|."""
    worst = [0.0, 0.0, 0.0]
    ok = True
    t_abs, t_rel, t_ulp = ((tol or {}).get(k, 0) for k in ("abs", "rel", "ulp"))
    for b, h, sp in zip(nb, nh, spacing):
        d = _dev(b, h, sp)
        worst = [max(w, x) for w, x in zip(worst, d)]
        if d == (0.0, 0.0, 0.0):
            continue
        finite = math.isfinite(b) and math.isfinite(h)
        if ok and not (tol is not None and finite and (d[0] <= t_abs + t_rel * abs(b) or (t_ulp and d[2] <= t_ulp))):
            ok = False
    return ok, worst


def _npy(base: bytes, head: bytes, tol):
    import numpy as np  # optional dependency
    ab = np.load(io.BytesIO(base), allow_pickle=False)
    ah = np.load(io.BytesIO(head), allow_pickle=False)
    if ab.shape != ah.shape or ab.dtype != ah.dtype:
        return False, f"structure differs: {ab.dtype}{ab.shape} vs {ah.dtype}{ah.shape}"
    ok, worst = True, [0.0, 0.0, 0.0]
    parts = [(ab, ah)]
    if ab.dtype.kind == "c":
        parts = [(ab.real, ah.real), (ab.imag, ah.imag)]
    for pb, ph in parts:
        if pb.dtype.kind != "f" or pb.dtype.itemsize > 8:
            if not np.array_equal(pb, ph):
                bad = np.flatnonzero(pb.ravel() != ph.ravel())
                i = int(bad[0]) if bad.size else 0
                return False, f"differs exactly ({bad.size} elements, first at {i}: {pb.ravel()[i]!r} vs {ph.ravel()[i]!r})"
            continue
        sp = np.spacing(np.abs(pb)).astype(float).ravel().tolist()
        good, w = _floats(pb.astype(float).ravel().tolist(), ph.astype(float).ravel().tolist(), sp, tol)
        ok &= good
        worst = [max(a, b) for a, b in zip(worst, w)]
    return _verdict(ok, worst, tol)


def _text(base: bytes, head: bytes, tol):
    for data in (base, head):
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            return False, "binary file, not comparable"
    tb, th = NUMBER.findall(base), NUMBER.findall(head)
    if NUMBER.sub(b"#", base) != NUMBER.sub(b"#", head) or len(tb) != len(th):
        return False, f"differs in structure ({len(tb)} vs {len(th)} numbers)"
    kinds_b = [bool(INTEGER.fullmatch(t)) for t in tb]
    if kinds_b != [bool(INTEGER.fullmatch(t)) for t in th]:
        return False, "differs in structure (integer vs float token)"
    ok, worst = True, [0.0, 0.0, 0.0]
    fb, fh = [], []
    for is_int, b, h in zip(kinds_b, tb, th):
        if is_int:
            if int(b) != int(h):
                return False, f"integer differs: {b.decode()} vs {h.decode()}"
        else:
            fb.append(float(b))
            fh.append(float(h))
    good, w = _floats(fb, fh, [math.ulp(abs(x)) if math.isfinite(x) else math.inf for x in fb], tol)
    return _verdict(ok and good, w, tol)


def _verdict(ok: bool, worst, tol):
    detail = f"max abs {worst[0]:.3g}, rel {worst[1]:.3g}, ulp {worst[2]:.3g}"
    return ok, ("within tolerance: " if ok else "outside tolerance: " if tol else "not bitwise: ") + detail


def compare(base: bytes, head: bytes, name: str, tol: dict | None) -> tuple[bool, str]:
    """(ok, detail). tol None means bitwise; detail always reports the largest deviations."""
    unknown = sorted(set(tol or {}) - set(TOLERANCE_KEYS))
    if unknown:
        raise ValueError(f"unknown tolerance key(s) {unknown}; allowed: {list(TOLERANCE_KEYS)}")
    if base == head:
        return True, "bitwise equal"
    try:
        return _npy(base, head, tol) if name.endswith(".npy") else _text(base, head, tol)
    except Exception as exc:
        return False, f"differs (not comparable numerically: {exc})"
