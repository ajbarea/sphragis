"""Would keeping the client-specific subspace local take the house style with it?

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
from itertools import combinations
from pathlib import Path

import numpy as np

from sphragis.measure.aggregate import gram, organization_membership_auc
from sphragis.measure.attribution import Source
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


def held_out_rows(
    vectors: np.ndarray, projects: list[str], rank: int, residual: bool
) -> np.ndarray | None:
    """Each client's cosine row in a subspace fitted without its whole project.

    Leaving out only the scored client still fits the basis to its project siblings, and the
    attribution below never compares a client with its own project, so the basis leaves the
    project out too. The rows depend on the updates alone, never on the labels, so one set of
    rows serves the truth and every relabeling of the null. At rank 1 the shared part is every
    update projected onto one direction, so all its cosines are 1 and it carries nothing; that
    cell is returned as None rather than scored.
    """
    if rank == 1 and not residual:
        return None
    n = len(vectors)
    # Each basis is fitted on the clients outside one project, so the largest project decides how
    # many rows the smallest fit has. At a rank that reaches that many, the fit spans everything:
    # the shared half is the whole space and the residual is numerical noise, which normalises
    # into unit vectors and scores like a real cell. On 34 clients at rank 32 the residual's
    # largest entry was 4e-15.
    smallest_fit = n - max(projects.count(name) for name in set(projects))
    if rank >= min(smallest_fit, vectors.shape[1]):
        return None
    bases: dict[str, np.ndarray] = {}
    rows = np.zeros((n, n))
    for i in range(n):
        if projects[i] not in bases:
            others = vectors[[j for j in range(n) if projects[j] != projects[i]]]
            bases[projects[i]] = np.linalg.svd(others, full_matrices=False)[2][:rank]
        basis = bases[projects[i]]
        onto = (vectors @ basis.T) @ basis
        space = vectors - onto if residual else onto
        norms = np.linalg.norm(space, axis=1)
        unit = space / np.where(norms > 0, norms, 1.0)[:, None]
        rows[i] = unit @ unit[i]
    return rows


def attribute(rows: np.ndarray, labels: list[str], projects: list[str]) -> tuple[float, float]:
    """Accuracy and balanced accuracy of the nearest class over other projects' clients.

    Balanced accuracy is reported beside accuracy because with 30 clients of one organization
    against 13 of the other, a rule that answers the larger class for everyone scores 0.698 and a
    rule that answers the smaller one scores 0.302, and the whole update's 0.419 was the second
    failure, not a baseline.
    """
    hits: dict[str, list[float]] = {label: [] for label in set(labels)}
    for i in range(len(labels)):
        best, chosen = None, None
        for label in sorted(set(labels)):
            aligned = [
                rows[i][j]
                for j in range(len(labels))
                if j != i and labels[j] == label and projects[j] != projects[i]
            ]
            if aligned and (best is None or np.mean(aligned) > best):
                best, chosen = float(np.mean(aligned)), label
        hits[labels[i]].append(1.0 if chosen == labels[i] else 0.0)
    overall = float(np.mean([h for per in hits.values() for h in per]))
    balanced = float(np.mean([np.mean(per) for per in hits.values()]))
    return overall, balanced


def groupings(labels: list[str], projects: list[str]) -> list[list[str]]:
    """Every relabeling that gives the first organization as many projects as it has.

    Two organizations only: with three, the relabelings would carry two classes while the truth
    carries three, the truth would not be among them, and the p-value would lose its floor.
    """
    if len(set(labels)) != 2:
        raise ValueError(
            f"the null relabels two organizations, got {sorted(set(labels))}; restrict the "
            "clients with --content or extend the enumeration to more classes"
        )
    owner = {p: label for p, label in zip(projects, labels, strict=True)}
    names = sorted(owner)
    first = sorted(set(labels))[0]
    k = sum(1 for p in names if owner[p] == first)
    other = next(label for label in sorted(set(labels)) if label != first)
    out = []
    for chosen in combinations(names, k):
        relabel = {p: (first if p in chosen else other) for p in names}
        out.append([relabel[p] for p in projects])
    return out


def detect(
    vectors: np.ndarray, sources: list[Source], args: argparse.Namespace
) -> dict[str, float]:
    """The aggregate detector on one half. Its basis is fitted to every update, which is correct
    here: a server holds every update, and the detector never scores a client against itself."""
    norms = [float(n) for n in np.linalg.norm(vectors, axis=1)]
    products = gram(cosines(vectors), norms)
    labels = [s.organization for s in sources]
    projects = [s.at("project") for s in sources]
    out: dict[str, float] = {}
    for organization in sorted(set(labels)):
        members = [i for i, name in enumerate(labels) if name == organization]
        if len(members) < 2 or len(labels) - len(members) < args.round_size:
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


def scored(rows: np.ndarray | None, labels: list[str], projects: list[str], null) -> dict:
    """Accuracy and balanced accuracy, each with its exact project-level p."""
    if rows is None:
        return {"degenerate": True}
    accuracy, balanced = attribute(rows, labels, projects)
    under_null = [attribute(rows, relabeled, projects) for relabeled in null]
    return {
        "accuracy": accuracy,
        "balanced_accuracy": balanced,
        "p_accuracy": float(np.mean([a >= accuracy for a, _ in under_null])),
        "p_balanced": float(np.mean([b >= balanced for _, b in under_null])),
        "null_mean_accuracy": float(np.mean([a for a, _ in under_null])),
        "null_accuracies": [a for a, _ in under_null],
    }


def max_over_ranks(cells: list[dict], null_size: int) -> dict | None:
    """The family's best cell, judged against the null's best cell over the same family.

    Choosing the rank after seeing the table is a search, and the best cell's own p prices one
    choice rather than the search. The maximum is taken per relabeling, so each null draw gets
    the same freedom to pick its best rank that the reported cell took. Degenerate cells were
    never scored and are not part of the family.
    """
    scored_cells = [cell for cell in cells if not cell.get("degenerate")]
    if not scored_cells:
        return None
    # The k-th entry means the same relabeling in every cell, which is what makes a maximum over
    # ranks a correction rather than a mixture of unrelated draws. A cell short of the full null
    # would truncate the family silently and read as more significant than it is.
    if null_size < 1:
        raise ValueError("a family-wise p needs a null to judge the family against")
    lengths = {len(cell["null_accuracies"]) for cell in scored_cells}
    if lengths != {null_size}:
        raise ValueError(
            f"every cell must carry all {null_size} relabelings, found {sorted(lengths)}"
        )
    best = max(cell["accuracy"] for cell in scored_cells)
    null_best = [max(cell["null_accuracies"][k] for cell in scored_cells) for k in range(null_size)]
    return {"accuracy": best, "p": float(np.mean([b >= best for b in null_best]))}


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
    labels = [s.organization for s in sources]
    projects = [s.at("project") for s in sources]
    null = groupings(labels, projects)
    majority = max(labels.count(label) for label in set(labels)) / len(labels)

    # The subspace is fitted to the updates as they are, uncentred: an aggregation step averages
    # the updates themselves, so the direction every client shares is part of what alignment
    # finds, and removing the mean first would hand the defence a subspace no server computes.
    _, singular, right = np.linalg.svd(vectors, full_matrices=False)
    energy = float((singular**2).sum())
    whole_rows = np.array(cosines(vectors))

    report: dict = {
        "content": args.content,
        "clients": len(sources),
        "dimension": int(vectors.shape[1]),
        "round_size": args.round_size,
        "majority_rate": majority,
        "null_groupings": len(null),
        "provenance": provenance_header(),
        "whole": {
            **scored(whole_rows, labels, projects, null),
            **detect(vectors, sources, args),
        },
        "ranks": {},
        "complete": False,
    }
    whole = report["whole"]
    print(
        f"whole update: accuracy {whole['accuracy']:.3f} (p {whole['p_accuracy']:.3f}), balanced "
        f"{whole['balanced_accuracy']:.3f} (p {whole['p_balanced']:.3f}); majority {majority:.3f}",
        flush=True,
    )
    for rank in args.ranks:
        if rank >= min(vectors.shape):
            print(f"rank {rank}: not below the {min(vectors.shape)} available, skipped", flush=True)
            continue
        basis = right[:rank]
        shared = (vectors @ basis.T) @ basis
        # This energy share is the global basis's, which the detector uses; the attribution's
        # held-out bases are fitted per project and have their own.
        cell: dict = {"energy_in_global_shared": float((singular[:rank] ** 2).sum() / energy)}
        for half, residual in (("shared", False), ("residual", True)):
            rows = held_out_rows(vectors, projects, rank, residual)
            cell[half] = {
                **scored(rows, labels, projects, null),
                **detect(vectors - shared if residual else shared, sources, args),
            }
        report["ranks"][str(rank)] = cell
        parts = []
        for half in ("shared", "residual"):
            entry = cell[half]
            parts.append(
                f"{half} degenerate"
                if entry.get("degenerate")
                else f"{half} {entry['accuracy']:.3f} (p {entry['p_accuracy']:.3f}), balanced "
                f"{entry['balanced_accuracy']:.3f} (p {entry['p_balanced']:.3f})"
            )
        share = cell["energy_in_global_shared"] * 100
        print(f"rank {rank:3}: {share:5.1f}% energy | " + " | ".join(parts))
        args.out.write_text(json.dumps(report, indent=2))

    for half in ("shared", "residual"):
        family = max_over_ranks(
            [report["ranks"][rank][half] for rank in report["ranks"]], len(null)
        )
        if family is None:
            continue
        report[f"max_over_ranks_{half}"] = family
        print(f"best {half} cell over ranks: {family['accuracy']:.3f}, p {family['p']:.3f}")
    report["complete"] = True
    args.out.write_text(json.dumps(report, indent=2))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
