#!/bin/bash
#SBATCH --job-name=sphragis-likelihood
#SBATCH --account=fl-mlm
#SBATCH --partition=tigris
#SBATCH --gres=gpu:gh200:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=96G
#SBATCH --time=03:00:00
#SBATCH --output=logs/sphragis-likelihood-%j.log
#SBATCH --mail-type=END,FAIL
#SBATCH --mail-user=ajb6289@rit.edu

# One stored H1 partition run scored by the log-probability of each held-out reference, its
# adapters and examples reused (scripts/likelihood_score.py): no training, no generation. Takes
# the stored run's ORG, PARTITION_SEED, SEEDS and TRAIN_SIZE, and PLANT for a planted run, and
# reads the run decoded at REPETITION_PENALTY (the registered 1.0 by default) for the exact
# match its rows carry.
set -euo pipefail

cd "${SPHRAGIS_CHECKOUT:-$HOME/ajsoftworks/sphragis}"
source sphragis/experiment/cluster-env.sh

ORG="${ORG:?set ORG to the organization}"
PARTITION_SEED="${PARTITION_SEED:?set PARTITION_SEED of the stored run}"
TRAIN_SIZE="${TRAIN_SIZE:?set TRAIN_SIZE of the stored run}"
SEEDS="${SEEDS:?set SEEDS of the stored run}"
PLANT="${PLANT:-}"
repetition_penalty
tag_result_suffix SEEDS TRAIN_SIZE PLANT
NAME="partition-$ORG-p$PARTITION_SEED"

claim_result OUT "$SPHRAGIS_RESULTS/likelihood-$NAME$RESULT_SUFFIX.json"
uv run --no-sync python scripts/likelihood_score.py \
  --run "$SPHRAGIS_RESULTS/rq1-$NAME$RESULT_SUFFIX-rp$REPETITION_PENALTY.json" \
  --adapters "$SPHRAGIS_ADAPTERS/sphragis-adapters-$NAME$RESULT_SUFFIX" \
  --seeds "$SEEDS" --train-size "$TRAIN_SIZE" \
  --out "$OUT"
