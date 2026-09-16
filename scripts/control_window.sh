#!/usr/bin/env bash
# Collect the pre-cutoff control window, built exactly like the post-cutoff window.
#
# One uniform window (cutoff = the window's first day) rather than six independent months,
# because the control and the window it is compared against have to differ in date and in
# nothing else. Zhang et al. (ACL 2026, arXiv:2509.00072) show that differences in how
# questions are constructed distort temporal signals on their own; identical construction is
# the defence. 2024-01 is re-fetched for that reason: it was collected as its own window.
#
# Window ends 2024-01, comfortably before the February 2024 repository-creation boundary the
# Qwen2.5-Coder report states (arXiv:2409.12186 3.1.1), so the control is plausibly inside
# training while the post-cutoff window postdates the model's release outright.
set -u
ROOT=datasets/gerrit-control
ORG=openstack
CUTOFF=2023-08-01
MONTHS="2023-08 2023-09 2023-10 2023-11 2023-12 2024-01"

cd "$(dirname "$0")/.." || exit 1
set -a; . ./.env; set +a   # the corpus salt, which never reaches a log or the process list
for m in $MONTHS; do
  echo "=== $m fetch $(date +%H:%M:%S) ==="
  uv run --no-active python -m sphragis.corpus fetch \
    --org "$ORG" --month "$m" --root "$ROOT" --cutoff "$CUTOFF" --overwrite \
    --request-interval 1.0 || { echo "$m: fetch failed, stopping"; exit 1; }
  echo "=== $m build $(date +%H:%M:%S) ==="
  uv run --no-active python -m sphragis.corpus build \
    --org "$ORG" --month "$m" --root "$ROOT" --cutoff "$CUTOFF" \
    --request-interval 1.0 || { echo "$m: build failed, stopping"; exit 1; }
  echo "=== $m done $(date +%H:%M:%S): $(wc -l < $ROOT/$ORG/examples/$m.jsonl) examples ==="
done
echo "=== control window complete $(date +%H:%M:%S) ==="
wc -l "$ROOT/$ORG/examples/"*.jsonl
