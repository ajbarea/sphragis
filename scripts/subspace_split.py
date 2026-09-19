"""Would keeping the client-specific subspace local take the fingerprint with it?

The defence curve says masking is the wrong lever: a cosine detector is scale free, so noise can
leave the confident identifications intact and at some sizes help the attacker. The literature's
other lever is structural. SDFLoRA (Shen, Lu, Wan and Chen, arXiv:2601.11219) decouples each
client's adapter into a shared component that "participates in subspace alignment and aggregation
across clients" and a private component that "remains local and uncommunicated", and injects DP
noise only into the aggregated shared update, which "avoids perturbations to local directions".

That defence removes the attacker's access to whatever lives outside the shared subspace. So it
closes this study's leak exactly when the organization is legible in the *residual* and not in the
shared subspace, and it does nothing when the organization is legible in the part that is
communicated anyway. Which it is, is a measurement, not an argument.

Each client's sketched update is split at a rank k:

  shared      its projection onto the top-k principal subspace of all clients' updates, which is
              what an alignment step across clients recovers. An approximation of SDFLoRA's
              construction, and the friendly one to the defence: a subspace fitted to everybody
              is the most "common" part there is.
  residual    what is left, which under that defence never leaves the organization.

Both halves are then read by the same two instruments the study uses elsewhere -- nearest-class
attribution over clients, and the aggregate detector with its reference split over projects.

    uv run --no-sync --no-active python scripts/subspace_split.py \
        --vectors datasets/results/client-vectors-cpp-early.npz \
        --clients datasets/results/client-updates-cpp-early.json \
        --content cpp --out datasets/results/subspace-split.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from sphragis.measure.aggregate import gram, organization_membership_auc
from sphragis.measure.attribution import Source, accuracy
from sphragis.provenance import provenance_header

parser = argparse.ArgumentParser()
parser.add_argument("--vectors", type=Path, required=True)
parser.add_argument("--clients", type=Path, required=True)
parser.add_argument("--content", default=None, help="hold content fixed, as the attacks do")
parser.add_argument("--ranks", type=int, nargs="+", default=[1, 2, 4, 8, 16, 32])
parser.add_argument("--round-size", type=int, default=4)
parser.add_argument("--draws", type=int, default=400)
parser.add_argument("--splits", type=int, default=8)
parser.add_argument("--seed", type=int, default=0)
parser.add_argument("--out", type=Path, required=True)


def cosines(vectors: np.ndarray) -> list[list[float]]:
    norms = np.linalg.norm(vectors, axis=1)
    safe = np.where(norms > 0, norms, 1.0)
    unit = vectors / safe[:, None]
    return (unit @ unit.T).tolist()


def honest_attribution(
    vectors: np.ndarray, labels: list[str], projects: list[str], rank: int, residual: bool
) -> float:
    """Leave-one-out attribution with the subspace refitted without the client being scored.

    Fitting the basis on every client and then holding one out scores that client against a
    subspace its own update helped define. The server really does hold every update, so the
    basis is fair game for the detector, but a leave-one-out accuracy has to leave the client
    out of everything, the basis included, or it is quoting a number built partly from the
    answer.
    """
    scores = []
    for i in range(len(vectors)):
        others = np.delete(vectors, i, axis=0)
        basis = np.linalg.svd(others, full_matrices=False)[2][:rank]
        onto = (vectors @ basis.T) @ basis
        space = (vectors - onto) if residual else onto
        pool = cosines(space)
        best, chosen = None, None
        for label in sorted(set(labels)):
            aligned = [
                pool[i][j]
                for j in range(len(vectors))
                if j != i and labels[j] == label and projects[j] != projects[i]
            ]
            if not aligned:
                continue
            value = sum(aligned) / len(aligned)
            if best is None or value > best:
                best, chosen = value, label
        scores.append(1.0 if chosen == labels[i] else 0.0)
    return float(np.mean(scores))


def read(vectors: np.ndarray, sources: list[Source], args: argparse.Namespace) -> dict[str, float]:
    """Both instruments on one half of the split."""
    norms = [float(n) for n in np.linalg.norm(vectors, axis=1)]
    products = gram(cosines(vectors), norms)
    labels = [s.organization for s in sources]
    projects = [s.at("project") for s in sources]

    out: dict[str, float] = {}
    out["organization_beyond_project"] = accuracy(cosines(vectors), labels, projects)
    for organization in sorted(set(labels)):
        members = [i for i, name in enumerate(labels) if name == organization]
        outside = len(labels) - len(members)
        if len(members) < 2 or outside < args.round_size:
            continue
        if len({projects[i] for i in members}) < 2:
            continue
        scored = organization_membership_auc(
            products,
            members=members,
            everyone=range(len(labels)),
            size=args.round_size,
            draws=args.draws,
            seed=args.seed,
            at_least=1,
            splits=args.splits,
            groups=projects,
        )
        out[f"{organization}_detector_auc"] = scored["auc"]
        out[f"{organization}_detector_tpr_at_1pct"] = scored["tpr_at_1pct_fpr"]
    return out


def main() -> None:
    args = parser.parse_args()
    loaded = np.load(args.vectors, allow_pickle=False)
    vectors = loaded["vectors"]
    names = [str(n).split("/", 1)[1] for n in loaded["names"]]
    clients = json.loads(args.clients.read_text())["clients"]
    sources = [Source.parse(clients[n]["source"]) for n in names]
    if args.content:
        keep = [i for i, s in enumerate(sources) if s.at("content") == args.content]
        vectors, sources = vectors[keep], [sources[i] for i in keep]
        print(f"{len(keep)} clients write {args.content}", flush=True)

    # The subspace is fitted to the updates as they are, uncentred: an aggregation step averages
    # the updates themselves, so the direction every client shares is part of what alignment
    # finds, and removing the mean first would hand the defence a subspace no server computes.
    _, singular, right = np.linalg.svd(vectors, full_matrices=False)
    energy = float((singular**2).sum())

    report: dict = {
        "content": args.content,
        "clients": len(sources),
        "dimension": int(vectors.shape[1]),
        "round_size": args.round_size,
        "provenance": provenance_header(),
        "whole": read(vectors, sources, args),
        "ranks": {},
    }
    print(f"whole update: {report['whole']}", flush=True)
    for rank in args.ranks:
        if rank >= min(vectors.shape):
            continue
        basis = right[:rank]
        shared = (vectors @ basis.T) @ basis
        residual = vectors - shared
        labels = [s.organization for s in sources]
        projects = [s.at("project") for s in sources]
        cell = {
            "energy_in_shared": float((singular[:rank] ** 2).sum() / energy),
            "shared": read(shared, sources, args),
            "residual": read(residual, sources, args),
            "shared_refit_per_target": honest_attribution(
                vectors, labels, projects, rank, residual=False
            ),
            "residual_refit_per_target": honest_attribution(
                vectors, labels, projects, rank, residual=True
            ),
        }
        report["ranks"][str(rank)] = cell
        print(
            f"rank {rank:3}: {cell['energy_in_shared'] * 100:5.1f}% energy shared | "
            f"attribution shared {cell['shared']['organization_beyond_project']:.3f} "
            f"(refit {cell['shared_refit_per_target']:.3f}) "
            f"residual {cell['residual']['organization_beyond_project']:.3f} "
            f"(refit {cell['residual_refit_per_target']:.3f}) | "
            f"detector aosp {cell['shared'].get('aosp_detector_auc', float('nan')):.3f} "
            f"qt {cell['shared'].get('qt_detector_auc', float('nan')):.3f}",
            flush=True,
        )
        args.out.write_text(json.dumps(report, indent=2))
    args.out.write_text(json.dumps(report, indent=2))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
