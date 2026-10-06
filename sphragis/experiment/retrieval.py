"""Few-shot retrieval: the competing explanation an adapter has to beat.

If an organization's conventions are in its data, an untrained model shown the most similar past
reviews from that organization may follow them without any fine-tuning. The comparator retrieves
the `k` training examples closest to a target by BM25 over its comments and old code (the
standard few-shot setup for code refinement: Pornprasit and Tantithamthavorn, IST 2024, top 3 by
BM25), and prompts the base model with them solved, then the target in the registered template.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from sphragis.corpus.dedup import jaccard, pair_text, shingles
from sphragis.experiment.grid import EvalRun, run_id
from sphragis.experiment.holdout import equalize_training
from sphragis.experiment.partitions import partition_run_windows
from sphragis.experiment.runner import build_prompt

# The registered k (registered-decisions.md, comparators): one most similar past review, which
# works best for review generation (RARe, arXiv:2511.05302), and the standard three for code
# refinement (Pornprasit and Tantithamthavorn, IST 2024).
KS = (1, 3)
# The first ten admissible partitions of each organization, the first ten its adapters ran on:
# Ritzwoller and Romano's least burn-in (partitions.K_MIN), fixed before any generation.
PARTITIONS = 10
# The seed `equalize_training` cuts each half's training rows with in a partition run:
# rq1_pilot.py's --split-seed default, which partition_run.sbatch leaves unset.
EQUALIZE_SEED = 0
# BM25's usual constants (Robertson and Zaragoza, 2009).
K1 = 1.2
B = 0.75
# Letters of any script, so a comment in German, Russian or Chinese is matched on its words.
_TOKEN = re.compile(r"[^\W\d_]+|\d+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def tokens(text: str) -> list[str]:
    """Lowercased words, with camelCase and snake_case identifiers split into their parts."""
    return _TOKEN.findall(_CAMEL.sub(" ", text).lower())


def query_text(example: Mapping[str, Any]) -> str:
    """What a target is matched on: its review comments and the code they were left on."""
    return "\n".join([*example.get("comments", []), str(example["before"])])


class BM25:
    """Okapi BM25 over a fixed pool of examples, pure Python."""

    def __init__(self, pool: Sequence[Mapping[str, Any]]) -> None:
        if not pool:
            raise ValueError("an empty pool would turn every few-shot prompt into the base prompt")
        self.pool = list(pool)
        self.docs = [Counter(tokens(query_text(row))) for row in self.pool]
        self.lengths = [sum(doc.values()) for doc in self.docs]
        self.mean_length = sum(self.lengths) / len(self.lengths)
        frequency: Counter[str] = Counter()
        for doc in self.docs:
            frequency.update(doc.keys())
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in frequency.items()}

    def top(self, example: Mapping[str, Any], k: int) -> list[Mapping[str, Any]]:
        """The `k` pool examples scoring highest for this target, ties broken by pool order."""
        # A term repeated in the query counts each time, as the usual implementation scores it.
        query = Counter(tokens(query_text(example)))
        scored = []
        for index, (doc, length) in enumerate(zip(self.docs, self.lengths, strict=True)):
            value = 0.0
            for term in query.keys() & doc.keys():
                tf = doc[term]
                norm = tf + K1 * (1 - B + B * length / self.mean_length)
                value += query[term] * self.idf[term] * tf * (K1 + 1) / norm
            scored.append((-value, index))
        scored.sort()
        return [self.pool[index] for _, index in scored[:k]]


Prompt = str | list[dict[str, str]]


def few_shot_prompt(example: Mapping[str, Any], shots: Sequence[Mapping[str, Any]]) -> Prompt:
    """The target in the registered template, after its shots as solved prior chat turns.

    Each shot is a user turn in the registered template and an assistant turn holding its
    revised code: the format every adapter was trained on, so the target's turn and the reply
    it asks for are those of the base arm. `shots` come most similar first, as `BM25.top`
    returns them, and are given in reverse, so the closest is the turn before the target's.
    Without shots, the registered prompt itself.
    """
    if not shots:
        return build_prompt(example)
    messages: list[dict[str, str]] = []
    for shot in reversed(shots):
        messages.append({"role": "user", "content": build_prompt(shot)})
        messages.append({"role": "assistant", "content": str(shot["after"])})
    messages.append({"role": "user", "content": build_prompt(example)})
    return messages


def prompt_text(prompt: Prompt) -> str:
    """A prompt as one string, to fingerprint it or measure it without a tokenizer."""
    return prompt if isinstance(prompt, str) else json.dumps(prompt, sort_keys=True)


def condition(k: int) -> str:
    """The condition a retrieval arm is keyed under, as an adapter arm is under `adapter`."""
    return f"retrieval-k{k}"


def arm_key(k: int, pool: str, evaluated: str) -> str:
    """`retrieval-k<k>:<pool>|<evaluated>`: the adapter arms' key, with no training seed."""
    return run_id(EvalRun(f"{condition(k)}:{pool}", evaluated, None))


def pools(
    train: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    size: int,
    fits: Callable[[Mapping[str, Any]], bool],
) -> dict[str, BM25]:
    """Each half's pool: the rows its adapter trains on, equalized to `size` and trainable.

    The same cut the partition run makes (`equalize_training` at `EQUALIZE_SEED` over both
    halves together), then only the rows `build_supervised` would accept, which is what
    `fits` stands for; an adapter never sees a refused row.
    """
    cut = equalize_training(train, seed=EQUALIZE_SEED, size=size)
    return {half: BM25([row for row in rows if fits(row)]) for half, rows in cut.items()}


