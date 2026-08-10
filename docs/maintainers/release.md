# Release Process

1. Confirm the release scope, align component versions, and update
   `CHANGELOG.md`.
2. Review dependency licenses, model identifiers, and the provenance and rights
   record for every public media asset.
3. Run `make check` and `make security-check` from a clean checkout.
4. Scan the complete Git history and release artifacts for secrets and private
   data, not only the working tree.
5. Verify the Chinese and English READMEs, documentation links, repository
   metadata, and support links.
6. Verify CI, CodeQL, Dependabot, branch protection, and the GitHub Pages audio
   publication workflow on the release commit.
7. Verify installation and documentation from a clean machine or disposable
   environment.
8. Create a signed version tag and GitHub release with English release notes.
9. Upgrade a backed-up TransPodcast host only when processing is idle.
10. Verify API health, Setup Readiness, a private preview, and feed
    authorization.

Never publish real media, transcripts, logs, screenshots, database files, or
provider outputs unless the release checklist includes documented rights and a
specific review of those files.

For a security release, coordinate disclosure through `SECURITY.md` and avoid
describing an exploitable detail before users can upgrade.
