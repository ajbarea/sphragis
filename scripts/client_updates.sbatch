#!/bin/bash
#SBATCH --job-name=sphragis-clients
#SBATCH --account=fl-mlm
#SBATCH --partition=tigris
#SBATCH --gres=gpu:gh200:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=96G
#SBATCH --time=02:00:00
#SBATCH --output=logs/sphragis-clients-%j.log
#SBATCH --mail-type=END,FAIL
#SBATCH --mail-user=ajb6289@rit.edu

# RQ2's client updates: 34 clients of 64 examples over eight projects, one base-model load, 8
# optimiser steps each at the measured ~7 s a step, so about 45 minutes with saving; two hours
# covers a slower node. Build the corpora with scripts/project_corpora.py into $CLIENTS.
set -euo pipefail

cd "$HOME/ajsoftworks/sphragis"
source sphragis/experiment/cluster-env.sh

C="${CLIENTS:-$HOME/corpus/clients}"

uv run --no-sync python scripts/client_updates.py \
  --clients 6 \
  --source "openstack:python/nova=$C/openstack_nova.jsonl" \
  --source "openstack:python/neutron=$C/openstack_neutron.jsonl" \
  --source "openstack:python/ironic=$C/openstack_ironic.jsonl" \
  --source "openstack:docs/starlingx-docs=$C/starlingx_docs.jsonl" \
  --source "qt:python/pyside-setup=$C/pyside_pyside-setup.jsonl" \
  --source "qt:docs/qtdoc=$C/qt_qtdoc.jsonl" \
  --source "qt:cpp/qt-creator=$C/qt-creator_qt-creator.jsonl" \
  --source "qt:cpp/qtbase=$C/qt_qtbase.jsonl" \
  --adapters "$HOME/scratch/sphragis-adapters-clients$RESULT_SUFFIX" \
  --out "$HOME/client-updates$RESULT_SUFFIX.json"
