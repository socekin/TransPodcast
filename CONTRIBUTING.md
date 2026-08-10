# Contributing to TransPodcast

Thank you for helping improve TransPodcast. Focused bug reports,
documentation, tests, accessibility fixes, and well-scoped implementation
changes are welcome.

## Before opening an issue

- Search existing issues and the [troubleshooting guide](docs/troubleshooting.md).
- Remove credentials, private feed URLs, account IDs, real transcripts, media,
  logs, local paths, and provider payloads.
- Use a minimal synthetic reproduction.
- Use GitHub Security Advisories for vulnerabilities, not a public issue.

## Development workflow

1. Fork the repository and create a focused branch.
2. Read `AGENTS.md` and [Development](docs/development.md).
3. Add or update tests for behavioral changes.
4. Run `make check`.
5. Update documentation and `CHANGELOG.md` when behavior changes.
6. Open a pull request using the repository template.

Keep pull requests small enough to review. Do not combine formatting,
dependency upgrades, and unrelated behavior in one change.

## Public data boundary

Only fictional, newly authored test data may be contributed by default. Do not
submit real source audio, voice references, transcripts, embeddings, generated
speech, screenshots containing private data, production databases, or logs.

An authorized media example requires prior maintainer agreement and the full
provenance record described in [Audio comparisons](docs/audio-comparisons.md).

## Code and documentation standards

- Use English for code, comments, engineering documentation, commits, issues,
  and pull requests. `README.md` is the Simplified Chinese product README;
  keep its structure and claims synchronized with `README.en.md`.
- Follow existing domain terms in `docs/domain-model.md`.
- Keep provider calls behind adapter interfaces.
- Keep tests deterministic and free of live or billable provider calls.
- Use Conventional Commits for commit messages.

## Contribution licensing

The project is source-available under the
[PolyForm Noncommercial License 1.0.0](LICENSE.md) and offers separate
commercial licenses.

By submitting a pull request, you agree to [CLA.md](CLA.md). The CLA lets Jay
use accepted contributions in noncommercial public releases and in separate
commercial licenses while you retain ownership of your contribution.

Issues, design discussion, documentation corrections, tests, and narrowly
scoped implementation changes are welcome. Large or organization-sponsored
contributions may require additional maintainer review before they are merged.

By participating, you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).
