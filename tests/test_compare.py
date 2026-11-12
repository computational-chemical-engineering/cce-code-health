import io
import math

import numpy as np
import pytest

from cce_code_health.compare import compare

INF = math.inf


def npy(a):
    buf = io.BytesIO()
    np.save(buf, a)
    return buf.getvalue()


@pytest.mark.parametrize("b,h", [("inf", "1.0"), ("inf", "-inf"), ("-inf", "inf"), ("1.0", "inf"),
                                 ("nan", "1.0"), ("1.0", "nan")])
def test_nonfinite_mismatch_fails_even_with_rel(b, h):
    assert not compare(f"x {b}\n".encode(), f"x {h}\n".encode(), "a.txt", {"rel": 0.1})[0]
    arr = lambda v: npy(np.array([float(v)]))  # noqa: E731
    assert not compare(arr(b), arr(h), "a.npy", {"rel": 0.1})[0]


def test_nonfinite_equal_passes():
    a = npy(np.array([np.nan, np.inf, -np.inf, 1.0]))
    b = npy(np.array([np.nan, np.inf, -np.inf, 1.0 + 1e-14]))
    assert compare(a, b, "a.npy", {"rel": 1e-9})[0]


def test_npy_shape_dtype_must_match():
    a = np.arange(6.0).reshape(2, 3)
    assert not compare(npy(a), npy(a.T.copy()), "a.npy", {"rel": 1})[0]
    ok, detail = compare(npy(a), npy(a.astype(np.float32)), "a.npy", {"rel": 1})
    assert not ok and "structure" in detail


def test_npy_complex_imag_part_compared():
    a = np.array([1 + 1j])
    assert not compare(npy(a), npy(np.array([1 + 1.5j])), "a.npy", {"rel": 0.1})[0]
    assert compare(npy(a), npy(np.array([1 + 1.01j])), "a.npy", {"rel": 0.1})[0]


def test_npy_int_exact():
    a, b = np.array([2**60], dtype=np.int64), np.array([2**60 + 1], dtype=np.int64)
    assert not compare(npy(a), npy(b), "a.npy", {"rel": 0.1})[0]
    assert not compare(npy(np.array([True])), npy(np.array([False])), "a.npy", {"rel": 1})[0]


def test_npy_ulp_in_own_dtype():
    a = np.array([1.0], dtype=np.float32)
    b = np.nextafter(a, np.float32(2))
    assert compare(npy(a), npy(b), "a.npy", {"ulp": 1})[0]
    assert not compare(npy(a), npy(np.nextafter(b, np.float32(2))), "a.npy", {"ulp": 1})[0]


def test_text_integers_exact_floats_tolerant():
    assert not compare(b"iters 12\n", b"iters 13\n", "o.txt", {"rel": 0.1})[0]
    assert compare(b"t 1.00\n", b"t 1.01\n", "o.txt", {"rel": 0.1})[0]
    assert not compare(b"n 12\n", b"n 12.0\n", "o.txt", {"rel": 0.1})[0]


def test_text_digits_in_identifiers_are_not_numbers():
    assert not compare(b"abc3456\n", b"abc3457\n", "o.txt", {"rel": 0.1})[0]
    assert not compare(b"sha deadbeef_12\n", b"sha deadbeef_13\n", "o.txt", {"abs": 5})[0]


def test_binary_file_not_comparable():
    ok, detail = compare(b"\xff\xfe\x00", b"\xff\xfe\x01", "o.bin", {"rel": 1})
    assert not ok and "binary file, not comparable" in detail
    assert compare(b"\xff", b"\xff", "o.bin", None)[0]


def test_unknown_tolerance_key_is_error():
    with pytest.raises(ValueError):
        compare(b"1.0", b"1.0", "o.txt", {"rtol": 1})
