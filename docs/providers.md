# Providers and Replaceable Boundaries

TransPodcast is local-first, not provider-free. The current production-shaped
path uses the services below. Provider accounts, pricing, quotas, model terms,
and regional availability change independently of this repository; verify them
before use.

| Capability | Current integration | Required for real processing | Replaceability |
| --- | --- | --- | --- |
| Source metadata and media | `yt-dlp` / YouTube, with a local Chromium-based browser for proof-of-origin tokens | Yes | Implement `SourceAdapter` |
| Triage, translation, review, Speaker ownership review, metadata | Codex CLI (default) or Claude Code CLI | Yes, one selected provider | Implement the agent protocols |
| Diarization, ASR, alignment, embeddings, TTS, Listening Audit | Modal apps | Yes | Implement analysis and speech adapters |
| Workflow state | Local SQLite | Yes | Core persistence contract |
| Private object storage | Local filesystem or Cloudflare R2 | R2 only for remote feed | Implement publication storage |
| Private feed edge | Cloudflare Worker | Only for remote feed | Replace with an equivalent token gate |
| Owner notifications | Telegram Bot API | No | Implement `NotificationAdapter` |

## Cost controls

The automated test suite uses no billable providers. Real source processing can
incur agent, GPU, storage, and egress costs. Use a dedicated Modal environment,
provider-side budget limits, least-privilege R2 credentials, and a short test
source before a long-form episode.

Local spend estimates are operational guidance only. They are not a billing
guarantee and cannot stop charges outside TransPodcast.

## Data sent to providers

- The source provider sees source discovery and media retrieval requests.
- The selected agent provider (Codex or Claude Code) receives the transcript
  or bounded review context needed for language tasks.
- Modal receives source or reference audio and text needed for the selected
  speech stage.
- Cloudflare stores and serves generated private publication objects.
- Telegram receives only configured notification content and bounded decision
  identifiers.

TransPodcast is intended only for learning, research, and technical
experimentation. Do not process content you are not authorized to use, and do
not process confidential or regulated data unless these provider data flows are
appropriate for it.

## Model licenses

Application source licensing does not relicense model weights. Review
[Third-party models](model-licenses.md) before deploying speech applications.