def shot_similarity(target: Mapping[str, Any], shots: Sequence[Mapping[str, Any]]) -> float:
    """The closest shot's Jaccard similarity to the target, as test 4 measures near-duplicates.

    Over the normalized before/after pair, so a shot carrying the target's own answer scores
    near 1. 0.0 for no shots.
    """
    pair = shingles(pair_text(target))
    return max((jaccard(pair, shingles(pair_text(shot))) for shot in shots), default=0.0)


def arm_prompts(
    targets: Sequence[Mapping[str, Any]],
    indexes: Mapping[str, BM25],
    *,
    evaluated: str,
    ks: Sequence[int] = KS,
) -> tuple[dict[str, list[Prompt]], dict[str, list[float]]]:
    """Every target's prompt under every pool at every k, keyed as `arm_key`, and each prompt's
    closest shot's similarity to its target (`shot_similarity`).

    One retrieval at the largest k serves every k: BM25's ranking is fixed, so the k nearest
    are a prefix of the K nearest.
    """
    prompts: dict[str, list[Prompt]] = {}
    similarity: dict[str, list[float]] = {}
    for pool, index in indexes.items():
        nearest = [index.top(target, max(ks)) for target in targets]
        for k in ks:
            key = arm_key(k, pool, evaluated)
            pairs = list(zip(targets, (shots[:k] for shots in nearest), strict=True))
            prompts[key] = [few_shot_prompt(target, shots) for target, shots in pairs]
            similarity[key] = [shot_similarity(target, shots) for target, shots in pairs]
    return prompts, similarity


def first_partitions(listing: Mapping[str, Any], *, org: str) -> tuple[list[int], int]:
    """An admissible list's first `PARTITIONS` partitions and its training size, for `org` only."""
    if listing.get("org") != org:
        raise ValueError(f"the list is {listing.get('org')}'s partitions, not {org}'s")
    order = list(listing["admissible"][:PARTITIONS])
    if len(order) < PARTITIONS:
        raise ValueError(f"{len(order)} admissible partitions, fewer than {PARTITIONS}")
    return order, int(listing["size_floor"])


def fingerprint(signature: str, prompt: Prompt) -> str:
    """What one generation depends on: the generator's settings and the prompt it was given."""
    return hashlib.sha256(f"{signature}\n{prompt_text(prompt)}".encode()).hexdigest()


def resumable(
    lines: Sequence[str], expected: Mapping[str, Sequence[tuple[str, str]]]
) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    """The rows of a rows file that this job would regenerate identically, and what was dropped.

    `expected` maps each arm to its targets' (id, fingerprint) in order. An arm's rows are kept
    if they are a prefix of those, so an arm a kill cut short resumes where it stopped; an arm
    from another prompt, pool, corpus or generator is dropped whole and regenerated. A line that
    does not parse (a write the kill interrupted) is dropped.
    """
    found: dict[str, list[dict[str, Any]]] = {}
    dropped: set[str] = set()
    for line in lines:
        try:
            row = json.loads(line)
            arm = row.pop("arm") if isinstance(row, dict) else None
        except (json.JSONDecodeError, KeyError):
            arm = None
        if not isinstance(arm, str):
            dropped.add("<unreadable line>")
            continue
        found.setdefault(arm, []).append(row)
    kept: dict[str, list[dict[str, Any]]] = {}
    for arm, rows in found.items():
        marks = [(r.get("id"), r.get("fingerprint")) for r in rows]
        if marks == list(expected.get(arm, []))[: len(marks)]:
            kept[arm] = rows
        else:
            dropped.add(arm)
    return kept, sorted(dropped)


def adapter_run(
    results: Path, *, org: str, partition: int, order: Sequence[int], size: int
) -> tuple[Path, dict[str, Any], Path, int]:
    """The adapters' development-window run on `partition`, the corpus its halves read, and the
    run's position in the admissible order (its training seed).

    Named as `partition_run.sbatch` names it (`-s<k>` for the k-th run past the first, `-n<N>`),
    checked by `partition_run_windows`, and its corpus root read from its halves' sources, so a
    pool is drawn from the corpus that run's adapters trained on, not a rebuild of it.
    """
    if partition not in order:
        raise ValueError(f"partition {partition} is not among the first {list(order)}")
    position = list(order).index(partition) + 1
    seeds = "" if position == 1 else f"-s{position}"
    path = results / f"rq1-partition-{org}-p{partition}{seeds}-n{size}.json"
    if not path.is_file():
        raise ValueError(f"no adapter run {path}")
    run = json.loads(path.read_text())
    try:
        windows = partition_run_windows(run, position=position, admissible=order, train_size=size)
    except ValueError as error:
        raise ValueError(f"{path}: {error}") from error
    if windows != {"train -> dev"}:
        raise ValueError(f"{path}: read on {sorted(windows)}, not the development window")
    roots = {
        Path(corpus["source"].split(" windows under ", 1)[1]).parent.parent
        for corpus in run["corpora"].values()
    }
    if len(roots) != 1:
        raise ValueError(f"{path}: its halves read different corpora {sorted(map(str, roots))}")
    return path, run, roots.pop(), position
