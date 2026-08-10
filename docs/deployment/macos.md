# macOS Host

## Manual operation

After completing the local setup:

```shell
uv run transpodcast-api
```

In another terminal:

```shell
uv run transpodcast-worker
```

This is the recommended first real setup because logs stay visible.

## LaunchAgents

`deploy/install-launchd.sh` installs per-user LaunchAgents for the API, worker,
and monthly spend-guardrail resume. Read the generated plist templates first,
then run:

```shell
./deploy/install-launchd.sh
```

Logs and runtime state stay under `data/`. The script uses the current checkout
and shell `PATH`; move the checkout only after reinstalling the agents.

## Upgrades

On an installed TransPodcast host, `deploy/upgrade.sh` synchronizes dependencies,
builds and tests the project, deploys changed Modal applications, and restarts
the local services. It refuses to start when the working tree is dirty or when
processing work is active.

Back up the database and artifacts before an upgrade. Do not use `--skip-tests`
for routine releases. The script never replaces `.env`, feed tokens, SQLite
state, or R2 objects.
