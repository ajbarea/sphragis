#!/bin/bash
#SBATCH --job-name=sphragis-foreign-arms
#SBATCH --account=fl-mlm
#SBATCH --partition=tigris
#SBATCH --gres=gpu:gh200:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=96G
#SBATCH --time=06:00:00
#SBATCH --output=logs/sphragis-foreign-arms-%j.log
#SBATCH --mail-type=END,FAIL
#SBATCH --mail-user=ajb6289@rit.edu

# One H1 partition run's H2 arms: its halves' adapters reused from its decoder-checked run
# (decoder_check.sbatch's -rp output), the two matched foreign arms trained on FOREIGN_SOURCE's
# cleared pool, the file FOREIGN_POOL names (clear_foreign_pool.py's; rq1_pilot.py --foreign-pool),
# and saved beside them, and every arm generated again at the registered decoder. Chain
# decoder_check -> foreign_arms -> likelihood_score (FOREIGN_SOURCE set), each afterok on the one
# before. Sized as the partition run, which trains two adapters too.
set -euo pipefail

cd "${SPHRAGIS_CHECKOUT:-$HOME/ajsoftworks/sphragis}"
source sphragis/experiment/cluster-env.sh

ORG="${ORG:?set ORG to the organization}"
PARTITION_SEED="${PARTITION_SEED:?set PARTITION_SEED of the stored run}"
TRAIN_SIZE="${TRAIN_SIZE:?set TRAIN_SIZE of the stored run}"
SEEDS="${SEEDS:?set SEEDS of the stored run}"
FOREIGN_SOURCE="${FOREIGN_SOURCE:?set FOREIGN_SOURCE to the foreign source}"
FOREIGN_POOL="${FOREIGN_POOL:?set FOREIGN_POOL to its cleared pool (clear_foreign_pool.py)}"
# EXPLORATORY=1: H2's exploratory source (decomposition.EXPLORATORY_FOREIGN_SOURCE), its arms
# trained beside the registered source's on the same stored run.
case "${EXPLORATORY:-0}" in
  1) EXPLORATORY_ARGS=(--exploratory) ;;
  0) EXPLORATORY_ARGS=() ;;
  *) echo "EXPLORATORY must be 0 or 1, not $EXPLORATORY" >&2; exit 1 ;;
esac
if [ -n "${PLANT:-}" ]; then
  echo "foreign arms are drawn for an unplanted run only" >&2
  exit 1
fi
repetition_penalty
NAME="partition-$ORG-p$PARTITION_SEED"
tag_result_suffix SEEDS TRAIN_SIZE
ROOT="$SPHRAGIS_DATA/corpus-$NAME$RESULT_SUFFIX"
STORED="$SPHRAGIS_RESULTS/rq1-$NAME$RESULT_SUFFIX-rp$REPETITION_PENALTY.json"
if [ ! -s "$STORED" ]; then
  echo "no $STORED: run decoder_check.sbatch with the same variables first" >&2
  exit 1
fi
claim_result OUT "$SPHRAGIS_RESULTS/rq1-$NAME$RESULT_SUFFIX-foreign$FOREIGN_SOURCE-rp$REPETITION_PENALTY.json"

uv run --no-sync python scripts/rq1_pilot.py \
  --root "$ROOT" --org "$ORG-a" --org "$ORG-b" \
  --seeds "$SEEDS" --train-size "$TRAIN_SIZE" --equalize-train \
  --adapters "$SPHRAGIS_ADAPTERS/sphragis-adapters-$NAME$RESULT_SUFFIX" \
  --reuse-from "$STORED" \
  --foreign-pool "$FOREIGN_POOL" --foreign-source "$FOREIGN_SOURCE" \
  ${EXPLORATORY_ARGS[@]+"${EXPLORATORY_ARGS[@]}"} \
  --repetition-penalty "$REPETITION_PENALTY" \
  --out "$OUT"
