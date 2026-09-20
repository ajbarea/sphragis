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

# What is the weakest house style the RQ1 contrast can see?
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
# Measured, not estimated: one condition took 1h53m to 2h31m on a GH200 (jobs 148088-148091),
# and marker-1 took 2h45m. The first version of this note said 75 minutes and four hours for
# two conditions, which lost marker-0 to the wall in job 145092. Submit one condition per job
# (CONDITIONS=<tag>) with TIME=03:30:00.
set -euo pipefail

cd "${SPHRAGIS_CHECKOUT:-$HOME/ajsoftworks/sphragis}"
source sphragis/experiment/cluster-env.sh
tag_result_suffix SEEDS

PLANTED="${PLANTED:-$HOME/corpus/planted}"
CONDITIONS="${CONDITIONS:-marker-1 marker-0}"

# Every condition's result is claimed before any trains, so a clash on the last condition stops
# the job at the start rather than after hours spent on the others.
for tag in $CONDITIONS; do
  claim_result OUT "$HOME/calibration-$tag$RESULT_SUFFIX.json"
done
for tag in $CONDITIONS; do
  echo "=== condition $tag ==="
  OUT="$HOME/calibration-$tag$RESULT_SUFFIX.json"
  uv run --no-sync python scripts/rq1_pilot.py \
    --corpus "a=$PLANTED/$tag-a.jsonl" \
    --corpus "b=$PLANTED/$tag-b.jsonl" \
    --seeds "${SEEDS:-1}" \
    --equalize-train \
    --adapters "$HOME/scratch/sphragis-adapters-$tag$RESULT_SUFFIX" \
    --out "$OUT"
done
echo "CALIBRATION_OK"
