#!/usr/bin/env bash
# Wait for a Slurm job to leave the queue, then print its accounting and log tail.
#
# Usage: scripts/wait_for_job.sh <jobid> [poll_seconds]
#
# Exists because the obvious one-liner is wrong in a way that only shows up on long waits:
#
#     until ! ssh tigris "squeue -h -j $JOB | grep -q ."; do sleep 120; done
#
# `ssh` returns non-zero when *ssh itself* fails, not only when the job is gone, so one
# dropped connection satisfies the loop and reports a finished job that is still queued.
# Over a three-hour wait that is a gamble; over the thirty-hour queue waits this cluster
# hands out at low fairshare it is close to certain.
#
# The rule this encodes: a remote predicate must be able to say "no answer" distinctly from
# "no". Every poll has to carry the sentinel for its answer to count at all, and absence has
# to be seen twice before it is believed.
set -u
JOB=${1:?usage: wait_for_job.sh <jobid> [poll_seconds]}
EVERY=${2:-300}
HOST=${TIGRIS_HOST:-tigris}
LOGDIR=${SPHRAGIS_LOGDIR:-\~/ajsoftworks/sphragis/logs}

gone=0; blips=0; polls=0
while :; do
  out=$(ssh -o ConnectTimeout=30 -o BatchMode=yes "$HOST" \
        "squeue -h -j $JOB -o 'STATE=%T ELAPSED=%M LEFT=%L' 2>/dev/null; echo POLL_OK" 2>/dev/null)
  polls=$((polls + 1))
  if [[ "$out" != *POLL_OK* ]]; then
    blips=$((blips + 1)); gone=0
    echo "poll $polls $(date +%H:%M): no answer from $HOST (blip $blips)"
    sleep "$EVERY"; continue
  fi
  if [[ -n "$(grep -o 'STATE=[A-Z_]*' <<<"$out" | head -1)" ]]; then
    gone=0
    if [[ $((polls % 12)) -eq 1 ]]; then
      echo "poll $polls $(date +%H:%M): $(tr '\n' ' ' <<<"${out%POLL_OK*}")"
    fi
  else
    gone=$((gone + 1))
    echo "poll $polls $(date +%H:%M): not in queue ($gone/2)"
    if [[ $gone -ge 2 ]]; then break; fi
  fi
  sleep "$EVERY"
done

echo "=== job $JOB left the queue after $polls polls, $blips unanswered ==="
ssh -o ConnectTimeout=30 "$HOST" "
  sacct -j $JOB -X -n -o JobID,State,ExitCode,Elapsed,MaxRSS 2>/dev/null
  echo '--- log tail ---'
  tail -c 8000 $LOGDIR/*-$JOB.log 2>/dev/null \
    | tr '\r' '\n' | grep -av 'Loading weights\|it/s\]\|Warning\|warn' | tail -40
"
