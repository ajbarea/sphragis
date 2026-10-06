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
# ARMS=rules prompts with the rules files rules_distil.sbatch wrote, from the results directory,
# instead of BM25 shots; its results are named for it.
ARMS="${ARMS:-retrieval}"
# The rules files carry this run's cluster suffix, before any dry or limit tag is added to it.
case "$ARMS" in
  retrieval) RULES=() ;;
  rules) RULES=(--arms rules --rules "$SPHRAGIS_RESULTS" --rules-suffix "$RESULT_SUFFIX") ;;
  *) echo "ARMS must be retrieval or rules, not $ARMS" >&2; exit 1 ;;
esac
ORG="${ORG:?set ORG to the evaluated organization}"
# DRY_RUN=1 builds every prompt and loads no model, 0 (the default) is a real run, and any
# other value is refused. A dry run or a limited smoke run claims a result of its own, never the
# full run's.
case "${DRY_RUN:-0}" in
  1) DRY=--dry-run; RESULT_SUFFIX="$RESULT_SUFFIX-dry" ;;
  0) DRY="" ;;
  *) echo "DRY_RUN must be 0 or 1, not $DRY_RUN" >&2; exit 1 ;;
esac
RESULT_SUFFIX="$RESULT_SUFFIX${LIMIT:+-limit$LIMIT}"
LISTS=datasets/results

case "$POOLS" in
  halves)
    PARTITION="${PARTITION:?set PARTITION to one of the first ten admissible partitions of ORG}"
    claim_result OUT "$SPHRAGIS_RESULTS/$ARMS-halves-$ORG-p$PARTITION$RESULT_SUFFIX.json"
    uv run --no-sync python scripts/retrieval_comparator.py --pools halves \
      --org "$ORG" --partition "$PARTITION" --results "$LISTS" ${RULES[@]+"${RULES[@]}"} \
      --admissible "$LISTS/admissible-partitions-$ORG.json" ${LIMIT:+--limit "$LIMIT"} \
      ${DRY:+"$DRY"} --out "$OUT"
    ;;
  foreign)
    FOREIGN="${FOREIGN:?set FOREIGN to the other organization}"
    claim_result OUT "$SPHRAGIS_RESULTS/$ARMS-foreign-$ORG$RESULT_SUFFIX.json"
    uv run --no-sync python scripts/retrieval_comparator.py --pools foreign \
      --org "$ORG" --results "$LISTS" --admissible "$LISTS/admissible-partitions-$ORG.json" \
      ${RULES[@]+"${RULES[@]}"} \
      --foreign "$FOREIGN" --foreign-admissible "$LISTS/admissible-partitions-$FOREIGN.json" \
      ${LIMIT:+--limit "$LIMIT"} ${DRY:+"$DRY"} --out "$OUT"
    ;;
  *)
    echo "POOLS must be halves or foreign, not $POOLS" >&2
    exit 1
    ;;
esac
