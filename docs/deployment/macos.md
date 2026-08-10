# macOS Host

Install Xcode Command Line Tools before setup so Git and `make` are available:

```shell
xcode-select --install
xcode-select -p
make --version
```

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
then verify that Codex authentication belongs to the current macOS user:

```shell
codex --version
codex login status
```

Install the agents only after both commands succeed:

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
