#!/bin/bash
#SBATCH --job-name=sphragis-placebo
#SBATCH --account=fl-mlm
#SBATCH --partition=tigris
#SBATCH --gres=gpu:gh200:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=96G
#SBATCH --time=06:00:00
#SBATCH --output=logs/sphragis-placebo-%j.log
#SBATCH --mail-type=END,FAIL
#SBATCH --mail-user=ajb6289@rit.edu

# The registered gate, run against two halves of ONE organization. Same runner, same windows,
# same equalisation, same seeds: only the boundary changes. It answers what the gate cannot
# answer alone, whether an arbitrary project boundary reproduces the organizational one.
#
# Sized from the windows mode it mirrors: ~3.5 hours there on two organizations of 4,327
# equalized examples. A placebo half is smaller than its parent organization, so six hours
# holds with the same margin.
set -euo pipefail

cd "${SPHRAGIS_CHECKOUT:-$HOME/ajsoftworks/sphragis}"
source sphragis/experiment/cluster-env.sh
tag_result_suffix SEEDS

ORG="${ORG:?set ORG to the organization to split against itself}"
ROOT="${PLACEBO_ROOT:-$HOME/corpus-windows-placebo-$ORG}"

# Claimed before the corpus is built, not after: a second job for the same organization must
# stop at the claim rather than after spending the build, and the build overwrites the root.
claim_result OUT "$HOME/rq1-placebo-$ORG$RESULT_SUFFIX.json"

# Built inside the job rather than beforehand, so the split that ran is the split this job
# records and a stale root from an earlier build cannot be read as this one's.
uv run --no-sync python scripts/placebo_corpus.py \
  --root "${CORPUS_WINDOWS:-$HOME/corpus-windows}" --org "$ORG" --out-root "$ROOT"
uv run --no-sync python scripts/rq1_pilot.py \
  --root "$ROOT" --org "$ORG-a" --org "$ORG-b" \
  --seeds "${SEEDS:-1}" \
  --equalize-train \
  --adapters "$HOME/scratch/sphragis-adapters-placebo-$ORG$RESULT_SUFFIX" \
  --out "$OUT"
