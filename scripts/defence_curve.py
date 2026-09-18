"""What masking an update with noise costs the attacker, and what it does not cost.

The attacks say a round's aggregate betrays whose clients were in it. The obvious defence is the
one federated deployments already use: each client masks its update with Gaussian noise before it
is aggregated, as DP-SGD and its relatives do. This measures what that buys, on the sketched
updates (`scripts/adapter_projection.py`), where noise perturbs a direction rather than a norm.

Two readings, because they answer different questions:

  single round   the attacker sees one round's aggregate. Noise is drawn afresh per client per
                 round, so this is the defence at its strongest.
  many rounds    the attacker averages the AGGREGATES of rounds holding the target, subtracts
                 the average of rounds without it, and scores that difference, which is
                 FedAttr's mechanism. Averaging per-round scores instead would not do: a cosine
                 is nonlinear in the noise, so its attenuation survives averaging while the
                 aggregates' noise does not. Fresh noise averages away over rounds and the
                 target's direction does not, so a per-round mask is a delay rather than a
                 defence unless the budget composes across rounds.

    uv run --no-sync --no-active python scripts/defence_curve.py \
        --vectors datasets/results/client-vectors.npz \
        --clients datasets/results/client-updates.json \
        --out datasets/results/defence-curve.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from sphragis.measure.attribution import Source

parser = argparse.ArgumentParser()
parser.add_argument("--vectors", type=Path, required=True)
parser.add_argument("--clients", type=Path, required=True)
parser.add_argument("--noise", type=float, nargs="+", default=[0.0, 0.5, 1.0, 2.0, 4.0, 8.0])
parser.add_argument("--round-size", type=int, default=8)
parser.add_argument("--rounds", type=int, nargs="+", default=[1, 10, 100])
parser.add_argument("--draws", type=int, default=300)
parser.add_argument("--seed", type=int, default=0)
parser.add_argument("--out", type=Path, required=True)


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(a @ b / denominator) if denominator > 0 else 0.0


def auc(present: list[float], absent: list[float]) -> float:
    wins = sum(1.0 if x > y else 0.5 if x == y else 0.0 for x in present for y in absent)
    return wins / (len(present) * len(absent))


def masked_rounds(
    vectors: np.ndarray,
    *,
    members: np.ndarray,
    outside: np.ndarray,
    reference: np.ndarray,
    size: int,
    rounds: int,
    noise: float,
    draws: int,
    rng: np.random.Generator,
) -> float:
    """AUC of the attacker's score after `rounds` observations, at a noise-to-signal ratio.

    Each participant's update is masked with an independent Gaussian of the given fraction of the
    mean update norm, drawn afresh for every round. The attacker averages the aggregates it sees
    and scores the average, which is what makes per-round noise a delay: the masks average away,
    the target's direction does not. Averaging the per-round scores instead would keep the
    attenuation, since a cosine is not linear in the noise.
    """
    scale = noise * float(np.linalg.norm(vectors, axis=1).mean())
    direction = vectors[reference].mean(axis=0)
    width = vectors.shape[1]

    def observed(with_target: bool) -> float:
        total = np.zeros(width)
        for _ in range(rounds):
            if with_target:
                participants = np.concatenate(
                    [rng.choice(members, 1), rng.choice(outside, size - 1, replace=False)]
                )
            else:
                participants = rng.choice(outside, size, replace=False)
            mask = rng.normal(0.0, scale / np.sqrt(width), (size, width))
            total += (vectors[participants] + mask).mean(axis=0)
        return cosine(total / rounds, direction)

    present = [observed(True) for _ in range(draws)]
    absent = [observed(False) for _ in range(draws)]
    return auc(present, absent)


def main() -> None:
    args = parser.parse_args()
    from sphragis.provenance import provenance_header

    loaded = np.load(args.vectors, allow_pickle=False)
    vectors = loaded["vectors"]
    names = [str(n).split("/", 1)[1] for n in loaded["names"]]
    clients = json.loads(args.clients.read_text())["clients"]
    sources = [Source.parse(clients[n]["source"]) for n in names]
    rng = np.random.default_rng(args.seed)

    report: dict = {
        "round_size": args.round_size,
        "draws": args.draws,
        "dimension": int(vectors.shape[1]),
        "provenance": provenance_header(),
        "targets": {},
    }
    for organization in sorted({s.organization for s in sources}):
        mine = np.array([i for i, s in enumerate(sources) if s.organization == organization])
        outside = np.array([i for i, s in enumerate(sources) if s.organization != organization])
        if len(mine) < 2 or len(outside) < args.round_size:
            continue
        half = max(1, len(mine) // 2)
        # At random: clients arrive grouped by project, and an ordered split hands a
        # multi-project organization a reference from other projects than its participants.
        shuffled = np.random.default_rng(args.seed).permutation(mine)
        reference, participants = shuffled[:half], shuffled[half:]
        cell: dict[str, dict[str, float]] = {}
        for rounds in args.rounds:
            for noise in args.noise:
                # A fresh generator per cell, so cells differ in rounds and noise and not in
                # which rounds were drawn, and the same number of draws throughout.
                rng = np.random.default_rng(args.seed + 7919 * rounds)
                value = masked_rounds(
                    vectors,
                    members=participants,
                    outside=outside,
                    reference=reference,
                    size=args.round_size,
                    rounds=rounds,
                    noise=noise,
                    draws=args.draws,
                    rng=rng,
                )
                cell.setdefault(str(rounds), {})[str(noise)] = value
                print(
                    f"{organization:12} {rounds:4} rounds, noise {noise:4}: AUC {value:.3f}",
                    flush=True,
                )
        report["targets"][organization] = cell
    args.out.write_text(json.dumps(report, indent=2))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
