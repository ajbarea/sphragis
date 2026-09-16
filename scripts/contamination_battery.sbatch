#!/bin/bash
#SBATCH --job-name=sphragis-contamination
#SBATCH --account=fl-mlm
#SBATCH --partition=tigris
#SBATCH --gres=gpu:gh200:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=96G
#SBATCH --time=02:00:00
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
SCORED_TEXT="${SCORED_TEXT:-with_context}"
# Six months each side, built by one pipeline under one cutoff and deduplicated the same
# way, so the sides differ in date and in nothing else. The single-month control they
# replace left 116 scored examples, too few to separate the windows from anything.
POST="${POST:-$HOME/corpus/openstack-post.jsonl}"
PRE="${PRE:-$HOME/corpus/openstack-pre.jsonl}"
TAG="${TAG:-6mo}"
uv run --no-sync python scripts/contamination_battery.py \
  --post "$POST" \
  --pre "$PRE" \
  --scored-text "$SCORED_TEXT" \
  --out "$HOME/contamination-openstack-$TAG-$SCORED_TEXT.json"
