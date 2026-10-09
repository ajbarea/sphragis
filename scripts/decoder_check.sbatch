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

# One stored H1 partition or placebo run rescored with an explicit repetition penalty, its
# adapters and corpus reused, so the decoder is the only thing that changes. The checkpoint's generation
# config sets 1.1, which every stored run decoded with; fp32 reproduces those runs exactly, so
# the stored result is the 1.1 arm.
set -euo pipefail

cd "${SPHRAGIS_CHECKOUT:-$HOME/ajsoftworks/sphragis}"
source sphragis/experiment/cluster-env.sh

ORG="${ORG:?set ORG to the organization}"
repetition_penalty
# KIND=partition (default): an H1 partition run, named as partition_run.sbatch names it.
# KIND=placebo: a placebo_gate.sbatch run, which the H1 sensitivity simulation reads; it was
# trained without a fixed size, under its RUN_TAG (e.g. v3), from PLACEBO_ROOT.
case "${KIND:-partition}" in
  partition)
    PARTITION_SEED="${PARTITION_SEED:?set PARTITION_SEED of the stored run}"
    TRAIN_SIZE="${TRAIN_SIZE:?set TRAIN_SIZE of the stored run}"
    SEEDS="${SEEDS:?set SEEDS of the stored run}"
    # A planted run (partition_run.sbatch's PLANT) is named and stored with its fraction.
    PLANT="${PLANT:-}"
    PLACEBO_ROOT=""
    NAME="partition-$ORG-p$PARTITION_SEED"
    ;;
  placebo)
    PLACEBO_ROOT="${PLACEBO_ROOT:?set PLACEBO_ROOT to the corpus root the stored run read}"
    if [ "${SEEDS:-1}" != 1 ] || [ -n "${TRAIN_SIZE:-}${PLANT:-}" ]; then
      echo "KIND=placebo rescores a single-seed run without a size or plant" >&2
      exit 1
    fi
    SEEDS=1 TRAIN_SIZE="" PLANT=""
    NAME="placebo-$ORG"
    ;;
  *) echo "KIND must be partition or placebo, not $KIND" >&2; exit 1 ;;
esac
tag_result_suffix SEEDS TRAIN_SIZE PLANT
ROOT="${PLACEBO_ROOT:-$SPHRAGIS_DATA/corpus-$NAME$RESULT_SUFFIX}"
SIZE=(--seeds "$SEEDS" ${TRAIN_SIZE:+--train-size "$TRAIN_SIZE"})

STORED="$SPHRAGIS_RESULTS/rq1-$NAME$RESULT_SUFFIX.json"
claim_result OUT "$SPHRAGIS_RESULTS/rq1-$NAME$RESULT_SUFFIX-rp$REPETITION_PENALTY.json"

uv run --no-sync python scripts/rq1_pilot.py \
  --root "$ROOT" --org "$ORG-a" --org "$ORG-b" \
  "${SIZE[@]}" --equalize-train \
  --adapters "$SPHRAGIS_ADAPTERS/sphragis-adapters-$NAME$RESULT_SUFFIX" \
  --reuse-from "$STORED" \
  --repetition-penalty "$REPETITION_PENALTY" \
  --out "$OUT"
