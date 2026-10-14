# Deployment

TransPodcast has three deployment surfaces:

1. TransPodcast Host on macOS, which owns workflow state and the local web interface;
2. Modal speech applications, which execute pinned GPU stages;
3. the Cloudflare publication Worker and private R2 bucket.

Start with the [macOS host](macos.md), then configure [Modal](modal.md), and add
[Cloudflare publication](cloudflare.md) only when a remote private feed is
needed. Local storage mode is sufficient for development.

Deployment commands can create infrastructure, incur cost, restart services,
or expose media. Review the target configuration before running them.
No deployment is part of `make check`.

## Prerequisites

Before deploying real processing:

- `make check` passes from a clean checkout.
- Xcode Command Line Tools provide Git and make on the macOS host.
- `.env` exists locally and is not tracked by Git.
- Only content that may be used for learning or research will be processed.
- The selected Agent CLI is installed and authenticated for the same macOS
  user that runs TransPodcast: `codex --version` and `codex login status` for
  Codex, or `claude --version` and `claude auth status` for Claude Code.
- Google Chrome or another Chromium-based browser is installed for YouTube
  source downloads.
- Modal CLI is authenticated to the intended workspace.
- Cloudflare and R2 are configured only if remote private listening is needed.
- Feed tokens, account IDs, API tokens, and private URLs are kept out of Git and
  out of issue reports.
- Cloudflare invocation logs, traces, and exports do not persist tokenized feed
  or media URLs.

## Order of operations

1. Configure local settings in `.env`.
2. Install and authenticate Codex CLI or Claude Code.
3. Deploy or verify the Modal speech apps for every configured profile.
4. Start TransPodcast Host manually and inspect Setup Readiness. Starting the
   worker may contact configured providers, but does not start a GPU speech
   stage without a Processing Run.
5. Process a short episode before relying on long episodes.
6. Add Cloudflare R2 and the publication Worker for remote listening.
7. Approve a private preview and verify the RSS feed.
8. Follow the private feed by URL in Apple Podcasts.

For agent-assisted deployment, give the agent
[`docs/agent-deployment.md`](../agent-deployment.md) and require an explicit
approval checkpoint before any live provider, infrastructure, publication, or
secret-changing action.

## Apple Podcasts

The supported TransPodcast path is private RSS by URL. Public Apple Podcasts
catalog submission is outside the intended learning and research workflow. See
[Apple Podcasts and Private RSS](../publishing/apple-podcasts.md).
