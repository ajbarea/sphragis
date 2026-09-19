"""The weight-update geometry behind RQ2's weight-space probe."""

from __future__ import annotations

import importlib.util
import json
import struct
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "adapter_geometry.py"
_spec = importlib.util.spec_from_file_location("adapter_geometry", _SCRIPT)
assert _spec and _spec.loader
geometry = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(geometry)


def test_the_trace_identity_matches_the_explicit_update() -> None:
    rng = np.random.default_rng(0)
    a1, a2 = rng.normal(size=(4, 30)), rng.normal(size=(4, 30))
    b1, b2 = rng.normal(size=(20, 4)), rng.normal(size=(20, 4))
    explicit = float(np.sum((b1 @ a1) * (b2 @ a2)))
    assert geometry.update_inner(a1, b1, a2, b2) == pytest.approx(explicit)


def _write(path: Path, tensors: dict[str, np.ndarray]) -> None:
    header, blobs, offset = {}, [], 0
    for name, array in tensors.items():
        raw = array.astype(np.float32).tobytes()
        header[name] = {
            "dtype": "F32",
            "shape": list(array.shape),
            "data_offsets": [offset, offset + len(raw)],
        }
        blobs.append(raw)
        offset += len(raw)
    encoded = json.dumps({"__metadata__": {"format": "pt"}, **header}).encode()
    path.write_bytes(struct.pack("<Q", len(encoded)) + encoded + b"".join(blobs))


def test_a_safetensors_file_reads_back_exactly(tmp_path: Path) -> None:
    rng = np.random.default_rng(1)
    a, b = rng.normal(size=(2, 5)).astype(np.float32), rng.normal(size=(3, 2)).astype(np.float32)
    path = tmp_path / "adapter_model.safetensors"
    _write(path, {"m.lora_A.weight": a, "m.lora_B.weight": b})
    header, start = geometry.tensor_index(path)
    assert geometry.modules(header) == ["m"]
    assert np.array_equal(geometry.read_tensor(path, header, start, "m.lora_B.weight"), b)


def test_an_update_is_identical_to_itself_and_scale_does_not_matter(tmp_path: Path) -> None:
    rng = np.random.default_rng(2)
    a, b = rng.normal(size=(2, 6)), rng.normal(size=(4, 2))
    same = geometry.update_inner(a, b, a, b)
    scaled = geometry.update_inner(a, b, 3 * a, b)
    assert scaled / (same**0.5 * (9 * same) ** 0.5) == pytest.approx(1.0)


def test_one_factor_alone_is_its_own_frobenius_inner_product() -> None:
    """What a scheme that transmits only A gives an attacker to compare."""
    first = np.array([[1.0, 2.0], [3.0, 4.0]])
    second = np.array([[5.0, 6.0], [7.0, 8.0]])
    assert geometry.factor_inner(first, second) == pytest.approx(float((first * second).sum()))
    assert geometry.factor_inner(first, first) == pytest.approx(float((first**2).sum()))


def test_the_factor_inner_product_is_not_the_product_inner_product() -> None:
    """They answer different questions, so a run must say which one it computed."""
    rng = np.random.default_rng(0)
    a1, b1 = rng.normal(size=(4, 6)), rng.normal(size=(5, 4))
    a2, b2 = rng.normal(size=(4, 6)), rng.normal(size=(5, 4))
    assert geometry.update_inner(a1, b1, a2, b2) != pytest.approx(geometry.factor_inner(a1, a2))
    assert geometry.update_inner(a1, b1, a2, b2) == pytest.approx(
        float(((b1 @ a1) * (b2 @ a2)).sum())
    )
