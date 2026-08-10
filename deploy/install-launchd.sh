#!/bin/sh
set -eu

project_root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
launch_agents_dir="$HOME/Library/LaunchAgents"
log_dir="$project_root/data/logs"
runtime_path="$PATH"

mkdir -p "$launch_agents_dir" "$log_dir"

install_agent() {
  label=$1
  start=${2:-kickstart}
  template="$project_root/deploy/launchd/$label.plist"
  destination="$launch_agents_dir/$label.plist"

  sed \
    -e "s|__TRANSPODCAST_ROOT__|$project_root|g" \
    -e "s|__TRANSPODCAST_PATH__|$runtime_path|g" \
    "$template" > "$destination"
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  # Bootout teardown can race the following bootstrap and fail with a
  # spurious I/O error; retry so a transient failure cannot abort the
  # script and leave a service unloaded.
  bootstrapped=false
  for _attempt in 1 2 3 4 5; do
    if launchctl bootstrap "gui/$(id -u)" "$destination" 2>/dev/null; then
      bootstrapped=true
      break
    fi
    sleep 1
  done
  if [ "$bootstrapped" = false ]; then
    echo "Failed to bootstrap $label; services may need manual repair." >&2
    exit 1
  fi
  launchctl enable "gui/$(id -u)/$label"
  if [ "$start" = "kickstart" ]; then
    launchctl kickstart -k "gui/$(id -u)/$label"
  fi
}

install_agent com.transpodcast.api
install_agent com.transpodcast.worker
# Scheduled-only: kickstarting would fire an immediate resume at install
# time, which could lift a legitimate mid-month Spend Guardrail pause.
install_agent com.transpodcast.spend-guardrail-resume schedule-only

printf '%s\n' \
  "TransPodcast API, worker, and spend-guardrail-resume LaunchAgents" \
  "are installed."
