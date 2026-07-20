#!/bin/zsh
# TriPeak daily sync: Strava -> Postgres -> metrics -> dashboard -> Databricks.
# Safe to re-run any time: every load is an upsert keyed on (source, external_id),
# so duplicates are impossible.
#
# Scheduled via launchd: ~/Library/LaunchAgents/com.tripeak.dailysync.plist
# Logs: logs/daily_sync.log

set -u
DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$DIR/logs/daily_sync.log"
mkdir -p "$DIR/logs"
PY="$DIR/.venv/bin/python"

exec >> "$LOG" 2>&1
echo "===== $(date '+%Y-%m-%d %H:%M:%S') daily sync start ====="

[ -f "$HOME/.ironman.env" ] && source "$HOME/.ironman.env"

fail=0
step() {
  echo "--- $1"
  shift
  "$@" || { echo "!!! step failed (exit $?)"; fail=1; }
}

# 7-day lookback: overlaps yesterday's window; upsert dedupes overlap.
step "strava sync"        "$PY" "$DIR/etl/strava_sync.py" --days 7
step "compute metrics"    "$PY" "$DIR/etl/compute_metrics.py"
step "rebuild dashboard"  "$PY" "$DIR/dashboard/build_dashboard.py"
# Cloud refresh is best-effort: needs a valid `databricks auth login` session.
step "databricks replicate" "$PY" "$DIR/etl/replicate_to_databricks.py"

echo "===== $(date '+%Y-%m-%d %H:%M:%S') done (fail=$fail) ====="
exit $fail
