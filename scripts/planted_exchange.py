"""The exchange rate between exact match and s, read on a planted code-level convention.

The SESOI is one more exact refinement per hundred review comments, and $s$ is read in nats per
token, so carrying it to $s$ needs a rate: how much $s$ moves when exact match moves by one. The
registered rate is the adapters' gain over the base model on each scale, most of which is learning
the task's output format. A planted convention gives the rate where it matters: each half of a
partition run writes its Python refinements in its own quote style (`placebo_corpus.py
--plant-convention quotes`), so on a planted held-out example the own adapter has learned the
reference's style and the sibling the other one. The rate is the own-minus-sibling gain in $s$
over that in exact match, each summed over the planted held-out examples of every half of every
run, with the registered percentile interval from resampling runs; a resample whose summed gain
in exact match is not positive has no rate and is counted, not hidden.

Each run is given as its likelihood file (`likelihood_score.py`, whose rows carry both scores)
and the manifest its planted corpus was written with (`placebo.json`, naming the changed ids); a
manifest whose partition seed, fraction or organization is not the run's corpus root is refused.

    uv run --no-sync --no-active python scripts/planted_exchange.py \\
        --run R/likelihood-partition-openstack-p2-n1850-plantquotes1.0-rp1.0.json=M/placebo.json \\
        ... --out datasets/results/planted-exchange-openstack.json
"""

from __future__ import annotations

import argparse
import json
import random
import re
from pathlib import Path

from sphragis.corpus.redact import redact_text
from sphragis.experiment.grid import EvalRun, run_id
from sphragis.experiment.neutral import source_root
from sphragis.measure.stats import percentile_interval
from sphragis.provenance import provenance_header

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "--run", action="append", required=True, metavar="LIKELIHOOD=MANIFEST", help="one per run"
)
parser.add_argument("--resamples", type=int, default=10_000)
parser.add_argument("--seed", type=int, default=7)
parser.add_argument("--out", type=Path, required=True)

METRICS = ("logprob_per_token", "exact_match")


def half_contrast(run: dict, own: str, sibling: str, planted: set[str]) -> dict:
    """Own minus sibling on `own`'s held-out examples the plant changed, summed per metric."""
    (seed,) = run["seeds"]
    arms = {
        name: {r["id"]: r for r in run["results"][run_id(EvalRun(f"adapter:{name}", own, seed))]}
        for name in (own, sibling)
    }
    ids = sorted(set(arms[own]) & planted)
    if not ids:
        raise ValueError(f"no planted held-out example on {own}")
    return {
        "examples": len(ids),
        **{
            metric: sum(float(arms[own][i][metric]) - float(arms[sibling][i][metric]) for i in ids)
            for metric in METRICS
        },
    }


def _matches(run: dict, manifest: dict) -> bool:
    """Whether every half's corpus root is the manifest's partition, fraction and organization."""
    plant = manifest["plant"]
    root = (
        f"corpus-partition-{manifest['source_org']}-p{manifest['partition_seed']}-"
        f"(?:[^/]*-)?plantquotes{re.escape(str(plant['fraction']))}(?:-[^/]*)?$"
    )
    return all(
        re.search(root, str(source_root(corpus["source"])).rsplit("/", 1)[-1])
        for corpus in run["corpora"].values()
    )


def read_run(likelihood: Path, manifest_path: Path) -> dict:
    """Both halves' planted contrasts for one run, refused unless its plant is the quote style."""
    run = json.loads(likelihood.read_text())
    manifest = json.loads(manifest_path.read_text())
    plant = manifest.get("plant")
    if plant is None or plant.get("convention") != "quotes":
        raise ValueError(f"{manifest_path} is not a quote-style plant")
    if not _matches(run, manifest):
        raise ValueError(f"{manifest_path} is not the manifest of {likelihood}'s corpus")
    names = sorted(plant["halves"])
    if names != sorted(run["corpora"]):
        raise ValueError(
            f"{manifest_path} plants {names}, {likelihood} scores {sorted(run['corpora'])}"
        )
    halves = {}
    for own in names:
        (sibling,) = [n for n in names if n != own]
        # Stored results carry ids through the redaction; the manifest names them as written.
        planted = {redact_text(i) for i in plant["halves"][own]["changed_ids"]}
        halves[own] = half_contrast(run, own, sibling, planted)
    return {"file": str(likelihood), "fraction": plant["fraction"], "halves": halves}


def rate(runs: list[dict]) -> float:
    """The summed gain in s over the summed gain in exact match, every planted example."""
    halves = [h for run in runs for h in run["halves"].values()]
    em = sum(h["exact_match"] for h in halves)
    if em <= 0:
        raise ValueError("the plant moved exact match by nothing, so no rate can be read")
    return sum(h["logprob_per_token"] for h in halves) / em


def interval(runs: list[dict], *, resamples: int, seed: int) -> dict:
    """The registered 95% percentile interval over runs resampled, and how many had no rate."""
    rng = random.Random(seed)
    draws, undefined = [], 0
    for _ in range(resamples):
        try:
            draws.append(rate([rng.choice(runs) for _ in runs]))
        except ValueError:
            undefined += 1
    if undefined > resamples * 0.01:
        raise ValueError(f"{undefined} of {resamples} resamples moved exact match by nothing")
    return {"rate_95": list(percentile_interval(draws, 0.95)), "undefined_resamples": undefined}


def main() -> None:
    args = parser.parse_args()
    try:
        runs = []
        for spec in args.run:
            likelihood, _, manifest = spec.partition("=")
            runs.append(read_run(Path(likelihood), Path(manifest)))
        fractions = {run["fraction"] for run in runs}
        if len(fractions) != 1:
            raise ValueError(f"the runs plant at {sorted(fractions)}, one rate needs one fraction")
        result = {"rate": rate(runs), **interval(runs, resamples=args.resamples, seed=args.seed)}
    except ValueError as error:
        raise SystemExit(str(error)) from error
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "provenance": provenance_header(),
                "fraction": fractions.pop(),
                **result,
                "resamples": args.resamples,
                "seed": args.seed,
                "runs": runs,
            },
            indent=2,
        )
        + "\n"
    )
    print(f"rate {result['rate']:.4f} nats per token per unit exact match, {result['rate_95']}")


if __name__ == "__main__":
    main()
