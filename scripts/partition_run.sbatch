#!/bin/bash
#SBATCH --job-name=sphragis-partition-run
#SBATCH --account=fl-mlm
#SBATCH --partition=tigris
#SBATCH --gres=gpu:gh200:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=96G
#SBATCH --time=06:00:00
#SBATCH --output=logs/sphragis-partition-run-%j.log
#SBATCH --mail-type=END,FAIL
#SBATCH --mail-user=ajb6289@rit.edu

# One run of H1 over repeated partitions: the organization deduplicated once, its projects
# assigned in partition seed PARTITION_SEED's order, both halves trained at the fixed size
# TRAIN_SIZE with training seed SEEDS, each scored on both halves. Which partition seed run k
# uses is the k-th entry of the committed admissible list, not a choice made here. Sized as the
# placebo gate it mirrors.
set -euo pipefail

cd "${SPHRAGIS_CHECKOUT:-$HOME/ajsoftworks/sphragis}"
source sphragis/experiment/cluster-env.sh

ORG="${ORG:?set ORG to the organization to partition}"
PARTITION_SEED="${PARTITION_SEED:?set PARTITION_SEED from the admissible list}"
TRAIN_SIZE="${TRAIN_SIZE:?set TRAIN_SIZE to the design's fixed training size}"
SEEDS="${SEEDS:?set SEEDS to the run's position in the admissible list}"
tag_result_suffix SEEDS TRAIN_SIZE
ROOT="$SPHRAGIS_DATA/corpus-partition-$ORG-p$PARTITION_SEED$RESULT_SUFFIX"

claim_result OUT "$SPHRAGIS_RESULTS/rq1-partition-$ORG-p$PARTITION_SEED$RESULT_SUFFIX.json"

uv run --no-sync python scripts/placebo_corpus.py \
  --root "${CORPUS_WINDOWS:-$SPHRAGIS_DATA/corpus-windows}" --org "$ORG" --out-root "$ROOT" \
  --dedup-org --partition-seed "$PARTITION_SEED"
uv run --no-sync python scripts/rq1_pilot.py \
  --root "$ROOT" --org "$ORG-a" --org "$ORG-b" \
  --seeds "$SEEDS" \
  --equalize-train --train-size "$TRAIN_SIZE" \
  --adapters "$SPHRAGIS_ADAPTERS/sphragis-adapters-partition-$ORG-p$PARTITION_SEED$RESULT_SUFFIX" \
  --out "$OUT"
