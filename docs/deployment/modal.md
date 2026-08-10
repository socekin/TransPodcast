# Modal Speech Applications

The Modal applications under `experiments/pipeline/` provide the required GPU
speech stages used by TransPodcast Host. Deployment builds the pinned images,
downloads model assets, and can incur storage or compute charges.

## Preparation

1. Install the locked project dependencies with `make setup`.
2. Review every model ID and its terms in
   [Third-party models](../model-licenses.md).
3. Configure provider-side spending limits for every Modal workspace.
4. Confirm that source and voice-reference data may be sent to Modal.

Modal credentials are stored in the local Modal profile configuration. Create
and verify profiles as the same macOS user that runs TransPodcast. Never copy
Modal credentials or `~/.modal.toml` into this repository.

## Single-account configuration

Create one local profile and the dedicated environment:

```shell
uv run modal token new --profile transpodcast
MODAL_PROFILE=transpodcast uv run modal token info
MODAL_PROFILE=transpodcast uv run modal environment list
MODAL_PROFILE=transpodcast uv run modal environment create transpodcast
```

Skip the final command when `transpodcast` already appears in the environment
list. Configure `.env`:

```dotenv
TRANSPODCAST_MODAL_ENVIRONMENT=transpodcast
TRANSPODCAST_MODAL_COMMAND=uv run transpodcast-modal-pipeline
```

## Multi-account routing

Multi-account routing is optional. It uses local Modal profile names to route a
GPU operation across multiple authenticated workspaces in priority order. Use
the same Modal environment name in every workspace so deployment and runtime
lookups resolve the same four applications.

The following example configures two workspaces. Replace the profile names with
local names that identify the intended accounts:

```shell
uv run modal token new --profile transpodcast-primary --no-activate
uv run modal token new --profile transpodcast-secondary --no-activate

MODAL_PROFILE=transpodcast-primary uv run modal token info
MODAL_PROFILE=transpodcast-secondary uv run modal token info

MODAL_PROFILE=transpodcast-primary uv run modal environment list
MODAL_PROFILE=transpodcast-secondary uv run modal environment list

MODAL_PROFILE=transpodcast-primary \
  uv run modal environment create transpodcast
MODAL_PROFILE=transpodcast-secondary \
  uv run modal environment create transpodcast
```

Skip an environment creation command when that workspace already contains the
`transpodcast` environment. Then configure the complete routing block in
`.env`:

```dotenv
TRANSPODCAST_MODAL_ENVIRONMENT=transpodcast
TRANSPODCAST_MODAL_COMMAND=uv run transpodcast-modal-router
TRANSPODCAST_MODAL_PROFILES=transpodcast-primary,transpodcast-secondary
TRANSPODCAST_MODAL_CREDIT_THRESHOLD_USD=2
TRANSPODCAST_MODAL_MONTHLY_ALLOWANCE_USD=30
```

`TRANSPODCAST_MODAL_PROFILES` is evaluated from left to right. The monthly
allowance applies to each listed workspace, and the threshold skips a workspace
when its estimated remaining monthly credit falls below that value. These
values are routing guardrails, not provider-side budgets.

Confirm that the router reads the intended order before deployment:

```shell
uv run transpodcast-modal-router --print-profiles
```

Expected output for the example:

```text
transpodcast-primary
transpodcast-secondary
```

See Modal's official documentation for
[tokens](https://modal.com/docs/cli/latest/token),
[profiles](https://modal.com/docs/cli/latest/profile), and
[environments](https://modal.com/docs/guide/environments).

## Deploy

From the repository root, review the active `.env`, approve the external
deployment, and run:

```shell
./deploy/modal-deploy.sh
```

In single-account mode, the script deploys changed applications to
`TRANSPODCAST_MODAL_ENVIRONMENT` and records the successful Git commit in
`data/deploy-state`. In multi-account mode, it deploys every application under
each profile and records each successful profile in
`data/deploy-state.d/<profile>`. A second run skips unchanged applications.

## Verify

For a multi-account deployment, query every profile without activating it.
The array syntax below works in the default macOS zsh shell and preserves each
profile as a separate value:

```shell
modal_env=transpodcast
modal_profiles=(transpodcast-primary transpodcast-secondary)
modal_readiness_dir=.cache/modal-readiness

mkdir -p "$modal_readiness_dir"
printf '{}\n' > "$modal_readiness_dir/request.json"

for modal_profile in "${modal_profiles[@]}"; do
  MODAL_PROFILE="$modal_profile" \
    uv run modal token info
  MODAL_PROFILE="$modal_profile" \
    uv run modal app list --env "$modal_env"
  MODAL_PROFILE="$modal_profile" \
    uv run modal billing summary --json
  MODAL_PROFILE="$modal_profile" \
    TRANSPODCAST_MODAL_ENVIRONMENT="$modal_env" \
    uv run transpodcast-modal-pipeline \
      --operation readiness \
      --request "$modal_readiness_dir/request.json" \
      --output "$modal_readiness_dir/$modal_profile.json"
  cat "$modal_readiness_dir/$modal_profile.json"
done
```

Each application list must include:

```text
transpodcast-pipeline-sortformer
transpodcast-pipeline-qwen3-asr
transpodcast-pipeline-speaker-embeddings
transpodcast-pipeline-indextts2
```

The billing command must return valid JSON for each profile. Every readiness
result must contain `"ready": true` and list all four components. The direct
pipeline command checks application metadata for one profile at a time; it does
not process episode media or start a GPU speech stage. If billing data is
unavailable, the router records that condition and still attempts the profile;
provider-side spending limits remain the authoritative cost control.

Restart TransPodcast after changing `.env`, open **Settings**, and confirm that
Modal passes the aggregate Setup Readiness check. This final check confirms that
the configured router can select an eligible profile. Multi-account routing
decisions are recorded in the ignored `data/logs/modal-router.log` file for
local troubleshooting.
