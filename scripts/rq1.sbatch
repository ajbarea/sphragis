#!/bin/bash
#SBATCH --job-name=sphragis-rq1
#SBATCH --account=fl-mlm
#SBATCH --partition=tigris
#SBATCH --gres=gpu:gh200:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=96G
#SBATCH --time=06:00:00
#SBATCH --output=logs/sphragis-rq1-%j.log
#SBATCH --mail-type=END,FAIL
#SBATCH --mail-user=ajb6289@rit.edu

# --time from measurement, not guesswork. Two pilot runs differing only in step count give
# the rates: job 143899 ran 74 optimiser steps and 480 generations in 16m44s, job 143957 ran
# 40 steps and the same 480 generations in 12m41s. The 34-step difference is 243s, so
# ~7.1 s/step, leaving ~0.85 s/generation after three model loads.
#
#   MODE=pilot   one month per organization, ~40 steps + 480 generations: well under an hour.
#   MODE=windows the train window, ~542 steps per adapter at 4,327 equalized examples, plus
#                roughly 4,700 generations over both dev windows: about 3.5 hours.
#
# Six hours covers the windows mode with margin. Shrink it for a pilot run, and remember a
# request overlapping the monthly 08:00-20:00 maintenance reservation will not schedule.
set -euo pipefail

# MODE=pilot|windows, e.g. `sbatch --export=ALL,MODE=windows scripts/rq1_pilot.sbatch`.
# pilot holds out changes at random from a single month, which measures that the apparatus
# runs. windows trains on the train window and evaluates on dev, which is the study's own
# split; the test window stays sealed and split_by_window refuses to name it.
MODE="${MODE:-pilot}"

# Submit from the checkout: --output is relative and is opened before this body runs.
cd "${SPHRAGIS_CHECKOUT:-$HOME/ajsoftworks/sphragis}"
source sphragis/experiment/cluster-env.sh
tag_result_suffix SEEDS LORA_RANK

if [ "$MODE" = "windows" ]; then
  SOURCE=(--root "$HOME/corpus-windows" --org openstack --org qt)
else
  SOURCE=(
    --corpus "openstack=$HOME/corpus/openstack-2024-10.jsonl"
    --corpus "qt=$HOME/corpus/qt-2024-10.jsonl"
  )
fi

# Corpus files must be cut from refined examples (scripts/project_corpora.py writes each one a
# record); LEGACY_CORPUS=1 reads an older file anyway, to reproduce an earlier result.
claim_result OUT "$HOME/rq1-$MODE$RESULT_SUFFIX.json"
uv run --no-sync python scripts/rq1_pilot.py \
  "${SOURCE[@]}" \
  ${LEGACY_CORPUS:+--legacy-corpus} \
  --seeds "${SEEDS:-1}" \
  --lora-rank "${LORA_RANK:-32}" \
  --equalize-train \
  --adapters "$HOME/scratch/sphragis-adapters-rq1-$MODE$RESULT_SUFFIX" \
  --out "$OUT"
