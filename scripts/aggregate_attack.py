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
    project_permutation,
)
from sphragis.measure.attribution import Source, client_names, reads_local
from sphragis.provenance import provenance_header

parser = argparse.ArgumentParser()
parser.add_argument("--geometry", type=Path, required=True)
parser.add_argument("--clients", type=Path, required=True)
parser.add_argument("--sizes", type=int, nargs="+", default=[4, 8, 16])
parser.add_argument("--draws", type=int, default=400)
parser.add_argument("--seed", type=int, default=0)
parser.add_argument("--altitude", default="organization", choices=("organization", "project"))
parser.add_argument(
    "--beyond-project",
    action="store_true",
    help="split the attacker's reference over projects, so an organization must be recognised "
    "from projects other than the target's own",
)
parser.add_argument(
    "--content",
    help="restrict every client to one content type first, so an organization cannot be read "
    "through the kind of code its clients happen to write",
)
parser.add_argument(
    "--permutation-seeds",
    type=int,
    nargs="+",
    default=[0, 1, 2, 3],
    help="the project permutation is repeated over these round-draw seeds, every p reported",
)
parser.add_argument("--out", type=Path, required=True)


def main() -> None:
    args = parser.parse_args()
    if args.beyond_project and args.altitude == "project":
        parser.error(
            "--beyond-project splits over projects, so it has nothing to test at --altitude project"
        )
    geometry = json.loads(args.geometry.read_text())
    report_in = json.loads(args.clients.read_text())
    clients = report_in["clients"]
    names = client_names(geometry, report_in)
    sources = [Source.parse(clients[n]["source"]) for n in names]
    norms = [geometry["update_norm"][name] for name in geometry["adapters"]]
    products = gram(geometry["cosine"], norms)
    if args.content:
        keep = [i for i, source in enumerate(sources) if source.content == args.content]
        if len(keep) < 4:
            raise SystemExit(f"{len(keep)} clients write {args.content}; too few for a round")
        names = [names[i] for i in keep]
        sources = [sources[i] for i in keep]
        products = [[products[i][j] for j in keep] for i in keep]
        print(f"{len(names)} clients write {args.content}", flush=True)
    labels = [s.at(args.altitude) for s in sources]

    rng = random.Random(args.seed)
    report: dict[str, Any] = {
        "altitude": args.altitude,
        "content": args.content,
        "clients": len(names),
        "draws": args.draws,
        "provenance": provenance_header(),
        "targets": {},
    }
    if "local_equals" in report_in and reads_local(geometry, report_in):
        report["local_equals"] = report_in["local_equals"]
    for label in sorted(set(labels)):
        mine = [i for i, other in enumerate(labels) if other == label]
        outside = [i for i, other in enumerate(labels) if other != label]
        # Per size, not per organization: a size that does not fit is skipped, and the ones that
        # do are still measured. Gating the whole organization on the largest size dropped Qt,
        # whose smaller rounds are perfectly measurable.
        sizes = [size for size in args.sizes if size <= len(outside)]
        if len(mine) < 2 or not sizes:
            print(f"{label}: too few clients outside it for any round size")
            continue
        if args.beyond_project and len({sources[i].at("project") for i in mine}) < 2:
            print(f"{label}: one project, so no reference can come from its other projects")
            continue
        cell: dict[str, Any] = {"clients": len(mine), "membership": {}, "mixed_rounds": {}}
        for size in sizes:
            for present in (1, 2):
                if size - present > len(outside) or len(mine) <= present:
                    continue
                try:
                    mixed = organization_membership_auc(
                        products,
                        members=mine,
                        everyone=range(len(names)),
                        size=size,
                        draws=args.draws,
                        seed=args.seed,
                        at_least=present,
                        groups=[s.at("project") for s in sources] if args.beyond_project else None,
                    )
                except ValueError as refused:
                    # A project split can leave fewer participants than the cell asks for; that
                    # cell is unmeasurable, and the others still are.
                    if "participants needed" not in str(refused):
                        raise
                    print(f"{label:24} rounds of {size:3}, {present} of its clients: {refused}")
                    continue
                cell["mixed_rounds"][f"{size}/{present}"] = mixed
                print(
                    f"{label:24} mixed rounds of {size:3}, {present} of its clients: "
                    f"AUC {mixed['auc']:.3f}, TPR {mixed['tpr_at_1pct_fpr']:.3f} at "
                    f"{mixed['fpr_achieved_at_1pct']:.3f} FPR",
                    flush=True,
                )
        for size in sizes:
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
        # The stranger baseline holds one outsider out, so the paired block needs one more than
        # the round it draws; it takes the largest size that leaves room.
        paired_sizes = [size for size in sizes if size + 1 <= len(outside)]
        if not paired_sizes:
            report["targets"][label] = cell
            print(f"{label}: no round size leaves an outsider over for the stranger baseline")
            continue
        size = paired_sizes[-1]
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

    # Is the detector reading the organization, or the projects that happen to compose it? The
    # organization's projects are replaced by every other choice of as many projects, the detector
    # is rerun on each, and the true grouping's rank is the p-value. The draws are cheaper than the
    # headline run's, so the test is repeated over seeds and every seed's p is kept.
    if args.beyond_project:
        projects_of = [s.at("project") for s in sources]
        owner = {s.at("project"): s.organization for s in sources}
        report["project_permutation"] = {}
        for label in report["targets"]:
            if sum(1 for o in owner.values() if o == label) < 2:
                print(f"{label}: one project, so there is no grouping to permute", flush=True)
                continue
            result = None
            for size in sorted(args.sizes):
                try:
                    result = project_permutation(
                        products,
                        projects_of=projects_of,
                        owner=owner,
                        label=label,
                        size=size,
                        draws=max(60, args.draws // 4),
                        splits=2,
                        seeds=args.permutation_seeds,
                    )
                    break
                except ValueError as refused:
                    if "round size" not in str(refused):
                        raise
            if result is None:
                print(f"{label}: no round size fits every grouping", flush=True)
                continue
            report["project_permutation"][label] = result
            print(
                f"{label:24} project-level permutation over {result['groupings']} groupings: "
                f"p median {result['p_median']:.3f}, range {result['p_min']:.3f} to "
                f"{result['p_max']:.3f} over {len(args.permutation_seeds)} seeds "
                f"(floor {result['floor']:.3f})",
                flush=True,
            )
    args.out.write_text(json.dumps(report, indent=2))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
