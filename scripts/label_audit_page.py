"""Build the page a human uses to check one rater's labels blind.

The page shows a blinded item (`label_audit_blind.py`), takes the human's label and the
outside-names answer, locks both, and only then reveals the rater's. Items keep the batches'
order, so any prefix a human completes is a random sample; the rubric's worked example is marked
and not counted. Checks are saved to the published page's own store, under the rubric version,
and read back into `label_audit_agreement.py --human`.

The page carries corpus text, so it is written under `scratch/` and published privately, never
committed; only this builder and its template are.

    uv run --no-sync --no-active python scripts/label_audit_page.py \\
        --batches scratch/audit-v2/batches --rater scratch/audit-v2/raters/A2/labels.json \\
        --out scratch/checkpage/label-audit-check.html
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from label_audit_sample import LABELS  # noqa: E402

TEMPLATE = Path(__file__).resolve().parent / "label_audit" / "check-page.html"

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--batches", type=Path, required=True, help="the blinded batch directory")
parser.add_argument("--rater", type=Path, required=True, help="the checked rater's labels.json")
parser.add_argument("--out", type=Path, required=True)


def page_items(batches: list[dict], labels: dict[str, dict]) -> list[dict]:
    """The page's items, in batch order, each with the checked rater's answers."""
    items = []
    for it in batches:
        answer = labels[it["item"]]
        if answer["label"] not in LABELS:
            raise SystemExit(f"{it['item']}: label {answer['label']!r} is not in the rubric")
        items.append(
            {
                "item": it["item"],
                "path": it["path"],
                "comments": it["comments"],
                "cb": it["context_before"],
                "b": it["before"],
                "a": it["after"],
                "ca": it["context_after"],
                "rater": answer["label"],
                "outside": bool(answer["outside_names"]),
                "reason": answer.get("reason", ""),
            }
        )
    return items


def render(items: list[dict], template: str) -> str:
    # "</" is escaped so no text in an item can close the script block it is embedded in.
    data = json.dumps(items, ensure_ascii=False).replace("</", "<\\/")
    if template.count("__DATA__") != 1:
        raise SystemExit("the template must hold exactly one __DATA__ placeholder")
    return template.replace("__DATA__", data)


def main() -> None:
    args = parser.parse_args()
    batches = [
        json.loads(line)
        for path in sorted(args.batches.glob("batch-*.jsonl"), key=lambda p: int(p.stem[6:]))
        for line in path.read_text().splitlines()
        if line
    ]
    labels = json.loads(args.rater.read_text())
    missing = [it["item"] for it in batches if it["item"] not in labels]
    if missing:
        raise SystemExit(f"the rater left {len(missing)} items unlabelled: {missing[:5]}")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(page_items(batches, labels), TEMPLATE.read_text()))
    print(f"wrote {args.out}: {len(batches)} items")


if __name__ == "__main__":
    main()
