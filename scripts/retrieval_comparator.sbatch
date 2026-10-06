#!/bin/bash
#SBATCH --job-name=sphragis-retrieval
#SBATCH --account=fl-mlm
#SBATCH --partition=tigris
#SBATCH --gres=gpu:gh200:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=96G
#SBATCH --time=12:00:00
#SBATCH --output=logs/sphragis-retrieval-%j.log
#SBATCH --mail-type=END,FAIL
#SBATCH --mail-user=ajb6289@rit.edu

# One retrieval comparator job (scripts/retrieval_comparator.py), reading the corpora the
# adapters' partition runs read, found through their committed results; nothing is rebuilt.
# POOLS=halves: ORG's partition PARTITION, every held-out example prompted from each half's pool
# at k = 1 and 3 (four generations an example). POOLS=foreign: every example ORG's partitions
# score, alone and from FOREIGN's first admissible partition's halves (five an example), once
# per organization. A rerun resumes from the job's .rows.jsonl.
set -euo pipefail

cd "${SPHRAGIS_CHECKOUT:-$HOME/ajsoftworks/sphragis}"
source sphragis/experiment/cluster-env.sh

POOLS="${POOLS:?set POOLS to halves or foreign}"
ORG="${ORG:?set ORG to the evaluated organization}"
# A dry run or a limited smoke run claims a result of its own, never the full run's.
RESULT_SUFFIX="$RESULT_SUFFIX${DRY_RUN:+-dry}${LIMIT:+-limit$LIMIT}"
LISTS=datasets/results

case "$POOLS" in
  halves)
    PARTITION="${PARTITION:?set PARTITION to one of the first ten admissible partitions of ORG}"
    claim_result OUT "$SPHRAGIS_RESULTS/retrieval-halves-$ORG-p$PARTITION$RESULT_SUFFIX.json"
    uv run --no-sync python scripts/retrieval_comparator.py --pools halves \
      --org "$ORG" --partition "$PARTITION" --results "$LISTS" \
      --admissible "$LISTS/admissible-partitions-$ORG.json" ${LIMIT:+--limit "$LIMIT"} \
      ${DRY_RUN:+--dry-run} --out "$OUT"
    ;;
  foreign)
    FOREIGN="${FOREIGN:?set FOREIGN to the other organization}"
    claim_result OUT "$SPHRAGIS_RESULTS/retrieval-foreign-$ORG$RESULT_SUFFIX.json"
    uv run --no-sync python scripts/retrieval_comparator.py --pools foreign \
      --org "$ORG" --results "$LISTS" --admissible "$LISTS/admissible-partitions-$ORG.json" \
      --foreign "$FOREIGN" --foreign-admissible "$LISTS/admissible-partitions-$FOREIGN.json" \
      ${LIMIT:+--limit "$LIMIT"} ${DRY_RUN:+--dry-run} --out "$OUT"
    ;;
  *)
    echo "POOLS must be halves or foreign, not $POOLS" >&2
    exit 1
    ;;
esac
