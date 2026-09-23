"""Blind a label-audit sheet for its raters: neutral ids, a fixed random order, no organization.

`label_audit_sample.py` draws the sheet. Raters must not see which organization, project, window
or half an example came from, and example ids name the organization, so each example gets a
neutral id (`item-001`, ...) in an order shuffled once by `--seed`. The key from neutral id back
to example id and the order are written beside the batches and are never shown to a rater.

A file path can still name its project; it stays, because a rater needs it to read the change.

Everything written is corpus text or points into it, so it belongs under `scratch/`, which is
not committed.

    uv run --no-sync --no-active python scripts/label_audit_blind.py \\
        --sheet scratch/audit-v2/sheet.jsonl --out-dir scratch/audit-v2
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

# What a rater sees of an example. Everything else on the sheet names where it came from.
SHOWN = ("path", "comments", "context_before", "before", "after", "context_after")

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--sheet", type=Path, required=True)
parser.add_argument("--out-dir", type=Path, required=True)
parser.add_argument("--seed", type=int, default=20260924)
parser.add_argument("--batches", type=int, default=4, help="files the items are split across")


def blind(rows: list[dict], *, seed: int) -> tuple[list[dict], dict[str, str]]:
    """Items in a seeded random order under neutral ids, and the key back to example ids."""
    shuffled = list(rows)
    random.Random(seed).shuffle(shuffled)
    items, key = [], {}
    for n, row in enumerate(shuffled, 1):
        item = f"item-{n:03d}"
        key[item] = row["id"]
        items.append({"item": item, **{field: row.get(field, "") for field in SHOWN}})
    return items, key


def main() -> None:
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.sheet.read_text().splitlines() if line]
    items, key = blind(rows, seed=args.seed)
    size = -(-len(items) // args.batches)
    batches = args.out_dir / "batches"
    batches.mkdir(parents=True, exist_ok=True)
    for k in range(args.batches):
        chunk = items[k * size : (k + 1) * size]
        (batches / f"batch-{k + 1}.jsonl").write_text("".join(json.dumps(i) + "\n" for i in chunk))
    (args.out_dir / "key.json").write_text(json.dumps(key, indent=1))
    (args.out_dir / "order.json").write_text(json.dumps([key[i["item"]] for i in items]))
    print(f"{len(items)} items in {args.batches} batches under {batches}; key in key.json")


if __name__ == "__main__":
    main()
