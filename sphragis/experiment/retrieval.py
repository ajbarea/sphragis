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
from sphragis.experiment.decomposition import halves
from sphragis.experiment.grid import conditioned
from sphragis.experiment.holdout import equalize_training, window_split
from sphragis.experiment.neutral import source_root
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
    return conditioned(condition(k), pool, evaluated)


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
    results: Path, *, org: str, partition: int, order: Sequence[int], size: int, suffix: str = ""
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
    # `suffix` names a rescoring of the run (`-rp<penalty>`, decoder_check.sbatch).
    path = results / f"rq1-partition-{org}-p{partition}{seeds}-n{size}{suffix}.json"
    if not path.is_file():
        raise ValueError(f"no adapter run {path}")
    run = json.loads(path.read_text())
    try:
        windows = partition_run_windows(run, position=position, admissible=order, train_size=size)
    except ValueError as error:
        raise ValueError(f"{path}: {error}") from error
    if windows != {"train -> dev"}:
        raise ValueError(f"{path}: read on {sorted(windows)}, not the development window")
    roots = {source_root(corpus["source"]) for corpus in run["corpora"].values()}
    if len(roots) != 1:
        raise ValueError(f"{path}: its halves read different corpora {sorted(map(str, roots))}")
    return path, run, roots.pop(), position


def trainable(tokenizer: Any) -> Callable[[Mapping[str, Any]], bool]:
    """Whether an example is one an adapter trains on: `supervised` keeps it."""
    from sphragis.experiment.training import supervised

    def fits(row: Mapping[str, Any]) -> bool:
        return supervised(tokenizer, [row], prompt_builder=build_prompt)[1] == 0

    return fits


def partition_root(root: Path, org: str, partition: int) -> dict[str, Any]:
    """The manifest of a partition corpus, refused unless it is `org`'s `partition`, unplanted."""
    path = root / "placebo.json"
    if not path.is_file():
        raise ValueError(f"{root}: no placebo.json; build it with placebo_corpus.py")
    manifest = json.loads(path.read_text())
    found = {
        "source_org": manifest.get("source_org"),
        "partition_seed": manifest.get("partition_seed"),
        "deduplicated once": manifest.get("dedup_org") is not None,
        "planted": manifest.get("plant") is not None,
        "names": manifest.get("names"),
    }
    wanted = {
        "source_org": org,
        "partition_seed": partition,
        "deduplicated once": True,
        "planted": False,
        "names": list(halves(org)),
    }
    if found != wanted:
        raise ValueError(f"{path}: {found}, not {wanted}")
    return manifest


def corpus_of(
    results: Path, org: str, partition: int, order: list[int], size: int
) -> tuple[Path, dict[str, int], dict[str, dict]]:
    """The corpus the adapters' run on `partition` read, how many rows each half trained on,
    and what the run recorded of each half's corpus.

    Refused unless the corpus is that partition's and the run cut its training sets as the pools
    are cut: equalized, at `EQUALIZE_SEED`.
    """
    path, run, root, position = adapter_run(
        results, org=org, partition=partition, order=order, size=size
    )
    if (run.get("equalize_train"), run.get("split_seed")) != (True, EQUALIZE_SEED):
        raise ValueError(
            f"{path}: equalize_train {run.get('equalize_train')}, split_seed "
            f"{run.get('split_seed')}; the pools are cut equalized at {EQUALIZE_SEED}"
        )
    partition_root(root, org, partition)
    trained = {half: run["training"][f"{half}-s{position}"]["items"] for half in halves(org)}
    return root, trained, run["corpora"]


def read_halves(
    root: Path, org: str, recorded: Mapping[str, Mapping[str, Any]]
) -> tuple[dict[str, list[dict]], dict[str, list[dict]], dict[str, dict]]:
    """Each half's training and held-out rows, refused unless they are what the run recorded.

    A corpus rebuilt at the same root since the adapters trained passes its manifest check; the
    counts the run recorded of each half (examples, dedup, train, held out) catch it.
    """
    train, held_out, summary = {}, {}, {}
    for half in halves(org):
        train[half], held_out[half], summary[half] = window_split(root, half)
        now = {
            **summary[half],
            "train_examples": len(train[half]),
            "held_out_examples": len(held_out[half]),
        }
        then = {key: recorded[half].get(key) for key in now}
        if now != then:
            raise ValueError(f"{root / half}: {now}, but the adapters' run read {then}")
    return train, held_out, summary


def half_pools(
    root: Path,
    org: str,
    size: int,
    fits: Callable[[Mapping[str, Any]], bool],
    recorded: Mapping[str, Mapping[str, Any]],
    trained: Mapping[str, int] | None = None,
) -> tuple[dict, dict[str, list[dict]], dict[str, dict]]:
    """A partition's two half pools, each half's held-out rows, and where each came from: the
    one cut the comparator prompts from and the distiller distils, so both stand where the
    adapters did. With `trained` (each adapter's training-set size, from its run), a pool of
    another size is refused: each pool is its adapter's training set."""
    train, held_out, summary = read_halves(root, org, recorded)
    cut = pools(train, size=size, fits=fits)
    pooled = {half: len(index.pool) for half, index in cut.items()}
    if trained is not None and pooled != dict(trained):
        raise ValueError(f"pools hold {pooled} rows, the adapters trained on {dict(trained)}")
    return cut, held_out, summary
