#!/bin/bash
#SBATCH --job-name=sphragis-projects
#SBATCH --account=fl-mlm
#SBATCH --partition=tigris
#SBATCH --gres=gpu:gh200:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=96G
#SBATCH --time=03:30:00
#SBATCH --output=logs/sphragis-projects-%j.log
#SBATCH --mail-type=END,FAIL
#SBATCH --mail-user=ajb6289@rit.edu

# Is the fingerprint a property of the codebase rather than the organization?
#
# The identical matched-versus-mismatched contrast RQ1 runs, but between two projects inside
# ONE organization, so anything it finds is a codebase effect and cannot be an organizational
# one. Both sides are Qt C++, so the language confound that separates OpenStack from Qt is
# absent here. Build the corpora with scripts/project_corpora.py.
#
# Sized from the calibration runs, which have the same shape (two corpora of ~1,900 to 2,500
# examples, holdout by change): 1h53m to 2h45m on a GH200.
set -euo pipefail

cd "$HOME/ajsoftworks/sphragis"
source sphragis/experiment/cluster-env.sh
tag_result_suffix SEEDS TRAIN_SIZE

PROJECTS="${PROJECTS:-$HOME/corpus/projects}"
A="${A:-qt-creator_qt-creator}"
B="${B:-qt_qtbase}"
# TRAIN_SIZE=N cuts both training sets to N, so pairs of different sizes compare at one size.
SIZE=(${TRAIN_SIZE:+--train-size "$TRAIN_SIZE"})

uv run --no-sync python scripts/rq1_pilot.py \
  --corpus "a=$PROJECTS/$A.jsonl" \
  --corpus "b=$PROJECTS/$B.jsonl" \
  --seeds "${SEEDS:-1}" \
  --equalize-train "${SIZE[@]}" \
  --adapters "$HOME/scratch/sphragis-adapters-projects-$A-$B$RESULT_SUFFIX" \
  --out "$HOME/projects-$A-$B$RESULT_SUFFIX.json"
