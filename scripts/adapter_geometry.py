"""How alike are two LoRA updates, and is it the data or the initialization that decides?

RQ2 asks what an adapter update reveals about where its data came from. In federated
fine-tuning every client starts a round from the same global adapter, so the realistic
comparison is between updates that share an initialization: here, adapters trained at the
same seed. Weight-space provenance is established for the training objective (Paul,
arXiv:2604.08844, a logistic classifier on spectral features at AUC 1.00), which leaves
"organic drift from distributional shift" untested: the same objective on different data,
which is exactly an organization's update against another's.

For every pair of adapters and every adapted module this computes the cosine between the two
weight updates dW = (alpha / r) B A without forming dW, which for a 7B model is about 26 GB per
adapter. The Frobenius inner product is a trace over r x r matrices,

    <B1 A1, B2 A2> = tr((B1^T B2) (A2 A1^T)),

and the scaling cancels in a cosine. Tensors are read one module at a time through memory
maps of the safetensors files, so memory holds one module across every adapter.

    uv run --no-sync python scripts/adapter_geometry.py --root ~/scratch --out geometry.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path
from typing import Any

import numpy as np

from sphragis.provenance import provenance_header, slurm_record

parser = argparse.ArgumentParser()
parser.add_argument("--root", type=Path, required=True, help="holds sphragis-adapters* dirs")
parser.add_argument("--out", type=Path, required=True)
parser.add_argument(
    "--matrices",
    default="product",
    choices=("product", "a", "b"),
    help="which part of the update the geometry is over: the product B A that changes the "
    "weights, or one factor alone, which is what a selective-sharing scheme transmits",
)
parser.add_argument(
    "--subtract-init",
    action="store_true",
    help="compare each factor against the broadcast initial adapter rather than against zero, "
    "which is the movement a server actually observes in a matrix it sent out",
)
parser.add_argument(
    "--pattern",
    default="sphragis-adapters*/*/adapter_model.safetensors",
    help="which adapters under the root, as a glob",
)
parser.add_argument(
    "--per-module", action="store_true", help="also save every module's cosine matrix"
)

_DTYPES = {"F32": np.float32, "F16": np.float16}


def tensor_index(path: Path) -> tuple[dict[str, Any], int]:
    """The safetensors header, and where the data section starts."""
    with path.open("rb") as handle:
        (length,) = struct.unpack("<Q", handle.read(8))
        header = json.loads(handle.read(length))
    header.pop("__metadata__", None)
    return header, 8 + length


def read_tensor(path: Path, header: dict[str, Any], start: int, name: str) -> np.ndarray:
    entry = header[name]
    if entry["dtype"] not in _DTYPES:
        raise ValueError(f"{path}: {name} is {entry['dtype']}, expected one of {list(_DTYPES)}")
    begin, end = entry["data_offsets"]
    flat = np.memmap(path, dtype=_DTYPES[entry["dtype"]], mode="r", offset=start + begin)
    count = int(np.prod(entry["shape"]))
    if (end - begin) != count * flat.itemsize:
        raise ValueError(f"{path}: {name} has {end - begin} bytes for shape {entry['shape']}")
    return np.asarray(flat[:count], dtype=np.float64).reshape(entry["shape"])


def update_inner(a1: np.ndarray, b1: np.ndarray, a2: np.ndarray, b2: np.ndarray) -> float:
    """<B1 A1, B2 A2>, Frobenius, computed through r x r matrices only."""
    return float(np.trace((b1.T @ b2) @ (a2 @ a1.T)))


def factor_inner(first: np.ndarray, second: np.ndarray) -> float:
    """<X1, X2>, Frobenius, for one LoRA factor on its own.

    FedSA-LoRA (Guo et al., ICLR 2025, arXiv:2410.01463) shares only the A matrices, on the
    finding that "A matrices are responsible for learning general knowledge, while B matrices
    focus on capturing client-specific knowledge". Whether the transmitted factor still carries
    the source is a measurement over exactly this inner product.
    """
    return float(np.tensordot(first, second, axes=2))


def modules(header: dict[str, Any]) -> list[str]:
    """The adapted modules, as the prefix before `.lora_A.weight`."""
    return sorted(k.removesuffix(".lora_A.weight") for k in header if k.endswith(".lora_A.weight"))


def initial(paths: dict[str, Path]) -> Path:
    """The one initial adapter every matched client started from.

    Each client's run directory saves its broadcast starting state as `init` beside the clients,
    so the init is found from the adapters themselves rather than by rewriting the glob, which
    could land on another run's `init`. Clients from more than one run would have started from
    more than one state, and there is then no single movement to measure, so that is refused.
    """
    found = {path.parent.parent / "init" / path.name for path in paths.values()}
    missing = sorted(str(p) for p in found if not p.is_file())
    if missing:
        raise SystemExit(f"--subtract-init needs each run's initial adapter; missing {missing}")
    if len(found) != 1:
        raise SystemExit(
            f"matched clients come from {len(found)} runs with different initial states"
        )
    return found.pop()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def adapters(root: Path, pattern: str) -> dict[str, Path]:
    """Every saved adapter the pattern matches, named `<run dir suffix>/<adapter>`."""
    found = {}
    for path in sorted(root.glob(pattern)):
        # client_updates saves the shared initial state as `init`; its B is zero, so it is the
        # starting point of every update rather than an update.
        if path.parent.name == "init":
            continue
        run = path.parent.parent.name.removeprefix("sphragis-adapters").lstrip("-") or "first"
        found[f"{run}/{path.parent.name}"] = path
    return found


def main() -> None:
    args = parser.parse_args()
    paths = adapters(args.root, args.pattern)
    if len(paths) < 2:
        raise SystemExit(f"{len(paths)} adapters match {args.pattern} under {args.root}")
    names = list(paths)
    indexes = {name: tensor_index(path) for name, path in paths.items()}
    shared = modules(indexes[names[0]][0])
    for name in names:
        if modules(indexes[name][0]) != shared:
            raise SystemExit(f"{name} adapts different modules from {names[0]}")
    n = len(names)
    inner = np.zeros((n, n))
    per_module_cosines = np.zeros((n, n))
    by_module: dict[str, list[list[float]]] = {}
    if args.subtract_init and args.matrices == "product":
        # Subtracting the initial A from one factor gives B (A - A0), not the update B A - B0 A0:
        # measured, the two differ in cosine by up to 0.236. The initial B is zero, so the product
        # already is the movement and there is nothing to subtract.
        raise SystemExit("--subtract-init applies to --matrices a or b, not to the product")
    start_path = initial(paths) if args.subtract_init else None
    start_index = tensor_index(start_path) if start_path else None
    for module in shared:
        origin = {"a": 0.0, "b": 0.0}
        if start_index is not None:
            head, offset = start_index
            for key in ("a", "b"):
                origin[key] = read_tensor(
                    start_path, head, offset, f"{module}.lora_{key.upper()}.weight"
                )
        loaded = []
        for name in names:
            header, start = indexes[name]
            a = read_tensor(paths[name], header, start, f"{module}.lora_A.weight") - origin["a"]
            b = read_tensor(paths[name], header, start, f"{module}.lora_B.weight") - origin["b"]
            loaded.append((a, b))
        if args.matrices == "product":
            block = np.array(
                [[update_inner(*loaded[i], *loaded[j]) for j in range(n)] for i in range(n)]
            )
        else:
            which = 0 if args.matrices == "a" else 1
            factors = [pair[which] for pair in loaded]
            block = np.array(
                [[factor_inner(factors[i], factors[j]) for j in range(n)] for i in range(n)]
            )
        inner += block
        norms = np.sqrt(np.clip(np.diag(block), 0.0, None))
        with np.errstate(divide="ignore", invalid="ignore"):
            module_cosine = np.nan_to_num(block / np.outer(norms, norms))
        per_module_cosines += module_cosine
        if args.per_module:
            by_module[module] = np.round(module_cosine, 6).tolist()
        print(f"{module}: done", flush=True)
    norms = np.sqrt(np.diag(inner))
    if not np.all(norms > 0):
        zero = [name for name, norm in zip(names, norms, strict=True) if not norm > 0]
        raise SystemExit(f"updates with zero norm have no direction: {zero}")
    args.out.write_text(
        json.dumps(
            {
                "adapters": names,
                "matrices": args.matrices,
                "subtract_init": bool(args.subtract_init),
                "initial_adapter": str(start_path) if start_path else None,
                "initial_adapter_sha256": sha256(start_path) if start_path else None,
                "modules": len(shared),
                "update_norm": dict(zip(names, norms.tolist(), strict=True)),
                "cosine": (inner / np.outer(norms, norms)).tolist(),
                "mean_module_cosine": (per_module_cosines / len(shared)).tolist(),
                **({"by_module": by_module} if args.per_module else {}),
                "provenance": {**provenance_header(), "slurm": slurm_record()},
            },
            indent=2,
        )
    )
    print(f"wrote {args.out}")
    print("ADAPTER_GEOMETRY_OK")


if __name__ == "__main__":
    main()
