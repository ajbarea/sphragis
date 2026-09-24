#!/usr/bin/env bash
# Collect Chromium over the train and dev windows, through the CLI and nothing else.
#
# Usage: PROJECTS="v8/v8 angle/angle ..." scripts/fetch_chromium.sh
#        (restartable: rerun it with the same PROJECTS after any interruption)
#
# The project set has no default: scripts/host_scoping.py found none that meets the split
# criteria, so it is a decision to pass in, and a month refetched under a different set would
# mix corpora. chromium/src cannot be fetched by month at all: it passes the host's 10,000-result
# cap, and fetch stops on the truncation rather than keep a partial month.
#
# One snapshot per month holds every selected project, as AOSP's do: the CLI writes one file
# per organization-month, so looping projects separately would overwrite each other. The
# cutoff is the train window's first day, so no pilot-window change enters; the months stop at
# 2025-10 because the CLI refuses the sealed test window's start and everything after it.
#
# Snapshots land in the main checkout's datasets/gerrit, wherever this runs from, so the
# other organizations' tooling finds them. The salt comes from the main checkout's .env and
# never reaches a log or the process list.
set -u
PROJECTS=${PROJECTS:?pass the project set: PROJECTS="a/b c/d" scripts/fetch_chromium.sh}
MONTHS="2024-11 2024-12 2025-01 2025-02 2025-03 2025-04 2025-05 2025-06 2025-07 2025-08 2025-09 2025-10"
CUTOFF=2024-11-01
ORG=chromium

cd "$(dirname "$0")/.." || exit 1
MAIN=$(cd "$(git rev-parse --git-common-dir)/.." && pwd)
ROOT=${ROOT:-$MAIN/datasets/gerrit}
set -a; . "$MAIN/.env"; set +a

project_args=()
clause=""
for p in $PROJECTS; do
  project_args+=(--project "$p")
  clause="${clause:+$clause OR }project:$p"
done
clause="($clause)"

for m in $MONTHS; do
  raw="$ROOT/$ORG/raw/$m"
  # The record is written after the snapshot, so a snapshot without one is a partial write.
  # A complete snapshot is immutable and is never refetched: build resumes from it.
  if [ -s "$raw.ndjson.gz" ] && [ -s "$raw.record.json" ]; then
    recorded=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["query"])' "$raw.record.json")
    if [[ "$recorded" != *" $clause" ]]; then
      echo "$m: already fetched under a different project set ($recorded); stopping"
      exit 1
    fi
    echo "=== $m already fetched, skipping fetch ==="
    continue
  fi
  overwrite=()
  [ -e "$raw.ndjson.gz" ] && overwrite=(--overwrite)
  echo "=== $m fetch $(date '+%F %T') ==="
  uv run --no-sync --no-active python -m sphragis.corpus fetch \
    --org "$ORG" --month "$m" --root "$ROOT" --cutoff "$CUTOFF" "${project_args[@]}" \
    "${overwrite[@]}" --request-interval 1.0 || { echo "$m: fetch failed, stopping"; exit 1; }
done

# Build walks every snapshot, skips months already built from the snapshot on disk, and
# rebuilds any month whose record says it was left partial.
echo "=== build $(date '+%F %T') ==="
uv run --no-sync --no-active python -m sphragis.corpus build \
  --org "$ORG" --root "$ROOT" --cutoff "$CUTOFF" --request-interval 1.0 \
  || { echo "build failed, stopping"; exit 1; }
echo "=== chromium complete $(date '+%F %T') ==="
