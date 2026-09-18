"""Read the client updates' geometry as a source-attribution attack, at three altitudes.

Takes the pairwise cosines scripts/adapter_geometry.py computed over the client adapters and
the source labels scripts/client_updates.py recorded, and reports leave-one-out attribution
accuracy with a permutation p-value per altitude, beside the mean cosine by relation.

    uv run --no-sync --no-active python scripts/client_attribution.py \
        --geometry datasets/results/client-geometry.json \
        --clients datasets/results/client-updates.json \
        --out datasets/results/client-attribution.json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from sphragis.measure.attribution import (
    ALTITUDES,
    Source,
    accuracy,
    group_permutation_p,
    permutation_p,
    relation_means,
)

parser = argparse.ArgumentParser()
parser.add_argument("--geometry", type=Path, required=True)
parser.add_argument("--clients", type=Path, required=True)
parser.add_argument("--draws", type=int, default=10_000)
parser.add_argument("--seed", type=int, default=0)
parser.add_argument("--out", type=Path, required=True)


def main() -> None:
    args = parser.parse_args()
    geometry = json.loads(args.geometry.read_text())
    clients = json.loads(args.clients.read_text())["clients"]
    names = [name.split("/", 1)[1] for name in geometry["adapters"]]
    missing = [n for n in names if n not in clients]
    if missing:
        raise SystemExit(f"adapters with no recorded source: {missing[:5]}")
    sources = [Source.parse(clients[n]["source"]) for n in names]
    cosine = geometry["cosine"]
    report: dict = {"clients": len(names), "altitudes": {}}
    for altitude in ALTITUDES:
        labels = [s.at(altitude) for s in sources]
        counts = Counter(labels)
        observed = accuracy(cosine, labels)
        p = permutation_p(cosine, labels, draws=args.draws, seed=args.seed)
        report["altitudes"][altitude] = {
            "classes": dict(counts),
            "accuracy": observed,
            "majority_rate": max(counts.values()) / len(labels),
            "permutation_p": p,
        }
        print(
            f"{altitude:13} {len(counts)} classes  accuracy {observed:.3f}  "
            f"majority {max(counts.values()) / len(labels):.3f}  p {p:.4f}"
        )
    # Where in the network the source lives, if anywhere: attribution from each module alone.
    # No per-module p-values; with 196 modules the best one is selected, so the count of modules
    # above the pooled accuracy is the honest summary, not the maximum.
    if "by_module" in geometry:
        per_module: dict[str, dict[str, float]] = {}
        for module, matrix in geometry["by_module"].items():
            per_module[module] = {
                altitude: accuracy(matrix, [s.at(altitude) for s in sources])
                for altitude in ALTITUDES
            }
        report["by_module"] = per_module
        for altitude in ALTITUDES:
            pooled = report["altitudes"][altitude]["accuracy"]
            values = sorted((m[altitude], name) for name, m in per_module.items())
            above = sum(1 for v, _ in values if v > pooled)
            print(
                f"{altitude:13} per module: median {values[len(values) // 2][0]:.3f}, "
                f"best {values[-1][0]:.3f} ({values[-1][1].split('model.')[-1]}), "
                f"{above} of {len(values)} above pooled"
            )
    # The language-controlled test: organization attribution among clients of one content type
    # only, where two organizations both contribute, so language cannot carry the attribution.
    report["organization_within_content"] = {}
    for content in sorted({s.content for s in sources}):
        keep = [i for i, s in enumerate(sources) if s.content == content]
        labels = [sources[i].organization for i in keep]
        if len(set(labels)) < 2:
            continue
        sub = [[cosine[i][j] for j in keep] for i in keep]
        counts = Counter(labels)
        cell = {
            "classes": dict(counts),
            "accuracy": accuracy(sub, labels),
            "majority_rate": max(counts.values()) / len(labels),
            "permutation_p": permutation_p(sub, labels, draws=args.draws, seed=args.seed),
        }
        report["organization_within_content"][content] = cell
        print(
            f"organization within {content:8} {dict(counts)}  accuracy {cell['accuracy']:.3f}  "
            f"majority {cell['majority_rate']:.3f}  p {cell['permutation_p']:.4f}"
        )
    # Organization beyond project: each client's own project left out of every class mean, and
    # organization labels permuted across projects, the exchangeable unit. Within each content
    # type where both organizations have at least two projects, and over all clients.
    report["organization_beyond_project"] = {}
    scopes = {"all": list(range(len(sources)))}
    for content in sorted({s.content for s in sources}):
        scopes[content] = [i for i, s in enumerate(sources) if s.content == content]
    for scope, keep in scopes.items():
        projects: dict[str, set[str]] = {}
        for i in keep:
            projects.setdefault(sources[i].organization, set()).add(sources[i].at("project"))
        if len(projects) < 2 or min(len(p) for p in projects.values()) < 2:
            continue
        labels = [sources[i].organization for i in keep]
        groups = [sources[i].at("project") for i in keep]
        sub = [[cosine[i][j] for j in keep] for i in keep]
        p, used = group_permutation_p(sub, labels, groups, draws=args.draws, seed=args.seed)
        cell = {
            "projects": {org: sorted(p_) for org, p_ in projects.items()},
            "accuracy": accuracy(sub, labels, groups),
            "permutation_p": p,
            "relabelings": used,
        }
        report["organization_beyond_project"][scope] = cell
        print(
            f"organization beyond project, {scope:8} "
            f"{ {o: len(v) for o, v in projects.items()} } projects  "
            f"accuracy {cell['accuracy']:.3f}  p {p:.4f} over {used} relabelings"
        )
    report["relations"] = relation_means(cosine, sources)
    for name, cell in sorted(report["relations"].items(), key=lambda kv: -kv[1]["mean"]):
        print(f"  {name:48} {cell['mean']:.3f} over {cell['pairs']} pairs")
    args.out.write_text(json.dumps(report, indent=2))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
