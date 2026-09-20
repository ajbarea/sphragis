"""What masking an update with noise costs the attacker, and what it does not cost.

The attacks say a round's aggregate betrays whose clients were in it. The obvious defence is the
one federated deployments already use: each client masks its update with Gaussian noise before it
is aggregated, as DP-SGD and its relatives do. This measures what that buys, on the sketched
updates (`scripts/adapter_projection.py`), where noise perturbs a direction rather than a norm.

Two readings, because they answer different questions:

  single round   the attacker sees one round's aggregate. Noise is drawn afresh per client per
                 round, so this is the defence at its strongest.
  many rounds    the attacker averages the aggregates of rounds holding the target, subtracts
                 the average of rounds without it, and scores that difference: FedAttr's
                 mechanism, where the subtraction cancels the outsiders both sets share. Fresh
                 noise averages away over rounds and the target's direction does not, so a
                 per-round mask is a delay rather than a defence unless the budget composes.
                 The updates replayed are one round's, so this measures the arithmetic of
                 averaging, not a source's persistence across a moving global model.

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

from sphragis.measure.aggregate import tpr_at_fpr
from sphragis.measure.attribution import Source

parser = argparse.ArgumentParser()
parser.add_argument("--vectors", type=Path, required=True)
parser.add_argument("--clients", type=Path, required=True)
parser.add_argument("--noise", type=float, nargs="+", default=[0.0, 0.5, 1.0, 2.0, 4.0, 8.0])
parser.add_argument("--round-size", type=int, default=8)
parser.add_argument("--rounds", type=int, nargs="+", default=[1, 10, 100])
parser.add_argument("--draws", type=int, default=300)
parser.add_argument("--seed", type=int, default=0)
parser.add_argument("--splits", type=int, default=4, help="reference splits averaged over")
parser.add_argument(
    "--content",
    default=None,
    help="restrict every client to one content type, so the reference direction cannot be the "
    "language rather than the organization",
)
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
) -> dict[str, float]:
    """AUC of FedAttr's statistic after `rounds` observations, at a noise-to-signal ratio.

    Each participant's update is masked with an independent Gaussian of the given fraction of the
    mean update norm, drawn afresh for every round. The attacker averages the aggregates of rounds
    holding the target, subtracts the average of rounds without it, and scores that difference
    against its reference direction. The subtraction is what cancels the outsiders the two sets
    share; averaging the per-round cosines instead would keep the noise's attenuation, since a
    cosine is not linear in it.

    A trial of the absent class differences two target-free sets, so the classes differ in the
    target's presence and in nothing else.

    What `rounds` means here: the client updates are one round's, replayed, so the target's
    direction is identical in every round by construction and only the masks and the participants
    are redrawn. A real attacker watching a training run sees rounds separated in time, across
    which the global adapter moves and each client's update starts from a different point; whether
    a source's direction persists across that is a question this design cannot answer.
    """
    scale = noise * float(np.linalg.norm(vectors, axis=1).mean())
    direction = vectors[reference].mean(axis=0)
    width = vectors.shape[1]

    # The mean of `size` independent masks is one Gaussian of the same shape with its variance
    # divided by `size`, so it is drawn directly: exact, and it avoids drawing size x width
    # normals for every round, which dominated the cost.
    mask_sd = scale / np.sqrt(width * size)

    def aggregate(with_target: bool) -> np.ndarray:
        if with_target:
            participants = np.concatenate(
                [rng.choice(members, 1), rng.choice(outside, size - 1, replace=False)]
            )
        else:
            participants = rng.choice(outside, size, replace=False)
        return vectors[participants].mean(axis=0) + rng.normal(0.0, mask_sd, width)

    def difference(with_target: bool) -> float:
        held = sum(aggregate(with_target) for _ in range(rounds)) / rounds
        without = sum(aggregate(False) for _ in range(rounds)) / rounds
        return cosine(held - without, direction)

    present = [difference(True) for _ in range(draws)]
    absent = [difference(False) for _ in range(draws)]
    # An AUC averages over false-positive rates no attacker would operate at, and a defence can
    # look effective there while leaving the confident identifications intact (Carlini et al.,
    # IEEE S&P 2022). The curve is read at both.
    point = tpr_at_fpr(present, absent, 0.01)
    return {
        "auc": auc(present, absent),
        "tpr_at_1pct_fpr": point["tpr"],
        "fpr_achieved_at_1pct": point["fpr_achieved"],
        "mean_present": float(np.mean(present)),
        "mean_absent": float(np.mean(absent)),
    }


def main() -> None:
    args = parser.parse_args()
    from sphragis.provenance import provenance_header

    loaded = np.load(args.vectors, allow_pickle=False)
    vectors = loaded["vectors"]
    names = [str(n).split("/", 1)[1] for n in loaded["names"]]
    clients = json.loads(args.clients.read_text())["clients"]
    sources = [Source.parse(clients[n]["source"]) for n in names]
    if args.content:
        # Pooled over content, a target's reference direction is its dominant language, and any
        # participant writing that language scores like a member. Measured on the 77-client set:
        # Qt's held-out clients sat 0.0024 BELOW outsiders in cosine with Qt's own reference,
        # because AOSP's C++ clients are as close to a C++ direction as Qt's are, and the curve
        # came out under 0.5 at every noise level.
        keep = [i for i, s in enumerate(sources) if s.at("content") == args.content]
        if len(keep) < 2:
            raise SystemExit(f"{len(keep)} clients write {args.content}")
        vectors = vectors[keep]
        sources = [sources[i] for i in keep]
        print(f"{len(keep)} clients write {args.content}", flush=True)
    rng = np.random.default_rng(args.seed)

    report: dict = {
        "content": args.content,
        "round_size": args.round_size,
        "draws": args.draws,
        "dimension": int(vectors.shape[1]),
        "provenance": provenance_header(),
        "targets": {},
    }
    groups = [s.at("project") for s in sources]
    for organization in sorted({s.organization for s in sources}):
        mine = np.array([i for i, s in enumerate(sources) if s.organization == organization])
        outside = np.array([i for i, s in enumerate(sources) if s.organization != organization])
        if len(mine) < 2 or len(outside) < args.round_size:
            continue
        half = max(1, len(mine) // 2)
        cell: dict[str, dict[str, dict[str, float]]] = {}
        for rounds in args.rounds:
            for noise in args.noise:
                scores = []
                for split in range(args.splits):
                    # The reference is split over PROJECTS, as the aggregate attack's detector
                    # already was. A random split over clients puts a target's own project on
                    # both sides, and for an organization whose projects are less alike than
                    # they are like an outsider's it runs the detector backwards: measured
                    # within C++, Qt's clients sit at a mean cosine of 0.2534 with each other
                    # against 0.2589 with AOSP's, and the curve read below chance at every
                    # noise level. The split stream is separate from the round stream, so which
                    # clients are the reference does not decide which rounds are drawn.
                    splitter = np.random.default_rng(args.seed + split)
                    names_here = sorted({groups[i] for i in mine})
                    if len(names_here) >= 2:
                        order = splitter.permutation(len(names_here))
                        cut = max(1, len(names_here) // 2)
                        held = {names_here[k] for k in order[:cut]}
                        reference = np.array([i for i in mine if groups[i] in held])
                        participants = np.array([i for i in mine if groups[i] not in held])
                    else:
                        shuffled = splitter.permutation(mine)
                        reference, participants = shuffled[:half], shuffled[half:]
                    if not len(reference) or not len(participants):
                        continue
                    # A fresh generator per cell, so cells differ in rounds and noise and not in
                    # which rounds were drawn, and the same number of draws throughout.
                    rng = np.random.default_rng(args.seed + 7919 * rounds + 104729 * split)
                    scores.append(
                        masked_rounds(
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
                    )
                aucs = [s["auc"] for s in scores]
                summary = {
                    "auc": float(np.mean(aucs)),
                    "tpr_at_1pct_fpr": float(np.mean([s["tpr_at_1pct_fpr"] for s in scores])),
                    "auc_sd_over_splits": float(np.std(aucs, ddof=1)) if len(aucs) > 1 else 0.0,
                    "splits": float(args.splits),
                    "mean_present": float(np.mean([s["mean_present"] for s in scores])),
                    "mean_absent": float(np.mean([s["mean_absent"] for s in scores])),
                }
                cell.setdefault(str(rounds), {})[str(noise)] = summary
                # Written as each cell lands: the whole grid runs for hours, and a run that
                # writes only at the end has nothing to show for an interruption.
                report["targets"][organization] = cell
                args.out.write_text(json.dumps(report, indent=2))
                print(
                    f"{organization:12} {rounds:4} rounds, noise {noise:4}: "
                    f"AUC {summary['auc']:.3f} (sd {summary['auc_sd_over_splits']:.3f}), "
                    f"TPR at 1% FPR {summary['tpr_at_1pct_fpr']:.3f}",
                    flush=True,
                )
        report["targets"][organization] = cell
    args.out.write_text(json.dumps(report, indent=2))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
