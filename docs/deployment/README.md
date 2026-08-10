# Deployment

TransPodcast has three deployment surfaces:

1. the macOS host, which owns workflow state and the local console;
2. Modal speech applications, which execute pinned GPU stages;
3. the Cloudflare publication Worker and private R2 bucket.

Start with the [macOS host](macos.md), then configure [Modal](modal.md), and add
[Cloudflare publication](cloudflare.md) only when a remote private feed is
needed. Local storage mode is sufficient for development.

Deployment commands can create infrastructure, incur cost, restart services,
or expose media. Review configuration and authorization before running them.
No deployment is part of `make check`.

## Prerequisites

Before deploying real processing:

- `make check` passes from a clean checkout.
- `.env` exists locally and is not tracked by Git.
- The source media and voice-use rights are documented.
- Codex CLI is authenticated locally.
- Modal CLI is authenticated to the intended workspace.
- Cloudflare and R2 are configured only if remote private listening is needed.
- Feed tokens, account IDs, API tokens, and private URLs are kept out of Git and
  out of issue reports.

## Order of operations

1. Configure local settings in `.env`.
2. Start the TransPodcast host manually and inspect Setup Readiness.
3. Deploy or verify Modal speech apps.
4. Process a short authorized episode before relying on long episodes.
5. Add Cloudflare R2 and the publication Worker for remote listening.
6. Approve a private preview and verify the RSS feed.
7. Follow the private feed by URL in Apple Podcasts.

For agent-assisted deployment, give the agent
[`docs/agent-deployment.md`](../agent-deployment.md) and require an explicit
approval checkpoint before any live provider, infrastructure, publication, or
secret-changing action.

## Apple Podcasts

The normal TransPodcast path is private RSS by URL. Public Apple Podcasts
catalog submission is separate and requires Apple Podcasts Connect validation,
content-rights review, stable HTTPS hosting, and platform metadata. See
[Apple Podcasts and Private RSS](../publishing/apple-podcasts.md).
