#!/bin/bash
#SBATCH --job-name=sphragis-clients
#SBATCH --account=fl-mlm
#SBATCH --partition=tigris
#SBATCH --gres=gpu:gh200:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=96G
#SBATCH --time=04:00:00
#SBATCH --output=logs/sphragis-clients-%j.log
#SBATCH --mail-type=END,FAIL
#SBATCH --mail-user=ajb6289@rit.edu

# RQ2's client updates: about 100 clients of 64 examples over nineteen projects, one base-model
# load, 8 optimiser steps each at the measured ~7 s a step, so roughly two hours with saving.
# CLIENT_SIZE=128 doubles the steps and the time, and names its own results and adapters, so two
# training lengths are never globbed together. Build the corpora with project_corpora into $CLIENTS.
set -euo pipefail

cd "${SPHRAGIS_CHECKOUT:-$HOME/ajsoftworks/sphragis}"
source sphragis/experiment/cluster-env.sh
tag_result_suffix CLIENT_SIZE
# PACKING_SEED draws another assignment of examples to clients with the same initialization, and
# names its own results and adapters so a second packing never lands on the first.
# The initialization seed is 1, so PACKING_SEED=1 would be the default packing under a new name.
if [ "${PACKING_SEED:-}" = 1 ]; then
  echo "PACKING_SEED=1 is the default packing; draw another seed" >&2
  exit 1
fi
[ -z "${PACKING_SEED:-}" ] || RESULT_SUFFIX="$RESULT_SUFFIX-p$PACKING_SEED"
export RESULT_SUFFIX

C="${CLIENTS:-$HOME/corpus/clients}"

SOURCES="${SOURCES:-scripts/clients-cpp.txt}"

# CLIENT_SIZE sets how much local training a client does: 64 examples is 8 optimiser steps,
# 128 is 16. The RQ1 geometry says training length moves updates apart, so the attack has to be
# read at more than one length.
# Corpus files must be cut from refined examples (scripts/project_corpora.py writes each one a
# record); LEGACY_CORPUS=1 reads an older file anyway, to reproduce an earlier result.
claim_result OUT "$HOME/client-updates$RESULT_SUFFIX.json"
uv run --no-sync python scripts/client_updates.py \
  --clients "${CLIENTS_PER_SOURCE:-6}" \
  --client-size "${CLIENT_SIZE:-64}" \
  ${PACKING_SEED:+--packing-seed "$PACKING_SEED"} \
  --sources-file "$SOURCES" \
  --corpus-root "$C" \
  ${LEGACY_CORPUS:+--legacy-corpus} \
  --adapters "$HOME/scratch/sphragis-adapters-clients$RESULT_SUFFIX" \
  --out "$OUT"
