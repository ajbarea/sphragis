#!/bin/bash
#SBATCH --job-name=sphragis-rules-distil
#SBATCH --account=fl-mlm
#SBATCH --partition=tigris
#SBATCH --gres=gpu:gh200:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=96G
#SBATCH --time=03:00:00
#SBATCH --output=logs/sphragis-rules-distil-%j.log
#SBATCH --mail-type=END,FAIL
#SBATCH --mail-user=ajb6289@rit.edu

# One rules-distillation job (scripts/rules_distil.py). SOURCE=reviews: ORG's partition PARTITION,
# both halves' files from their pools, about ten to fourteen map calls and one reduce a half.
# SOURCE=guide: ORG's written conventions from datasets/rules/guide-ORG.json.
set -euo pipefail

cd "${SPHRAGIS_CHECKOUT:-$HOME/ajsoftworks/sphragis}"
source sphragis/experiment/cluster-env.sh

SOURCE="${SOURCE:?set SOURCE to reviews or guide}"
ORG="${ORG:?set ORG to the organization}"
LISTS=datasets/results

case "$SOURCE" in
  reviews)
    PARTITION="${PARTITION:?set PARTITION to one of the first ten admissible partitions of ORG}"
    claim_result OUT "$SPHRAGIS_RESULTS/rules-reviews-$ORG-p$PARTITION$RESULT_SUFFIX.json"
    uv run --no-sync python scripts/rules_distil.py --source reviews --org "$ORG" \
      --partition "$PARTITION" --results "$LISTS" --out "$OUT"
    ;;
  guide)
    claim_result OUT "$SPHRAGIS_RESULTS/rules-guide-$ORG$RESULT_SUFFIX.json"
    uv run --no-sync python scripts/rules_distil.py --source guide --org "$ORG" \
      --guides datasets/rules --out "$OUT"
    ;;
  *)
    echo "SOURCE must be reviews or guide, not $SOURCE" >&2
    exit 1
    ;;
esac
