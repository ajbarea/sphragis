#!/bin/bash
#SBATCH --job-name=sphragis-decoder-check
#SBATCH --account=fl-mlm
#SBATCH --partition=tigris
#SBATCH --gres=gpu:gh200:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=96G
#SBATCH --time=03:00:00
#SBATCH --output=logs/sphragis-decoder-check-%j.log
#SBATCH --mail-type=END,FAIL
#SBATCH --mail-user=ajb6289@rit.edu

# One stored H1 partition run rescored with an explicit repetition penalty, its adapters and
# corpus reused, so the decoder is the only thing that changes. The checkpoint's generation
# config sets 1.1, which every stored run decoded with; fp32 reproduces those runs exactly, so
# the stored result is the 1.1 arm.
set -euo pipefail

cd "${SPHRAGIS_CHECKOUT:-$HOME/ajsoftworks/sphragis}"
source sphragis/experiment/cluster-env.sh

ORG="${ORG:?set ORG to the organization}"
PARTITION_SEED="${PARTITION_SEED:?set PARTITION_SEED of the stored run}"
TRAIN_SIZE="${TRAIN_SIZE:?set TRAIN_SIZE of the stored run}"
SEEDS="${SEEDS:?set SEEDS of the stored run}"
repetition_penalty
# A planted run (partition_run.sbatch's PLANT) is named and stored with its fraction.
PLANT="${PLANT:-}"
tag_result_suffix SEEDS TRAIN_SIZE PLANT
ROOT="$SPHRAGIS_DATA/corpus-partition-$ORG-p$PARTITION_SEED$RESULT_SUFFIX"

STORED="$SPHRAGIS_RESULTS/rq1-partition-$ORG-p$PARTITION_SEED$RESULT_SUFFIX.json"
claim_result OUT "$SPHRAGIS_RESULTS/rq1-partition-$ORG-p$PARTITION_SEED$RESULT_SUFFIX-rp$REPETITION_PENALTY.json"

uv run --no-sync python scripts/rq1_pilot.py \
  --root "$ROOT" --org "$ORG-a" --org "$ORG-b" \
  --seeds "$SEEDS" \
  --equalize-train --train-size "$TRAIN_SIZE" \
  --adapters "$SPHRAGIS_ADAPTERS/sphragis-adapters-partition-$ORG-p$PARTITION_SEED$RESULT_SUFFIX" \
  --reuse-from "$STORED" \
  --repetition-penalty "$REPETITION_PENALTY" \
  --out "$OUT"
