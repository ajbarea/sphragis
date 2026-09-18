"""What is the weakest fingerprint this contrast can see?

Splits one organization's corpus into two statistically identical halves, imposes a
convention of known strength on one of them, and runs the identical matched-versus-mismatched
contrast RQ1 uses. Sweeping the strength gives the instrument's detection floor.

Without this, every null the study produces is ambiguous between "there is no organizational
fingerprint" and "this contrast cannot see fingerprints of any size". With it, a null becomes
a bound.

Two conventions, reported separately and never averaged.

  marker  a fixed annotation appended to the refinement. Applies to every example and is the
          easiest thing a model could learn, so it is the ceiling: if the contrast misses
          this, it can see nothing. Artificial by design.
  quotes  single-quoted Python literals rewritten as double-quoted. A convention linters
          really enforce, eligible on 14.8% of OpenStack's refinements, so its ceiling is
          that share and the sweep reports realised rather than nominal strength.

Usage on the cluster mirrors rq1_pilot.py, one allocation walking the whole sweep.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from sphragis.experiment.planted import (
    append_marker,
    flip_quotes,
    planted_corpora,
    symmetric_planted_corpora,
)

TRANSFORMS = {"marker": append_marker, "quotes": flip_quotes}

parser = argparse.ArgumentParser()
parser.add_argument("--examples", type=Path, required=True, help="one organization's JSONL")
parser.add_argument("--out-dir", type=Path, required=True, help="where the planted corpora go")
parser.add_argument("--transform", choices=sorted(TRANSFORMS), default="marker")
parser.add_argument(
    "--fraction", action="append", type=float, default=[], help="repeatable; default sweep"
)
parser.add_argument("--seed", type=int, default=11)
parser.add_argument(
    "--symmetric",
    action="store_true",
    help="a convention on BOTH halves (marker only): what two organizations actually look like",
)


def main() -> None:
    args = parser.parse_args()
    fractions = args.fraction or [0.0, 0.05, 0.1, 0.25, 0.5, 1.0]
    rows = [json.loads(line) for line in args.examples.open() if line.strip()]
    transform = TRANSFORMS[args.transform]
    args.out_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {
        "source": str(args.examples),
        "transform": args.transform,
        "seed": args.seed,
        "examples": len(rows),
        "conditions": [],
    }
    print(f"{len(rows)} examples from {args.examples}, convention '{args.transform}'")
    if args.symmetric and args.transform != "marker":
        raise SystemExit("--symmetric is defined for the marker convention only")
    for fraction in fractions:
        if args.symmetric:
            left, right, both = symmetric_planted_corpora(rows, fraction=fraction, seed=args.seed)
            # One report for the manifest line: each half carries its own, at the same rate.
            report = {**both["b"], "a": both["a"], "b": both["b"]}
            tag = f"sym-{fraction:g}"
        else:
            left, right, report = planted_corpora(
                rows, fraction=fraction, seed=args.seed, transform=transform
            )
            tag = f"{args.transform}-{fraction:g}"
        paths = {}
        for side, half in (("a", left), ("b", right)):
            path = args.out_dir / f"{tag}-{side}.jsonl"
            path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in half))
            paths[side] = str(path)
        manifest["conditions"].append({"tag": tag, **report, "paths": paths})
        print(
            f"  {tag:<16} a={len(left):5d} b={len(right):5d}  "
            f"eligible {int(report['eligible']):5d}  changed {int(report['changed']):5d}  "
            f"realised {report['realised_fraction']:.4f}"
        )

    out = args.out_dir / f"manifest-{'sym' if args.symmetric else args.transform}.json"
    out.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"wrote {out}")
    print(
        "\nEach condition is a pair of corpora that differ only in the planted convention.\n"
        "Run the RQ1 contrast on each pair; the smallest realised rate whose interval still\n"
        "clears zero is the detection floor, and it is the number a null has to be read\n"
        "against."
    )


if __name__ == "__main__":
    main()
