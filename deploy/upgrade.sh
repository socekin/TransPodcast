#!/bin/bash
# TransPodcast host upgrade: run after `git pull` on the host.
# Syncs dependencies, rebuilds the console, runs the test gate, deploys
# changed Modal pipeline apps, restarts services, and verifies health.
#
# Usage: ./deploy/upgrade.sh [--skip-tests] [--skip-modal]
# Never touches .env, tokens, SQLite state, or R2 content.
set -euo pipefail

cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"
STATE_FILE="$REPO_ROOT/data/deploy-state"
SKIP_TESTS=false
SKIP_MODAL=false
for argument in "$@"; do
  case "$argument" in
    --skip-tests) SKIP_TESTS=true ;;
    --skip-modal) SKIP_MODAL=true ;;
    *) echo "Unknown option: $argument" >&2; exit 2 ;;
  esac
done

step() { printf '\n==> %s\n' "$1"; }

ensure_processing_idle() {
  active_work="$(uv run python - <<'PY'
import sqlite3
from pathlib import Path

from transpodcast.config import TransPodcastSettings

database_path = TransPodcastSettings().database_path.expanduser()
if not database_path.is_absolute():
    database_path = Path.cwd() / database_path
if not database_path.is_file():
    raise SystemExit(0)

checks = {
    "processing_runs": ("queued", "processing", "retry_wait"),
    "segment_revision_jobs": ("queued", "processing"),
    "transcript_revision_jobs": ("queued", "processing"),
}
connection = sqlite3.connect(database_path)
try:
    existing_tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    for table, statuses in checks.items():
        if table not in existing_tables:
            continue
        placeholders = ", ".join("?" for _ in statuses)
        count = connection.execute(
            f"SELECT COUNT(*) FROM {table} "
            f"WHERE status IN ({placeholders})",
            statuses,
        ).fetchone()[0]
        if count:
            print(f"{table}: {count}")
finally:
    connection.close()
PY
)"
  if [ -n "$active_work" ]; then
    echo "Processing work is active; upgrade was not started:" >&2
    echo "$active_work" >&2
    echo "Wait for the work to finish, then run the upgrade again." >&2
    exit 1
  fi
}

step "Preflight"
if [ -n "$(git status --porcelain)" ]; then
  echo "Working tree is not clean; commit or stash first." >&2
  exit 1
fi
CURRENT_COMMIT="$(git rev-parse HEAD)"
LAST_COMMIT="$(cat "$STATE_FILE" 2>/dev/null || echo "")"
echo "HEAD: $CURRENT_COMMIT"
echo "Last deployed: ${LAST_COMMIT:-never}"
ensure_processing_idle

step "Dependencies"
make setup

if [ "$SKIP_TESTS" = false ]; then
  step "Validation gate"
  make check \
    || { echo "Tests failed; aborting before restart." >&2; exit 1; }
else
  step "Test gate skipped (--skip-tests)"
  make build
fi

if [ "$SKIP_MODAL" = false ]; then
  step "Modal pipeline apps"
  ./deploy/modal-deploy.sh
else
  step "Modal deploys skipped (--skip-modal)"
fi

step "Restart services"
# Recheck after builds and tests because work may have been queued while
# the upgrade was preparing the new release.
ensure_processing_idle
# Migrations apply automatically on service startup.
UID_NUMBER="$(id -u)"
for service in com.transpodcast.api com.transpodcast.worker; do
  if launchctl print "gui/$UID_NUMBER/$service" > /dev/null 2>&1; then
    launchctl kickstart -k "gui/$UID_NUMBER/$service"
    echo "Restarted $service"
  else
    echo "LaunchAgent $service is not installed; run" \
      "./deploy/install-launchd.sh, or start manually with" \
      "'uv run transpodcast-api' and 'uv run transpodcast-worker'."
  fi
done

step "Health check"
sleep 3
for attempt in 1 2 3 4 5; do
  if curl -sf http://127.0.0.1:8000/api/v1/setup-readiness > /dev/null; then
    echo "Control API is healthy."
    break
  fi
  if [ "$attempt" = 5 ]; then
    echo "Control API did not respond; check data/logs." >&2
    exit 1
  fi
  sleep 3
done

mkdir -p "$(dirname "$STATE_FILE")"
echo "$CURRENT_COMMIT" > "$STATE_FILE"
step "Upgrade complete at $CURRENT_COMMIT"
