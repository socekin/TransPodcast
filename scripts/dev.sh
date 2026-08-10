#!/bin/sh
set -eu

project_root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_root"

api_pid=""
worker_pid=""

# Invoked by the EXIT trap.
# shellcheck disable=SC2329
stop_services() {
  trap - INT TERM EXIT
  if [ -n "$api_pid" ]; then
    kill "$api_pid" 2>/dev/null || true
  fi
  if [ -n "$worker_pid" ]; then
    kill "$worker_pid" 2>/dev/null || true
  fi
  wait 2>/dev/null || true
}

process_is_running() {
  process_state=$(ps -o stat= -p "$1" 2>/dev/null | tr -d ' ')
  [ -n "$process_state" ] && [ "${process_state#Z}" = "$process_state" ]
}

trap stop_services EXIT
trap 'exit 130' INT TERM

uv run transpodcast-api &
api_pid=$!
uv run transpodcast-worker &
worker_pid=$!

printf '%s\n' \
  "TransPodcast is starting at http://transpodcast.localhost:8000" \
  "Press Ctrl-C to stop the API and worker."

while process_is_running "$api_pid" && process_is_running "$worker_pid"; do
  sleep 1
done

printf '%s\n' "The API or worker stopped unexpectedly."
exit 1
