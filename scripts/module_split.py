"""Does the organization survive in the modules a selective scheme actually transmits?

Every personalized federated adapter design splits the adapter into a part that is sent to the
server and a part that stays home, and they differ only in where they cut. PFAdapter (Liu, Yang,
Wang, Yang, Hao, Zhang, Liu and Zhou, IEEE TCCN, arXiv:2607.12111) cuts by the role of the
projection: "query and key projections are assigned to global synchronization for capturing
universal multimodal semantics across the network, while value and output projections remain
localized for edge-specific adaptation", and only the global-shared set is transmitted.

None of these papers measures whether the transmitted part still identifies its source. PFAdapter
says as much in its own words: "FL keeps raw samples on device, but it does not by itself guarantee
resistance to update inversion, gradient leakage, or membership inference. The privacy scope of
PFAdapter is therefore limited to decentralized training without centralized raw-data pooling."

This reads each cut with the study's own instruments. The geometry stores a cosine matrix per
adapted module; a subset of modules is scored by the mean of their cosine matrices, which is the
Gram matrix of unit vectors because a mean of positive semi-definite matrices is one, so the
aggregate detector's algebra holds on it unchanged. Norm-weighting across modules is deliberately
dropped: one module with a large update would otherwise decide a subset's verdict.

    uv run --no-sync --no-active python scripts/module_split.py \
        --geometry datasets/results/client-geometry-cpp-early.json \
        --clients datasets/results/client-updates-cpp-early.json \
        --content cpp --out datasets/results/module-split.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from sphragis.measure.aggregate import organization_membership_auc
from sphragis.measure.attribution import Source, accuracy
from sphragis.provenance import provenance_header

parser = argparse.ArgumentParser()
parser.add_argument("--geometry", type=Path, required=True)
parser.add_argument("--clients", type=Path, required=True)
parser.add_argument("--content", default=None)
parser.add_argument("--round-size", type=int, default=4)
parser.add_argument("--draws", type=int, default=400)
parser.add_argument("--splits", type=int, default=8)
parser.add_argument("--seed", type=int, default=0)
parser.add_argument("--out", type=Path, required=True)

# The cuts the literature proposes, named by the paper that proposes them, plus each projection
# on its own so a verdict on a cut can be traced to the modules that carry it.
CUTS = {
    "pfadapter transmitted (q,k)": ("q_proj", "k_proj"),
    "pfadapter kept local (v,o)": ("v_proj", "o_proj"),
    "attention, all four": ("q_proj", "k_proj", "v_proj", "o_proj"),
    "mlp, all three": ("gate_proj", "up_proj", "down_proj"),
    "everything": (),
}


def read(cosine: np.ndarray, sources: list[Source], args: argparse.Namespace) -> dict[str, float]:
    labels = [s.organization for s in sources]
    projects = [s.at("project") for s in sources]
    products = cosine.tolist()
    out = {"organization_beyond_project": accuracy(products, labels, projects)}
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
        out[f"{organization}_auc"] = scored["auc"]
        out[f"{organization}_tpr_at_1pct"] = scored["tpr_at_1pct_fpr"]
    return out


def main() -> None:
    args = parser.parse_args()
    geometry = json.loads(args.geometry.read_text())
    if "by_module" not in geometry:
        raise SystemExit(f"{args.geometry} has no per-module cosines: rerun with --per-module")
    names = [str(n).split("/", 1)[1] for n in geometry["adapters"]]
    clients = json.loads(args.clients.read_text())["clients"]
    sources = [Source.parse(clients[n]["source"]) for n in names]
    keep = list(range(len(names)))
    if args.content:
        keep = [i for i, s in enumerate(sources) if s.at("content") == args.content]
        sources = [sources[i] for i in keep]
        print(f"{len(keep)} clients write {args.content}", flush=True)

    by_module = {k: np.array(v)[np.ix_(keep, keep)] for k, v in geometry["by_module"].items()}
    kinds = sorted({name.split(".")[-1] for name in by_module})
    report: dict = {
        "content": args.content,
        "clients": len(sources),
        "modules": len(by_module),
        "kinds": kinds,
        "provenance": provenance_header(),
        "cuts": {},
    }
    for label, kept_kinds in {**CUTS, **{f"{k} alone": (k,) for k in kinds}}.items():
        chosen = [
            matrix
            for name, matrix in by_module.items()
            if not kept_kinds or name.split(".")[-1] in kept_kinds
        ]
        if not chosen:
            continue
        scored = read(np.mean(chosen, axis=0), sources, args)
        report["cuts"][label] = {"modules": len(chosen), **scored}
        detectors = " ".join(
            f"{key.removesuffix('_auc')} {value:.3f}"
            for key, value in scored.items()
            if key.endswith("_auc")
        )
        print(
            f"{label:32} {len(chosen):4d} modules  attribution "
            f"{scored['organization_beyond_project']:.3f}  detector {detectors}",
            flush=True,
        )
        args.out.write_text(json.dumps(report, indent=2))
    args.out.write_text(json.dumps(report, indent=2))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
