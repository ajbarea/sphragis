"""Why a mask can raise the attacker's AUC, and whether the fast rewrite is the same measurement."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "masking_mechanism.py"
_spec = importlib.util.spec_from_file_location("masking_mechanism", _SCRIPT)
assert _spec and _spec.loader
mechanism = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mechanism)


def _gathered(vectors, *, members, outside, size, rounds, draws, rng):
    """The difference built by gathering rows, which the count-vector version replaced."""
    out = []
    for _ in range(draws):
        held, without = [], []
        for _ in range(rounds):
            if members is not None:
                chosen = np.concatenate(
                    [rng.choice(members, 1), rng.choice(outside, size - 1, replace=False)]
                )
            else:
                chosen = rng.choice(outside, size, replace=False)
            held.append(vectors[chosen].mean(axis=0))
            without.append(vectors[rng.choice(outside, size, replace=False)].mean(axis=0))
        out.append(np.mean(held, axis=0) - np.mean(without, axis=0))
    return np.array(out)


@pytest.mark.parametrize("members_given", [True, False])
def test_the_count_vector_rewrite_is_the_row_gathering_measurement(members_given: bool) -> None:
    """Both draw from the same stream in the same order, so they must agree to floating point."""
    vectors = np.random.default_rng(0).normal(size=(12, 40))
    members, outside = np.array([0, 1, 2]), np.arange(3, 12)
    kwargs = dict(
        members=members if members_given else None, outside=outside, size=4, rounds=3, draws=5
    )
    fast = mechanism.differences(vectors, mask_sd=0.0, rng=np.random.default_rng(7), **kwargs)
    slow = _gathered(vectors, rng=np.random.default_rng(7), **kwargs)
    assert np.allclose(fast, slow, atol=1e-12)


def test_a_mask_deflates_the_null_class_more_once_rounds_average_the_sampling_away() -> None:
    """The mechanism behind a defence that helps the attacker, stated as a testable ordering."""
    rng = np.random.default_rng(1)
    shared = rng.normal(size=128)
    shared /= np.linalg.norm(shared)
    vectors = np.vstack([rng.normal(size=(4, 128)) + 8.0 * shared, rng.normal(size=(20, 128))])
    members, outside = np.arange(4), np.arange(4, 24)
    lengths = {}
    for rounds in (1, 100):
        for name, who in (("present", members), ("absent", None)):
            drawn = mechanism.differences(
                vectors,
                members=who,
                outside=outside,
                size=8,
                rounds=rounds,
                mask_sd=0.0,
                draws=40,
                rng=np.random.default_rng(2),
            )
            lengths[name, rounds] = float(np.linalg.norm(drawn, axis=1).mean())
    # At one round both classes' differences are dominated by which outsiders were drawn; after
    # a hundred that term averages down and only the member class keeps a length of its own.
    assert lengths["present", 1] / lengths["absent", 1] < 1.3
    assert lengths["present", 100] / lengths["absent", 100] > 2.0
