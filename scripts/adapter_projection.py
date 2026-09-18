"""Each client update as a short vector, so defences can be simulated honestly.

The Gram matrix `adapter_geometry` records is enough for the attacks but not for a defence: to ask
what noise on an update costs an attacker, the noise must perturb the update's direction, and a
Gram matrix holds no directions to perturb. Adding the noise's squared norm to the diagonal
captures only its effect on the denominator, which would have said noise never helps.

A dense random projection of the flattened update is not affordable: one module's update is up to
3,584 x 18,944, and a projection matrix over it would need hundreds of gigabytes. The update is
low rank, dW = B A with r = 32, which a bilinear sketch exploits:

    S = (L B)(A R),   L of shape (d, out), R of shape (in, d), both iid N(0, 1/d),

costing two thin products. For any two updates E<S_X, S_Y> = <X, Y>, so inner products survive in
expectation, with a relative error of order sqrt(2)/d per module; the per-module sketches are
concatenated, which sums their inner products exactly as the true updates' do, and the errors
across modules are independent. The sketch is linear, so Gaussian noise added to an update is
Gaussian noise added to its sketch, which is what makes a defence curve possible at all.

    uv run --no-sync python scripts/adapter_projection.py --root ~/scratch \
        --pattern "sphragis-adapters-clients/*-c*/adapter_model.safetensors" \
        --sketch 16 --out ~/client-vectors.npz
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from sphragis.provenance import provenance_header, slurm_record

sys.path.insert(0, str(Path(__file__).resolve().parent))
from adapter_geometry import adapters, modules, read_tensor, tensor_index  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("--root", type=Path, required=True)
parser.add_argument("--pattern", default="sphragis-adapters-clients/*-c*/adapter_model.safetensors")
parser.add_argument("--sketch", type=int, default=16, help="each side of a module's sketch")
parser.add_argument("--seed", type=int, default=0)
parser.add_argument("--out", type=Path, required=True)


def sketch_module(a: np.ndarray, b: np.ndarray, width: int, seed: int) -> np.ndarray:
    """(L B)(A R) flattened: one module's update, sketched to `width` squared coordinates."""
    rng = np.random.default_rng(seed)
    left = rng.normal(0.0, 1.0 / np.sqrt(width), size=(width, b.shape[0]))
    right = rng.normal(0.0, 1.0 / np.sqrt(width), size=(a.shape[1], width))
    return ((left @ b) @ (a @ right)).reshape(-1)


def main() -> None:
    args = parser.parse_args()
    paths = adapters(args.root, args.pattern)
    if not paths:
        raise SystemExit(f"no adapters match {args.pattern} under {args.root}")
    names = list(paths)
    shared = modules(tensor_index(paths[names[0]])[0])
    vectors = np.zeros((len(names), len(shared) * args.sketch**2))
    for index, name in enumerate(names):
        header, start = tensor_index(paths[name])
        for position, module in enumerate(shared):
            a = read_tensor(paths[name], header, start, f"{module}.lora_A.weight")
            b = read_tensor(paths[name], header, start, f"{module}.lora_B.weight")
            piece = sketch_module(a, b, args.sketch, args.seed + position * 1_000_003)
            span = slice(position * args.sketch**2, (position + 1) * args.sketch**2)
            vectors[index, span] = piece
        print(f"{name}: sketched", flush=True)
    np.savez_compressed(
        args.out,
        vectors=vectors,
        names=np.array(names),
        meta=np.array(
            [
                json.dumps(
                    {
                        "sketch": args.sketch,
                        "dimension": len(shared) * args.sketch**2,
                        "seed": args.seed,
                        "modules": len(shared),
                        "provenance": {**provenance_header(), "slurm": slurm_record()},
                    }
                )
            ]
        ),
    )
    print(f"wrote {args.out}")
    print("ADAPTER_PROJECTION_OK")


if __name__ == "__main__":
    main()
