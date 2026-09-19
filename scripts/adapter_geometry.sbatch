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

# The output is named after the adapters it reads, not after this job's RUN_TAG: PATTERN is what
# selects the input, and a run over one client size that inherited another's suffix would
# overwrite the geometry every committed RQ2 number was computed from. The default pattern gives
# the empty suffix, so existing names are unchanged.
RESULT_SUFFIX="${PATTERN%%/*}"
RESULT_SUFFIX="${RESULT_SUFFIX#sphragis-adapters-clients}"
# MATRICES=a or b reads one LoRA factor rather than the product, which is a different geometry
# over the same adapters, so it names its own file.
MATRICES="${MATRICES:-product}"
[ "$MATRICES" = product ] || RESULT_SUFFIX="$RESULT_SUFFIX-$MATRICES"
export RESULT_SUFFIX

OUT="$(result_path "$HOME/client-geometry$RESULT_SUFFIX.json")"
uv run --no-sync python scripts/adapter_geometry.py \
  --root "$HOME/scratch" \
  --pattern "$PATTERN" \
  --matrices "$MATRICES" \
  ${SUBTRACT_INIT:+--subtract-init} \
  --per-module \
  --out "$OUT"
