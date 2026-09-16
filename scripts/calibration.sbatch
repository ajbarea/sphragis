#!/bin/bash
#SBATCH --job-name=sphragis-calibration
#SBATCH --account=fl-mlm
#SBATCH --partition=tigris
#SBATCH --gres=gpu:gh200:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=96G
#SBATCH --time=04:00:00
#SBATCH --output=logs/sphragis-calibration-%j.log
#SBATCH --mail-type=END,FAIL
#SBATCH --mail-user=ajb6289@rit.edu

# What is the weakest fingerprint the RQ1 contrast can see?
#
# Two conditions first, because they answer the two questions everything else depends on and
# a full sweep is eighteen hours of allocation.
#
#   marker-1    a convention on every refinement, the easiest thing a model could learn. If
#               the contrast misses THIS, it can see nothing, and every null the study has
#               produced means only that the instrument is blind.
#   marker-0    two halves of one corpus differing in nothing at all. The negative control
#               the study also lacks: the contrast must report nothing here, and a pass
#               would mean the gate fires on a difference that does not exist.
#
# Each condition is ~2,400 examples per side, so roughly 300 optimiser steps per adapter and
# two adapters per condition. At the measured 7.1 s/step and 0.85 s/generation that is about
# 75 minutes per condition; four hours covers both with margin.
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
export HF_HUB_CACHE=$HOME/hf-cache/hub
export TOKENIZERS_PARALLELISM=false
export CC=/usr/bin/gcc

cd "$HOME/ajsoftworks/sphragis"

PLANTED="${PLANTED:-$HOME/corpus/planted}"
CONDITIONS="${CONDITIONS:-marker-1 marker-0}"

for tag in $CONDITIONS; do
  echo "=== condition $tag ==="
  uv run --no-sync python scripts/rq1_pilot.py \
    --corpus "a=$PLANTED/$tag-a.jsonl" \
    --corpus "b=$PLANTED/$tag-b.jsonl" \
    --seeds 1 \
    --equalize-train \
    --adapters "$HOME/scratch/sphragis-adapters-$tag" \
    --out "$HOME/calibration-$tag.json"
done
echo "CALIBRATION_OK"
