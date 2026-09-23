#!/bin/bash
#SBATCH --job-name=sphragis-dual
#SBATCH --account=fl-mlm
#SBATCH --partition=tigris
#SBATCH --gres=gpu:gh200:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=96G
#SBATCH --time=12:00:00
#SBATCH --output=logs/sphragis-dual-%j.log
#SBATCH --mail-type=END,FAIL
#SBATCH --mail-user=ajb6289@rit.edu

# The adapter-instance cut: two adapters a client, only the global one communicated. The
# iterative schedule trains twice a round, so ROUNDS=2 is four training passes a client against
# client_updates' one, and the wall clock scales with it: the 128-example run took an hour and a
# half, so twelve hours is the budget with saving. SCHEDULE=feddpa-f is the sequential variant
# and costs two passes.
set -euo pipefail

cd "${SPHRAGIS_CHECKOUT:-$HOME/ajsoftworks/sphragis}"
source sphragis/experiment/cluster-env.sh
tag_result_suffix CLIENT_SIZE
# The schedule and the round count both change what is measured, so each names its own result:
# a feddpa-f run must never land on a feddpa-t name, and two round counts are two experiments.
SCHEDULE="${SCHEDULE:-feddpa-t}"
ROUNDS="${ROUNDS:-2}"
case "$SCHEDULE" in
  feddpa-f) RESULT_SUFFIX="$RESULT_SUFFIX-f" ;;
  feddpa-t) RESULT_SUFFIX="$RESULT_SUFFIX-t$ROUNDS" ;;
  *) echo "SCHEDULE must be feddpa-f or feddpa-t, got $SCHEDULE" >&2; exit 1 ;;
esac
export RESULT_SUFFIX

C="${CLIENTS:-$HOME/corpus/clients3}"
SOURCES="${SOURCES:-scripts/clients-cpp-rebuilt.txt}"

# Corpus files must be cut from refined examples (scripts/project_corpora.py writes each one a
# record); LEGACY_CORPUS=1 reads an older file anyway, to reproduce an earlier result.
claim_result OUT "$HOME/client-updates-dual$RESULT_SUFFIX.json"
uv run --no-sync python scripts/dual_adapter_updates.py \
  --clients "${CLIENTS_PER_SOURCE:-6}" \
  --client-size "${CLIENT_SIZE:-64}" \
  --schedule "$SCHEDULE" \
  --rounds "$ROUNDS" \
  ${PACKING_SEED:+--packing-seed "$PACKING_SEED"} \
  --sources-file "$SOURCES" \
  --corpus-root "$C" \
  ${LEGACY_CORPUS:+--legacy-corpus} \
  --adapters "$HOME/scratch/sphragis-adapters-dual$RESULT_SUFFIX" \
  --out "$OUT"
