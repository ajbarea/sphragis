"""The bilinear sketch RQ2's defence simulation needs: inner products survive it."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "adapter_projection.py"
_spec = importlib.util.spec_from_file_location("adapter_projection", _SCRIPT)
assert _spec and _spec.loader
projection = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(projection)


def _pair(rng, out: int, inner: int, rank: int = 4):
    return rng.normal(size=(rank, inner)), rng.normal(size=(out, rank))


def test_the_sketch_preserves_inner_products_across_modules() -> None:
    """One module's sketch is noisy; the concatenation over many is what the attack reads."""
    rng = np.random.default_rng(0)
    truth, sketched = 0.0, 0.0
    for module in range(60):
        # Correlated updates, as two clients' are: independent ones have a true inner product
        # near zero, which no relative error statement can be made about.
        a1, b1 = _pair(rng, 40, 90)
        a2 = a1 + 0.4 * rng.normal(size=a1.shape)
        b2 = b1 + 0.4 * rng.normal(size=b1.shape)
        truth += float(np.sum((b1 @ a1) * (b2 @ a2)))
        first = projection.sketch_module(a1, b1, 16, seed=module)
        second = projection.sketch_module(a2, b2, 16, seed=module)
        sketched += float(first @ second)
    assert sketched == pytest.approx(truth, rel=0.25)


def test_a_sketch_preserves_an_update_s_own_norm() -> None:
    rng = np.random.default_rng(1)
    a, b = _pair(rng, 60, 120)
    truth = float(np.sum((b @ a) ** 2))
    sketched = [
        float(projection.sketch_module(a, b, 16, seed=s) ** 2 @ np.ones(256)) for s in range(40)
    ]
    assert float(np.mean(sketched)) == pytest.approx(truth, rel=0.15)


def test_two_updates_must_meet_the_same_projection() -> None:
    """A different seed is a different space: the sketch only compares within one."""
    rng = np.random.default_rng(2)
    a, b = _pair(rng, 40, 90)
    same = projection.sketch_module(a, b, 16, seed=3) @ projection.sketch_module(a, b, 16, seed=3)
    crossed = projection.sketch_module(a, b, 16, seed=3) @ projection.sketch_module(
        a, b, 16, seed=4
    )
    assert same > 0
    assert abs(crossed) < same
