# Security Policy

## Supported versions

Security fixes are provided for the latest tagged release. During public
preview, users should run the latest commit on the default branch only when
they can review and test it in their own environment.

## Reporting a vulnerability

Do not open a public issue. Use the repository's **Security** tab and select
**Report a vulnerability** to open a private GitHub Security Advisory:

<https://github.com/socekin/TransPodcast/security/advisories/new>

Include the affected version, impact, minimal reproduction, and any suggested
mitigation. Remove real credentials, private feed tokens, copyrighted media,
and personal data. If a secret was exposed, revoke it first and report only a
redacted identifier.

The maintainer will acknowledge a complete report within seven days, provide a
status update within fourteen days, and coordinate remediation and disclosure.
These are response targets, not guarantees.

## Security-sensitive surfaces

- private feed and preview authorization;
- R2 object isolation and immutable media paths;
- source URLs and untrusted provider output;
- subprocess and FFmpeg invocation;
- agent prompt and structured-output boundaries;
- webhook and Telegram owner authorization;
- SQLite migrations and revision history;
- logs, exported reports, backups, and error summaries.

## Operator responsibilities

Use least-privilege provider tokens, keep `.env` and `data/` private, restrict
the TransPodcast host to trusted users, back up encrypted state, and revoke a feed
token immediately if its URL is exposed. Never make the publication R2 bucket
public.

The public repository intentionally contains no production secrets. A scanner
finding a placeholder such as `ACCOUNT_ID` is not evidence of a live credential.
