# Roadmap

The roadmap describes direction, not a delivery guarantee. Issues selected for
implementation should define their own acceptance criteria.

## Public preview

- Finish the public data and Git-history audit.
- Publish synthetic end-to-end demo fixtures.
- Add reproducible CI across supported Python and Node.js versions.
- Document every model and external-provider license.
- Provide rights-cleared audio comparisons.

## Adoption

- Add a guided setup wizard and one-command diagnostics.
- Make source, translation-agent, speech, storage, notification, and
  publication adapters easier to replace.
- Add a local or self-hosted speech backend that does not require Modal.
- Add import paths for owner-provided local media and RSS enclosures.
- Improve restore automation and disaster-recovery validation.

## Sustainability

- Publish versioned releases and migration notes.
- Maintain a small set of synthetic quality baselines.
- Split `TransPodcastService` into focused source, processing, publication,
  speaker, notification, and maintenance services.
- Expand contributor-friendly module boundaries in the Owner Console.
- Define long-term support and deprecation windows after the first stable
  release.
