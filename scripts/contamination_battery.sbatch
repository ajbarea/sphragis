#!/bin/bash
#SBATCH --job-name=sphragis-contamination
#SBATCH --account=fl-mlm
#SBATCH --partition=tigris
#SBATCH --gres=gpu:gh200:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=96G
#SBATCH --time=05:00:00
#SBATCH --output=logs/sphragis-contamination-%j.log
#SBATCH --mail-type=END,FAIL
#SBATCH --mail-user=ajb6289@rit.edu

# Five hours, not two. Job 145093 hit a two-hour wall at 1h35m of guided completion on the
# six-month corpus, which is fifteen times the sample the old limit was measured against.
# Membership finished inside that and is now written before the generative half starts, so a
# future wall costs the null instrument rather than the primary one.
set -euo pipefail

# Submit from the checkout: --output is relative and is opened before this body runs.
cd "${SPHRAGIS_CHECKOUT:-$HOME/ajsoftworks/sphragis}"
source sphragis/experiment/cluster-env.sh
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
  --out "$HOME/contamination-openstack-$TAG-$SCORED_TEXT$RESULT_SUFFIX.json"
