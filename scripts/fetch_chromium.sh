#!/usr/bin/env bash
# Collect Chromium over the train and dev windows, through the CLI and nothing else.
#
# Usage: PROJECTS="v8/v8 angle/angle ..." scripts/fetch_chromium.sh
#        (restartable: rerun it with the same PROJECTS after any interruption)
#
# Over git (`--via git`), the same route AOSP uses: chromium-review.googlesource.com's
# robots.txt disallows an automated REST client, but chromium.googlesource.com serves NoteDb.
# Refuses to run while chromium.googlesource.com is not recorded permitted in
# `sphragis.corpus.notedb.GIT_PERMITTED` -- read from there, not duplicated here, so a later
# grant of permission is one change to that table, not to this script too.
#
# The project set has no default: scripts/host_scoping.py found none that meets the split
# criteria, so it is a decision to pass in, and a month refetched under a different set would
# mix corpora.
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

if ! uv run --no-sync --no-active python -c "
from sphragis.corpus.notedb import GIT_PERMITTED
permission = GIT_PERMITTED.get('chromium.googlesource.com')
raise SystemExit(0 if permission and permission.permitted else 1)
"; then
  echo "refusing to run: chromium.googlesource.com is not permitted in" \
    "sphragis.corpus.notedb.GIT_PERMITTED; bulk Chromium collection waits for the host's" \
    "permission"
  exit 1
fi

set -a; . "$MAIN/.env"; set +a

project_args=()
for p in $PROJECTS; do
  project_args+=(--project "$p")
done
current_projects=$(printf '%s\n' $PROJECTS | sort | tr '\n' ' ')
current_projects=${current_projects% }

for m in $MONTHS; do
  raw="$ROOT/$ORG/raw/$m"
  # The record is written after the snapshot, so a snapshot without one is a partial write.
  # A complete snapshot is immutable and is never refetched: build resumes from it.
  if [ -s "$raw.ndjson.gz" ] && [ -s "$raw.record.json" ]; then
    recorded=$(python3 -c '
import json, sys
record = json.load(open(sys.argv[1]))
print(" ".join(sorted(record.get("projects", {}))))
' "$raw.record.json")
    if [[ "$recorded" != "$current_projects" ]]; then
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
    --via git --org "$ORG" --month "$m" --root "$ROOT" --cutoff "$CUTOFF" \
    "${project_args[@]}" "${overwrite[@]}" --request-interval 1.0 \
    || { echo "$m: fetch failed, stopping"; exit 1; }
done

# Build walks every snapshot, skips months already built from the snapshot on disk, and
# rebuilds any month whose record says it was left partial.
echo "=== build $(date '+%F %T') ==="
uv run --no-sync --no-active python -m sphragis.corpus build \
  --org "$ORG" --root "$ROOT" --cutoff "$CUTOFF" --request-interval 1.0 \
  || { echo "build failed, stopping"; exit 1; }
echo "=== chromium complete $(date '+%F %T') ==="
