"""Build the label audit's pages: the human check of one rater, and the second-rater page.

The check page shows a blinded item (`label_audit_blind.py`), takes the human's label and the
outside-names answer, locks both, and only then reveals the rater's. Items keep the batches'
order, so any prefix a human completes is a random sample; the rubric's worked example is marked
and not counted. Checks are saved to the published page's own store, under the rubric version,
and read back into `label_audit_agreement.py --human`.

The second-rater page (`--second-rater`) shows the items the human checked from one
organization, in the same order and blinding, and reveals nothing: it takes only item ids from
the agreement report and only the shown fields from the batches, so no label can reach the
page. Its practice item is the rubric's invented worked example. Each viewer's locked answers
are saved under their own id in the page's store, under the rules in
`label_audit/rater2-capabilities.json` (publish the page with them), and read back by
`label_audit_agreement.py --second-raters`.

Both pages carry corpus text, so they are written under `scratch/` and published privately,
never committed; only this builder and its templates are.

    uv run --no-sync --no-active python scripts/label_audit_page.py \\
        --batches scratch/audit-v2/batches --rater scratch/audit-v2/raters/A2/labels.json \\
        --out scratch/checkpage/label-audit-check.html
    uv run --no-sync --no-active python scripts/label_audit_page.py --second-rater \\
        --batches scratch/audit-v2/batches --report datasets/results/label-audit-v2.json \\
        --org openstack --out scratch/rater2/label-audit-rater2.html
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from label_audit_agreement import checked_items  # noqa: E402
from label_audit_sample import LABELS  # noqa: E402

PAGES = Path(__file__).resolve().parent / "label_audit"
TEMPLATE = PAGES / "check-page.html"
SECOND_RATER_TEMPLATE = PAGES / "rater2-page.html"
STYLE = PAGES / "page.css"

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--batches", type=Path, required=True, help="the blinded batch directory")
parser.add_argument("--rater", type=Path, help="the checked rater's labels.json (check page)")
parser.add_argument("--second-rater", action="store_true", help="build the second-rater page")
parser.add_argument("--report", type=Path, help="the agreement report naming the checked items")
parser.add_argument("--org", help="the organization whose checked items the page shows")
parser.add_argument("--out", type=Path, required=True)


def _shown(it: dict) -> dict:
    """What either page shows of an item: its neutral id and the change, nothing else."""
    return {
        "item": it["item"],
        "path": it["path"],
        "comments": it["comments"],
        "cb": it["context_before"],
        "b": it["before"],
        "a": it["after"],
        "ca": it["context_after"],
    }


def page_items(batches: list[dict], labels: dict[str, dict]) -> list[dict]:
    """The check page's items, in batch order, each with the checked rater's answers."""
    items = []
    for it in batches:
        answer = labels[it["item"]]
        if answer["label"] not in LABELS:
            raise SystemExit(f"{it['item']}: label {answer['label']!r} is not in the rubric")
        items.append(
            {
                **_shown(it),
                "rater": answer["label"],
                "outside": bool(answer["outside_names"]),
                "reason": answer.get("reason", ""),
            }
        )
    return items


def second_rater_items(batches: list[dict], chosen: list[str]) -> list[dict]:
    """The second-rater page's items: the chosen ids, in batch order, with no rater's answers."""
    by_id = {it["item"]: it for it in batches}
    missing = [i for i in chosen if i not in by_id]
    if missing:
        raise SystemExit(f"the batches hold none of {missing[:5]}")
    wanted = set(chosen)
    return [_shown(it) for it in batches if it["item"] in wanted]


def render(items: list[dict], template: str, style: str = "") -> str:
    # "</" is escaped so no text in an item can close the script block it is embedded in.
    data = json.dumps(items, ensure_ascii=False).replace("</", "<\\/")
    for placeholder in ("__DATA__", "/*__STYLE__*/"):
        if template.count(placeholder) != 1:
            raise SystemExit(f"the template must hold exactly one {placeholder} placeholder")
    return template.replace("/*__STYLE__*/", style).replace("__DATA__", data)


def read_batches(directory: Path) -> list[dict]:
    return [
        json.loads(line)
        for path in sorted(directory.glob("batch-*.jsonl"), key=lambda p: int(p.stem[6:]))
        for line in path.read_text().splitlines()
        if line
    ]


def main() -> None:
    args = parser.parse_args()
    batches = read_batches(args.batches)
    if args.second_rater:
        if args.rater or not (args.report and args.org):
            parser.error("--second-rater takes --report and --org, and never --rater")
        chosen = checked_items(json.loads(args.report.read_text()), args.org)
        items, template = second_rater_items(batches, chosen), SECOND_RATER_TEMPLATE
    else:
        if not args.rater:
            parser.error("the check page needs --rater")
        labels = json.loads(args.rater.read_text())
        missing = [it["item"] for it in batches if it["item"] not in labels]
        if missing:
            raise SystemExit(f"the rater left {len(missing)} items unlabelled: {missing[:5]}")
        items, template = page_items(batches, labels), TEMPLATE
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(items, template.read_text(), STYLE.read_text()))
    print(f"wrote {args.out}: {len(items)} items")


if __name__ == "__main__":
    main()
