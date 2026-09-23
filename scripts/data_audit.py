"""A repeatable data audit of a built and refined corpus, one artifact per organization.

The Stage 1 audit found three label defects by looking (research log, 2026-09-23): lint-bot
comments kept as reviewers, rebase-only successors kept as rewrites, and a missed one-click
acknowledgement. This makes the looking a command, so a new organization or a new project is
audited the same way before anything is trained on it:

- the drop profile of the build and of the refinement, by reason;
- automated comments the registry recognises, by bot;
- the distribution of successor revision kinds over built examples;
- **candidate unregistered bots**: comment texts repeated verbatim across at least `--min-changes`
  distinct changes that no registered template matches. Humans repeat "typo" and "same here" too,
  so this is a review queue, not a filter: a template belongs in the registry only once its
  source is found.

Reads the built and refined corpora and the raw snapshots; writes no corpus text beyond the
queued comment strings, which are review text rather than code.

    uv run --no-sync --no-active python scripts/data_audit.py --root datasets/gerrit \\
        --org openstack --org qt --out-dir datasets/results
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from sphragis.corpus.automated import matched_bot, registry_digest
from sphragis.corpus.build import is_acknowledgement
from sphragis.corpus.load import built_examples, refined_dir, refined_examples
from sphragis.corpus.refine import RULES_VERSION, revision_kinds
from sphragis.corpus.storage import read_snapshot
from sphragis.provenance import provenance_header

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--root", type=Path, default=Path("datasets/gerrit"))
parser.add_argument("--org", action="append", required=True)
parser.add_argument("--min-changes", type=int, default=15)
parser.add_argument("--top", type=int, default=40)
parser.add_argument("--out-dir", type=Path, default=Path("datasets/results"))


def _sum_drops(paths: list[Path]) -> dict[str, int]:
    total: Counter[str] = Counter()
    for path in paths:
        total.update(json.loads(path.read_text()))
    return dict(total)


def audit(root: Path, org: str, *, min_changes: int, top: int) -> dict:
    built = built_examples(root, org)
    refined = refined_examples(root, org)
    raw = root / org / "raw"
    kinds = revision_kinds(c for p in sorted(raw.glob("*.ndjson.gz")) for c in read_snapshot(p))
    successor = Counter(
        kinds.get((e["change_id"], e["project"], int(e["patch_set"]) + 1), "UNRECORDED")
        for e in built
    )
    by_bot: Counter[str] = Counter()
    changes_by_text: dict[str, set[str]] = defaultdict(set)
    for e in built:
        for comment in e["comments"]:
            bot = matched_bot(comment)
            if bot:
                by_bot[bot] += 1
            elif not is_acknowledgement(comment):
                changes_by_text[re.sub(r"\s+", " ", comment.strip())].add(e["change_id"])
    queue = sorted(
        ((text, len(ids)) for text, ids in changes_by_text.items() if len(ids) >= min_changes),
        key=lambda item: (-item[1], item[0]),
    )[:top]
    return {
        "org": org,
        "rules": RULES_VERSION,
        "bot_registry": registry_digest(),
        "built_examples": len(built),
        "refined_examples": len(refined),
        "build_drops": _sum_drops(sorted((root / org / "examples").glob("*.drops.json"))),
        "refine_drops": _sum_drops(sorted(refined_dir(root, org).glob("*.drops.json"))),
        "automated_comments_by_bot": dict(by_bot),
        "successor_kinds": dict(successor),
        "candidate_unregistered_bots": [{"text": text, "changes": n} for text, n in queue],
        "min_changes": min_changes,
    }


def main() -> None:
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for org in args.org:
        report = audit(args.root, org, min_changes=args.min_changes, top=args.top)
        report["provenance"] = provenance_header()
        out = args.out_dir / f"data-audit-{org}.json"
        out.write_text(json.dumps(report, indent=2) + "\n")
        print(
            f"{org}: built {report['built_examples']}, refined {report['refined_examples']}, "
            f"bots {report['automated_comments_by_bot']}, "
            f"{len(report['candidate_unregistered_bots'])} texts queued for review -> {out}"
        )


if __name__ == "__main__":
    main()
