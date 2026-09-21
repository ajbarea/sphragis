#!/bin/bash
#SBATCH --job-name=sphragis-geometry
#SBATCH --account=fl-mlm
#SBATCH --partition=tigris
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=01:30:00
#SBATCH --output=logs/sphragis-geometry-%j.log
#SBATCH --mail-type=END,FAIL
#SBATCH --mail-user=ajb6289@rit.edu

# Pairwise cosines between every saved update, through r x r traces. CPU only: no GPU requested.
# PATTERN selects which adapters, so one client run's are never mixed with another's.
set -euo pipefail

cd "${SPHRAGIS_CHECKOUT:-$HOME/ajsoftworks/sphragis}"
source sphragis/experiment/cluster-env.sh

PATTERN="${PATTERN:-sphragis-adapters-clients/*-c*/adapter_model.safetensors}"

# The default pattern gives the empty suffix, so existing names are unchanged.
name_result_after_adapters "$PATTERN"
# MATRICES=a or b reads one LoRA factor rather than the product, a different geometry over the
# same adapters, so it names its own file. Each combination has exactly one meaning:
#   product  the update B A itself; the initial B is zero, so there is nothing to subtract, and
#            subtracting the initial A from one factor computes B (A - A0), which is not the update.
#   a        A's movement from the broadcast initial A. Raw A is dominated by the shared random
#            initialization, so SUBTRACT_INIT=1 is required and "-a" always means the movement.
#   b        B, whose initial value is zero, so B is already its movement.
MATRICES="${MATRICES:-product}"
case "$MATRICES:${SUBTRACT_INIT:-}" in
  product:) ;;
  a:1) RESULT_SUFFIX="$RESULT_SUFFIX-a" ;;
  b:) RESULT_SUFFIX="$RESULT_SUFFIX-b" ;;
  *) echo "MATRICES=$MATRICES with SUBTRACT_INIT=${SUBTRACT_INIT:-unset} has no meaning here:" \
       "product and b take no SUBTRACT_INIT, a requires SUBTRACT_INIT=1" >&2; exit 1 ;;
esac
export RESULT_SUFFIX

claim_result OUT "$HOME/client-geometry$RESULT_SUFFIX.json"
uv run --no-sync python scripts/adapter_geometry.py \
  --root "$HOME/scratch" \
  --pattern "$PATTERN" \
  --matrices "$MATRICES" \
  ${SUBTRACT_INIT:+--subtract-init} \
  --per-module \
  --out "$OUT"
