"""Merging single-seed result files into one multi-seed study.

Runs are merged only if they are seeds of one study: the same model, corpora, split and
training size, and the same held-out examples per arm.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sphragis.corpus.redact import redact_text


def configuration(run: dict[str, Any], *, roots_verified: bool = False) -> dict[str, Any]:
    """Everything that must match for two runs to be seeds of one study.

    The held-out examples are checked separately, but they fix only the evaluation: a run
    trained at 788 examples scores the same held-out set as one trained at 1,517, and merging
    the two would average different studies as if they were seeds of one.

    Training size is read from what was trained on, not from `--train-size`: a size equal to
    the smallest set trains on exactly the rows an unsized run does. Steps per organization
    catch a change to epochs or batch size between the runs.

    `roots_verified` compares each corpus by its path below the root, for runs built into
    separate roots that the caller has checked are byte-identical and records the digest of.
    """
    return {
        "model_id": run["model_id"],
        "split_seed": run["split_seed"],
        "equalize_train": run["equalize_train"],
        "max_new_tokens": run["max_new_tokens"],
        "corpora": {
            org: (
                _below_root(c["source"], org) if roots_verified else c["source"],
                c.get("train_examples_equalized"),
                c["held_out_examples"],
            )
            for org, c in run["corpora"].items()
        },
        "steps": {
            name.rsplit("-s", 1)[0]: (t["items"], t["steps"]) for name, t in run["training"].items()
        },
    }


def _below_root(source: str, org: str) -> str:
    """A corpus source with its root removed: `.../<root>/<org>/refined` becomes `<org>/refined`."""
    marker = f"/{org}/"
    if marker not in source:
        raise SystemExit(f"cannot find {org}'s directory in corpus source {source!r}")
    return source[source.rindex(marker) + 1 :]


def merge(
    paths: list[Path], *, roots_verified: bool = False
) -> tuple[dict[str, list[dict[str, Any]]], tuple[int, ...]]:
    """Every run's adapter arms under one results map, and the seeds they carry."""
    merged: dict[str, list[dict[str, Any]]] = {}
    seeds: list[int] = []
    first: dict[str, Any] | None = None
    for path in paths:
        run = json.loads(path.read_text())
        if first is None:
            first = run
        elif (theirs := configuration(run, roots_verified=roots_verified)) != (
            ours := configuration(first, roots_verified=roots_verified)
        ):
            changed = sorted(k for k in ours if ours[k] != theirs.get(k))
            raise SystemExit(f"{path} was not the same study as {paths[0]}: differs on {changed}")
        seeds.extend(run["seeds"])
        for arm, rows in run["results"].items():
            if arm.startswith("base|"):
                # The base model has no seed; keep the first run's evaluation of it.
                merged.setdefault(arm, rows)
                continue
            if arm in merged:
                raise SystemExit(f"{path} repeats arm {arm}: two runs used the same seed")
            reference = first["results"].get(f"{arm.rsplit('|', 1)[0]}|s{first['seeds'][0]}")
            # Compared through the redaction, because a stored run whose ids were redacted
            # and a fresh one whose ids were not are the same examples under different
            # spellings, and this guard exists to catch different examples.
            if reference is not None and {redact_text(r["id"]) for r in rows} != {
                redact_text(r["id"]) for r in reference
            }:
                raise SystemExit(f"{path} scored different examples on {arm}")
            merged[arm] = rows
    return merged, tuple(seeds)
