#!/bin/bash
#SBATCH --job-name=sphragis-contamination
#SBATCH --account=rc-onboard
#SBATCH --partition=tigris
#SBATCH --gres=gpu:gh200:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=96G
#SBATCH --time=01:00:00
#SBATCH --output=logs/sphragis-contamination-%j.log
#SBATCH --mail-type=END,FAIL
#SBATCH --mail-user=ajb6289@rit.edu

set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
# HF_HUB_CACHE, not HF_HOME: relocating HF_HOME also relocates the auto-refreshing OAuth
# token `hf auth login` writes.
export HF_HUB_CACHE=$HOME/hf-cache/hub
export TOKENIZERS_PARALLELISM=false
export CC=/usr/bin/gcc

# Submit from the checkout: --output is relative and is opened before this body runs.
cd "$HOME/ajsoftworks/sphragis"
# SCORED_TEXT=after|with_context, e.g. `sbatch --export=ALL,SCORED_TEXT=with_context ...`.
# Each variant writes its own file, so a rerun never overwrites an earlier result.
SCORED_TEXT="${SCORED_TEXT:-after}"
uv run --no-sync python scripts/contamination_battery.py \
  --post "$HOME/corpus/openstack-2024-10.jsonl" \
  --pre "$HOME/corpus/openstack-2024-01-control.jsonl" \
  --scored-text "$SCORED_TEXT" \
  --out "$HOME/contamination-openstack-$SCORED_TEXT.json"
