# Coding Agent Guide

TransPodcast is designed to be maintainable with Codex, Claude Code, Cursor,
GitHub Copilot, and other coding agents. Repository-wide constraints live in
[`AGENTS.md`](../AGENTS.md); give that file to any agent before it edits code.

## Recommended first prompt

```text
Read AGENTS.md, README.md, docs/architecture.md, and the files related to this
task. Describe the intended change and the smallest relevant tests before
editing. Do not call live providers, deploy, publish, or add real media. Run the
focused tests during the change and make check before finishing.
```

## Context map

| Task | Read first |
| --- | --- |
| Workflow or API behavior | `src/transpodcast/service.py`, `api.py`, domain tests |
| Provider integration | `src/transpodcast/dependencies.py`, relevant adapter |
| Owner Console | `apps/owner-console/src/`, API client, UI tests |
| Speech pipeline | `src/transpodcast/modal_pipeline.py`, `experiments/pipeline/` |
| Publication edge | `workers/publication/src/`, Worker tests |
| Persistence | `database.py`, migration files, acceptance tests |
| Terminology | `docs/domain-model.md` |

## Safe boundaries for agents

Agents may run local tests, builds, linters, type checks, and read-only Git
inspection without provider access. They must receive explicit maintainer
approval before live source downloads, billable agent or GPU calls, deployment,
publication, secret changes, database migrations on real data, or release Git
operations.

Ask agents to use obviously fictional fixtures. Never let an agent solve a
failing public test by copying a production transcript, log, database, media
file, or secret into the repository.

## Effective task handoff

Include the user-visible outcome, files in scope, acceptance criteria, relevant
issue, and commands that must pass. For a bug, include the sanitized observed
behavior and a minimal reproduction. Do not paste a complete `.env`, provider
response, or private feed URL.

## Review prompts

After implementation, use separate review prompts for:

1. behavior and regression risk;
2. security, privacy, and secret exposure;
3. documentation and onboarding accuracy;
4. accessibility and localization for UI changes.

An agent's confidence is not verification. Require command output and inspect
the final diff before committing.
