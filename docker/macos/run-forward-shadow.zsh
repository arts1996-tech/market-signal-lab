#!/bin/zsh

set -u

PROJECT_DIR="/Users/tsurusumu/Projects/market-signal-lab"
DOCKER_BIN="/usr/local/bin/docker"
HOST_LOG="$PROJECT_DIR/logs/forward-shadow-host-attempts.tsv"
OBSERVED_AT="$(/bin/date -u '+%Y-%m-%dT%H:%M:%SZ')"
ATTEMPT_ID="host-$(/bin/date -u '+%Y%m%dT%H%M%SZ')-$$"

/bin/mkdir -p "$PROJECT_DIR/logs"
cd "$PROJECT_DIR" || exit 72

record_attempt() {
  /usr/bin/printf '%s\t%s\t%s\t%s\t%s\n' \
    "$OBSERVED_AT" "$ATTEMPT_ID" "$1" "$2" "$3" >> "$HOST_LOG"
}

if [[ ! -x "$DOCKER_BIN" ]] || ! "$DOCKER_BIN" info >/dev/null 2>&1; then
  record_attempt "error" "docker_unavailable" "75"
  exit 75
fi

"$DOCKER_BIN" compose run --rm app \
  python jobs/run_forward_shadow.py --daily --not-before-jst 18:30
STATUS=$?

if [[ $STATUS -eq 0 ]]; then
  record_attempt "success" "completed" "0"
  exit 0
fi

if ! "$DOCKER_BIN" compose exec -T db \
  sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >/dev/null 2>&1; then
  record_attempt "error" "database_unavailable" "$STATUS"
else
  record_attempt "error" "forward_job_failed" "$STATUS"
fi
exit "$STATUS"
