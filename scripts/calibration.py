"""What is the weakest house style this contrast can see?

Splits one organization's corpus into two statistically identical halves, imposes a
convention of known strength on one of them, and runs the identical matched-versus-mismatched
contrast RQ1 uses. Sweeping the strength gives the instrument's detection floor.

Without this, every null the study produces is ambiguous between "there is no organizational
house style" and "this contrast cannot see a house style of any strength". With it, a null becomes
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

from sphragis.corpus.load import (
    derived_file_rows,
    derived_sources,
    refined_examples,
    write_derived_file,
)
from sphragis.experiment.planted import (
    append_marker,
    flip_quotes,
    planted_corpora,
    symmetric_planted_corpora,
)

TRANSFORMS = {"marker": append_marker, "quotes": flip_quotes}

parser = argparse.ArgumentParser()
source = parser.add_mutually_exclusive_group(required=True)
source.add_argument("--org", help="one organization's refined examples, under --root")
source.add_argument("--examples", type=Path, help="a corpus file cut from refined examples")
parser.add_argument("--root", type=Path, default=Path("datasets/gerrit"))
parser.add_argument(
    "--legacy-corpus",
    action="store_true",
    help="with --examples: read a file not cut under the current label rules, to reproduce an "
    "earlier result; the planted halves are then written without a record, so every later "
    "read of them must say --legacy-corpus too",
)
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
    if args.org and args.legacy_corpus:
        parser.error("--legacy-corpus reads a file: pass --examples, not --org")
    sources: list[tuple[Path, str]] | None
    if args.org:
        rows, sources = refined_examples(args.root, args.org), [(args.root, args.org)]
    else:
        rows = derived_file_rows(args.examples, legacy=args.legacy_corpus)
        sources = None if args.legacy_corpus else derived_sources(args.examples)
    transform = TRANSFORMS[args.transform]
    args.out_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {
        "source": str(args.examples) if args.examples else f"{args.root}/{args.org}",
        "legacy_corpus": args.legacy_corpus,
        "transform": args.transform,
        "seed": args.seed,
        "examples": len(rows),
        "conditions": [],
    }
    print(f"{len(rows)} examples from {manifest['source']}, convention '{args.transform}'")
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
            if sources is None:
                path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in half))
            else:
                via = [args.examples] if args.examples else []
                write_derived_file(path, half, sources=sources, via=via)
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
