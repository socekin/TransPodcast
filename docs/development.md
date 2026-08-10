# Development

## Repository setup

```shell
make setup
make check
```

The test suite is offline and deterministic. A test that requires a live
provider, personal account, or production artifact does not belong in the
default public test target.

## Common commands

| Command | Purpose |
| --- | --- |
| `make dev` | Build the console and start the local API and worker |
| `make lint` | Run Ruff over Python code |
| `make typecheck` | Run strict mypy and both TypeScript checks |
| `make test` | Run backend, console, hosting, and Worker tests |
| `make build` | Build the TransPodcast Host web interface |
| `make check` | Run the complete local merge gate, including docs and secrets |
| `make security-check` | Scan for secrets and audit locked dependencies |
| `make generate-api` | Regenerate the typed frontend API client |

## Maintainer workflow

1. Create a focused local change.
2. Read the domain and existing tests around the change.
3. Add or update a failing test for behavioral work.
4. Make the smallest coherent implementation.
5. Run focused checks, then `make check`.
6. Update user documentation and `CHANGELOG.md`.
7. Inspect the diff for secrets, private data, generated media, and accidental
   dependency changes.

The merge gate also checks local documentation links and scans the repository
for likely secrets. The backend test gate enforces at least 80% line coverage.
Coverage is a safety net rather than a substitute for behavior-focused tests;
changes to pipeline behavior should include a regression test that demonstrates
the expected user outcome.

Use Conventional Commits. Keep source code, comments, documentation, commits,
and release notes in English.

## Tests and fixtures

Tests should use deterministic adapters and synthetic content. Do not use a
real transcript merely because it is publicly accessible. A public regression
fixture should be newly authored, minimal, fictional, and contain only the data
needed to reproduce the behavior.

Mark no test as passing by weakening a production invariant. If an external
contract cannot be tested offline, isolate its parser or request builder and
test that boundary.

## Database changes

Add a new Alembic migration and an acceptance test. Never modify a migration
included in a released version. Exercise both a clean database and upgrade from
the preceding schema where relevant.

## API changes

Regenerate the frontend client with `make generate-api`, then review the
generated diff. Update API consumers and tests in the same change.

## Dependency changes

Keep runtime dependencies minimal. Update lockfiles with the ecosystem's
package manager, run `make security-check`, and describe meaningful license or
deployment implications in the change record.
