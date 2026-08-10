#!/bin/bash
# Deploy the Modal pipeline apps for the TransPodcast host.
#
# With TRANSPODCAST_MODAL_PROFILES configured (read through the router's
# --print-profiles query mode so one Python code path owns parsing), apps
# are deployed per profile with a per-profile deploy record. A profile
# without a record receives every app unconditionally, so a newly added
# Modal Account is fully served even when no app has changed. Without
# configured profiles, the legacy single-account behavior applies: deploy
# apps changed since the recorded commit under the active Modal profile.
set -euo pipefail

cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"
LEGACY_STATE_FILE="$REPO_ROOT/data/deploy-state"
PROFILE_STATE_DIR="$REPO_ROOT/data/deploy-state.d"
PROFILES_COMMAND=${TRANSPODCAST_MODAL_PROFILES_COMMAND:-"uv run transpodcast-modal-router --print-profiles"}
DEPLOY_COMMAND=${TRANSPODCAST_MODAL_DEPLOY_COMMAND:-"uv run modal deploy --env transpodcast"}

MODAL_APPS=(
  experiments/pipeline/modal_sortformer.py
  experiments/pipeline/modal_qwen3_asr.py
  experiments/pipeline/modal_speaker_embeddings.py
  experiments/pipeline/modal_indextts2_batch.py
)

changed_since() {
  commit=$1
  app=$2
  [ -z "$commit" ] || ! git diff --quiet "$commit" HEAD -- "$app"
}

# deploy_apps <last_commit> [profile] — deploy apps changed since
# <last_commit> (all apps when it is empty), under the given Modal
# profile or the active one when no profile is passed.
deploy_apps() {
  last_commit=$1
  profile=${2:-}
  for app in "${MODAL_APPS[@]}"; do
    if changed_since "$last_commit" "$app"; then
      echo "Deploying $app${profile:+ to Modal profile $profile}"
      if [ -n "$profile" ]; then
        MODAL_PROFILE="$profile" $DEPLOY_COMMAND "$app"
      else
        $DEPLOY_COMMAND "$app"
      fi
    else
      echo "Unchanged${profile:+ for profile $profile}: $app"
    fi
  done
}

profiles="$($PROFILES_COMMAND)"
current_commit="$(git rev-parse HEAD)"

if [ -z "$profiles" ]; then
  deploy_apps "$(cat "$LEGACY_STATE_FILE" 2>/dev/null || echo "")"
  exit 0
fi

mkdir -p "$PROFILE_STATE_DIR"
while IFS= read -r profile; do
  [ -n "$profile" ] || continue
  record="$PROFILE_STATE_DIR/$profile"
  deploy_apps "$(cat "$record" 2>/dev/null || echo "")" "$profile"
  echo "$current_commit" > "$record"
done <<< "$profiles"
