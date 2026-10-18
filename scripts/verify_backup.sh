#!/usr/bin/env bash
# Compare every file under LOCAL/<names> with its copy under REMOTE/<names> on HOST by sha256.
# The comparison is the check, not rsync's exit code. Lock files are skipped: they belong to a
# running collector, not to the corpus.
#   scripts/verify_backup.sh HOST LOCAL REMOTE NAME...
set -euo pipefail
host=$1 local=$2 remote=$3; shift 3
l=$(mktemp) && r=$(mktemp) && trap 'rm -f "$l" "$r"' EXIT
(cd "$local" && find "$@" -type f ! -path '*/.locks/*' -print0 | xargs -0 sha256sum) | LC_ALL=C sort -k2 >"$l"
# shellcheck disable=SC2029  # the names expand here, on purpose
ssh -o ConnectTimeout=20 "$host" "cd $remote && find $* -type f ! -path '*/.locks/*' -print0 | xargs -0 sha256sum" \
  | LC_ALL=C sort -k2 >"$r"
n=$(wc -l <"$l"); missing=$(LC_ALL=C comm -23 "$l" "$r" | wc -l)
if [ "$n" -eq 0 ] || [ "$missing" -ne 0 ]; then
  echo "backup: $missing of $n file(s) differ or are missing on $host" >&2; exit 1
fi
echo "backup: all $n files identical on $host"
