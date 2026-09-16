#!/usr/bin/env bash
# Finish a month's build once the host is answering again, and not one request sooner.
#
# Usage: scripts/resume_when_allowed.sh <org> <month> [cutoff]
#
# Qt's Gerrit stopped completing handshakes after hours of collection at five requests a
# second, and stayed shut long after the traffic stopped. The snapshot for the month is
# already on disk and complete; only the build, which needs one comments request per change
# and one diff request per comment, is blocked. So this waits rather than refetches.
#
# The etiquette the earlier incident taught, encoded:
#   - probe with a handful of requests, not a build attempt, so a closed door costs the
#     server five requests instead of thousands;
#   - require the probe to be nearly perfect before trying, because one request getting
#     through is luck and was misread as recovery once already;
#   - back off much harder after a failed build than after a failed probe, since a failed
#     build means we were let in and then shut out again.
set -u
ORG=${1:?usage: resume_when_allowed.sh <org> <month> [cutoff]}
MONTH=${2:?usage: resume_when_allowed.sh <org> <month> [cutoff]}
CUTOFF=${3:-2024-10-01}

case "$ORG" in
  qt) HOST=https://codereview.qt-project.org ;;
  openstack) HOST=https://review.opendev.org ;;
  *) echo "unknown org $ORG"; exit 1 ;;
esac

PROBE_EVERY=${PROBE_EVERY:-1200}   # 20 minutes between probes
AFTER_FAIL=${AFTER_FAIL:-3600}     # an hour after being let in and shut out again
NEEDED=${NEEDED:-5}                # of 5 probe requests

cd "$(dirname "$0")/.." || exit 1
set -a; . ./.env; set +a

attempt=0
while :; do
  attempt=$((attempt + 1))
  ok=0
  for _ in 1 2 3 4 5; do
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$HOST/config/server/version")
    [[ "$code" == "200" ]] && ok=$((ok + 1))
    sleep 5
  done
  echo "$(date '+%m-%d %H:%M') probe $attempt: $ok/5 answered"

  if [[ "$ok" -lt "$NEEDED" ]]; then
    sleep "$PROBE_EVERY"
    continue
  fi

  echo "$(date '+%m-%d %H:%M') building $ORG $MONTH"
  if uv run --no-active python -m sphragis.corpus build \
       --org "$ORG" --month "$MONTH" --cutoff "$CUTOFF" --request-interval 1.0; then
    echo "$(date '+%m-%d %H:%M') built $ORG $MONTH: $(wc -l < "datasets/gerrit/$ORG/examples/$MONTH.jsonl") examples"
    exit 0
  fi
  echo "$(date '+%m-%d %H:%M') build failed after being admitted; backing off"
  sleep "$AFTER_FAIL"
done
