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

# One retrieval comparator job (scripts/retrieval_comparator.py). POOLS=halves: ORG's partition
# PARTITION, its corpus built as partition_run.sbatch builds it, every held-out example prompted
# from each half's pool at k = 1 and 3 (four generations an example). POOLS=foreign: ORG's
# held-out examples alone and from FOREIGN's first admissible partition's halves (five an
# example), once per organization. A rerun resumes from the job's .rows.jsonl.
set -euo pipefail

cd "${SPHRAGIS_CHECKOUT:-$HOME/ajsoftworks/sphragis}"
source sphragis/experiment/cluster-env.sh

POOLS="${POOLS:?set POOLS to halves or foreign}"
ORG="${ORG:?set ORG to the evaluated organization}"
# A dry run or a limited smoke run claims a result of its own, never the full run's.
RESULT_SUFFIX="$RESULT_SUFFIX${DRY_RUN:+-dry}${LIMIT:+-limit$LIMIT}"
WINDOWS_ROOT="${CORPUS_WINDOWS:-$SPHRAGIS_DATA/corpus-windows}"
LISTS=datasets/results
first_admissible() {
  uv run --no-sync python -c \
    "import json, sys; print(json.load(open(sys.argv[1]))['admissible'][0])" "$1"
}
partition_corpus() {
  # $1 org, $2 partition seed, $3 role: the partition run's corpus, under a root of the job's own
  # role, so a foreign job and a halves job never rebuild one root at once.
  local root="$SPHRAGIS_DATA/corpus-retrieval-$3-$1-p$2"
  uv run --no-sync python scripts/placebo_corpus.py \
    --root "$WINDOWS_ROOT" --org "$1" --out-root "$root" \
    --dedup-org --partition-seed "$2" >&2
  echo "$root"
}

case "$POOLS" in
  halves)
    PARTITION="${PARTITION:?set PARTITION to one of the first ten admissible partitions of ORG}"
    claim_result OUT "$SPHRAGIS_RESULTS/retrieval-halves-$ORG-p$PARTITION$RESULT_SUFFIX.json"
    ROOT="$(partition_corpus "$ORG" "$PARTITION" halves)"
    uv run --no-sync python scripts/retrieval_comparator.py --pools halves \
      --root "$ROOT" --org "$ORG" --partition "$PARTITION" \
      --admissible "$LISTS/admissible-partitions-$ORG.json" ${LIMIT:+--limit "$LIMIT"} \
      ${DRY_RUN:+--dry-run} --out "$OUT"
    ;;
  foreign)
    FOREIGN="${FOREIGN:?set FOREIGN to the other organization}"
    claim_result OUT "$SPHRAGIS_RESULTS/retrieval-foreign-$ORG$RESULT_SUFFIX.json"
    FOREIGN_ROOT="$(partition_corpus "$FOREIGN" \
      "$(first_admissible "$LISTS/admissible-partitions-$FOREIGN.json")" foreign)"
    uv run --no-sync python scripts/retrieval_comparator.py --pools foreign \
      --root "$WINDOWS_ROOT" --org "$ORG" --admissible "$LISTS/admissible-partitions-$ORG.json" \
      --foreign "$FOREIGN" --foreign-root "$FOREIGN_ROOT" \
      --foreign-admissible "$LISTS/admissible-partitions-$FOREIGN.json" \
      ${LIMIT:+--limit "$LIMIT"} ${DRY_RUN:+--dry-run} --out "$OUT"
    ;;
  *)
    echo "POOLS must be halves or foreign, not $POOLS" >&2
    exit 1
    ;;
esac
