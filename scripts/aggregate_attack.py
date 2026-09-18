"""RQ2 under secure aggregation: does a round's aggregate betray whose clients were in it?

The per-client attack assumes the server sees each update. Secure aggregation hides them and
shows only the round's mean, which is the threat model a federated deployment would actually
claim. This runs two detectors an honest-but-curious server could run, over the same client
updates, using only the Gram matrix `adapter_geometry` recorded.

  membership   rounds of one size, half holding exactly one client of the target organization
               and half none, scored by the round's alignment with the attacker's reference
               updates from that organization. Reported as an AUC, where 0.5 is no signal. The
               detected client is never in the reference set.
  paired       FedAttr's include-minus-exclude difference (arXiv:2605.06596) with the watermark
               removed, per target client, beside the same statistic computed for a client of
               another organization, which is the baseline it has to beat.

    uv run --no-sync --no-active python scripts/aggregate_attack.py \
        --geometry datasets/results/client-geometry.json \
        --clients datasets/results/client-updates.json \
        --out datasets/results/aggregate-attack.json
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from statistics import fmean
from typing import Any

from sphragis.measure.aggregate import (
    gram,
    membership_auc,
    organization_membership_auc,
    paired_subset_difference,
)
from sphragis.measure.attribution import Source

parser = argparse.ArgumentParser()
parser.add_argument("--geometry", type=Path, required=True)
parser.add_argument("--clients", type=Path, required=True)
parser.add_argument("--sizes", type=int, nargs="+", default=[4, 8, 16])
parser.add_argument("--draws", type=int, default=400)
parser.add_argument("--seed", type=int, default=0)
parser.add_argument("--altitude", default="organization", choices=("organization", "project"))
parser.add_argument("--out", type=Path, required=True)


def main() -> None:
    args = parser.parse_args()
    geometry = json.loads(args.geometry.read_text())
    clients = json.loads(args.clients.read_text())["clients"]
    names = [name.split("/", 1)[1] for name in geometry["adapters"]]
    sources = [Source.parse(clients[n]["source"]) for n in names]
    norms = [geometry["update_norm"][name] for name in geometry["adapters"]]
    products = gram(geometry["cosine"], norms)
    labels = [s.at(args.altitude) for s in sources]

    rng = random.Random(args.seed)
    report: dict[str, Any] = {
        "altitude": args.altitude,
        "clients": len(names),
        "draws": args.draws,
        "targets": {},
    }
    for label in sorted(set(labels)):
        mine = [i for i, other in enumerate(labels) if other == label]
        outside = [i for i, other in enumerate(labels) if other != label]
        if len(mine) < 2 or len(outside) < max(args.sizes):
            continue
        cell: dict[str, Any] = {"clients": len(mine), "membership": {}, "mixed_rounds": {}}
        for size in args.sizes:
            for present in (1, 2):
                if size - present > len(outside) or len(mine) <= present:
                    continue
                mixed = organization_membership_auc(
                    products,
                    members=mine,
                    everyone=range(len(names)),
                    size=size,
                    draws=args.draws,
                    seed=args.seed,
                    at_least=present,
                )
                cell["mixed_rounds"][f"{size}/{present}"] = mixed
                print(
                    f"{label:24} mixed rounds of {size:3}, {present} of its clients: "
                    f"AUC {mixed['auc']:.3f}",
                    flush=True,
                )
        for size in args.sizes:
            aucs = []
            for held in mine:
                reference = [i for i in mine if i != held]
                aucs.append(
                    membership_auc(
                        products,
                        target=[held],
                        others=outside,
                        reference=reference,
                        size=size,
                        draws=args.draws,
                        seed=args.seed + held,
                    )["auc"]
                )
            cell["membership"][size] = {"mean_auc": fmean(aucs), "per_client": aucs}
            print(f"{label:24} rounds of {size:3}: membership AUC {fmean(aucs):.3f}", flush=True)
        size = args.sizes[-1]
        own, stranger = [], []
        for held in mine:
            reference = [i for i in mine if i != held]
            own.append(
                paired_subset_difference(
                    products,
                    target=held,
                    others=outside,
                    reference=reference,
                    size=size,
                    draws=args.draws // 4,
                    seed=args.seed + held,
                )
            )
            impostor = rng.choice(outside)
            stranger.append(
                paired_subset_difference(
                    products,
                    target=impostor,
                    others=[i for i in outside if i != impostor],
                    reference=reference,
                    size=size,
                    draws=args.draws // 4,
                    seed=args.seed + held,
                )
            )
        cell["paired"] = {
            "round_size": size,
            "own_mean": fmean(own),
            "other_organization_mean": fmean(stranger),
            "own": own,
            "other_organization": stranger,
        }
        print(
            f"{label:24} paired difference at {size}: own {fmean(own):+.3f}, "
            f"a stranger's {fmean(stranger):+.3f}",
            flush=True,
        )
        report["targets"][label] = cell
    args.out.write_text(json.dumps(report, indent=2))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
