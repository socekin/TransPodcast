# TransPodcast

<p align="center">
  <img src="docs/assets/transpodcast-logo.webp" alt="TransPodcast logo" width="140" />
</p>

<p align="center">
  <strong>A podcast translation tool that turns English podcasts into Mandarin while preserving each speaker's voice and delivery.</strong>
</p>

<p align="center">
  <a href="README.md">Simplified Chinese</a>
  ·
  <strong>English</strong>
</p>

<p align="center">
  <a href="https://github.com/socekin/TransPodcast/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/socekin/TransPodcast/actions/workflows/ci.yml/badge.svg" /></a>
  <a href="https://github.com/socekin/TransPodcast/actions/workflows/codeql.yml"><img alt="CodeQL" src="https://github.com/socekin/TransPodcast/actions/workflows/codeql.yml/badge.svg" /></a>
  <a href="LICENSE.md"><img alt="License: PolyForm Noncommercial 1.0.0" src="https://img.shields.io/badge/license-PolyForm%20Noncommercial%201.0.0-blue" /></a>
  <img alt="Python 3.12-3.14" src="https://img.shields.io/badge/python-3.12--3.14-3776AB" />
  <img alt="Node.js 22+" src="https://img.shields.io/badge/node.js-22%2B-339933" />
</p>

TransPodcast is designed for listeners who want to follow complete long-form
English podcasts in Mandarin. It imports YouTube podcast URLs, recognizes
multiple speakers, translates the full conversation, renders Mandarin speech,
and produces a private podcast that can be followed in Apple Podcasts and
other podcast clients.

> TransPodcast is intended only for learning, research, and technical
> experimentation. Do not process or distribute unauthorized content. Public
> distribution of translated episodes is not recommended.

![TransPodcast Host](docs/assets/owner-console.png)

## Features

- **🎧 Complete episode translation:** Transcribe, translate, and render complete
  long-form podcasts instead of producing only summaries or text.
- **👥 Multi-speaker voice preservation:** Identify individual speakers and
  preserve their voice characteristics and delivery in the Mandarin edition.
- **✍️ Translation and review:** Translate with full-episode context and run an
  independent review for terminology, numbers, meaning, and spoken style.
- **✂️ Ad and opening-highlight removal:** Exclude clearly separable third-party
  paid advertisements and assembled opening highlights from the final Mandarin
  audio. Uncertain material is retained to avoid removing editorial content.
- **🔎 Episode discovery and selection:** Import YouTube channels or individual
  URLs and select episodes against personal interests.
- **🗣️ Channel voice library:** Reuse confirmed speaker voices across later
  episodes from the same channel.
- **🖥️ Local web management:** Use TransPodcast Host to manage the library and
  inspect progress, failures, revisions, and publication status.
- **♻️ Recoverable long-running work:** Continue interrupted episodes from the
  latest completed stage instead of processing the entire episode again.
- **✅ Private preview before publication:** Review translation, pronunciation,
  and audio quality before approving an episode.
- **📡 Private podcast subscription:** Publish a protected RSS feed that Apple
  Podcasts and other podcast clients can follow by URL.
- **🔔 Optional Telegram notifications:** Receive notifications when an episode
  needs approval, processing fails, or publication completes.

## Audio Samples

Each sample compares the same 20–30 second excerpt in the original English
audio and the Mandarin edition produced by TransPodcast.

| Source episode | Original English | Rendered Mandarin |
| --- | --- | --- |
| [All-In Podcast: Google's AI Brain Drain, SpaceX's Huge Quarter…](https://www.youtube.com/watch?v=muRIXCDw-k0) | [▶ Play original (28s)](https://socekin.github.io/TransPodcast/audio/all-in-ai-talent-spacex-saas/original.en.mp3) | [▶ Play Mandarin (23s)](https://socekin.github.io/TransPodcast/audio/all-in-ai-talent-spacex-saas/rendered.zh-CN.mp3) |
| [Lenny's Podcast: The playbook for building high talent density teams](https://www.youtube.com/watch?v=zegYJ6dhIg4) | [▶ Play original (27s)](https://socekin.github.io/TransPodcast/audio/lenny-talent-density-adam-ward/original.en.mp3) | [▶ Play Mandarin (22s)](https://socekin.github.io/TransPodcast/audio/lenny-talent-density-adam-ward/rendered.zh-CN.mp3) |

See [Audio Comparisons](docs/audio-comparisons.md) for sources, timestamps,
transcripts, and translations.

## Known Issues

- **Overlapping speakers:** When multiple people speak at the same time,
  speaker attribution, source separation, and Mandarin voice transitions may
  be inaccurate.
- **Laughter:** Laughter and smiling speech may be weakened or lost during
  transcription and speech synthesis, making the generated audio less natural
  than the source.

## Deployment Requirements

| Category | Requirement | Purpose |
| --- | --- | --- |
| Local host | An always-on macOS device | Runs TransPodcast, stores local data, and serves the management interface |
| Local toolchain | Xcode Command Line Tools (providing Git and make), Python 3.12–3.14, [uv](https://docs.astral.sh/uv/), Node.js 22+, npm, and FFmpeg | Installs, builds, and runs the project |
| Codex CLI (required) | An installed and authenticated [Codex CLI](https://developers.openai.com/codex/cli) | Performs episode selection, translation, review, and episode copy generation |
| GPU speech service | A Modal account | Runs speech recognition, speaker analysis, and Mandarin speech synthesis |
| Private podcast delivery | A Cloudflare account, R2 bucket, Worker, and stable HTTPS domain | Hosts the protected RSS feed and full episode audio |
| Podcast client | Apple Podcasts or another client that supports adding a feed by URL | Follows the private Mandarin podcast |
| Intended use | Process only content you may lawfully use | This project is for learning, research, and technical experimentation; do not process or distribute unauthorized content |
| Optional notifications | A Telegram Bot Token and Telegram Chat ID | Delivers processing and publication notifications |

A private subscription requires the Apple Podcasts app and an accessible RSS
URL; it does not require an Apple Podcasts Connect account. See
[Apple Podcasts and Private RSS](docs/publishing/apple-podcasts.md).

## Agent-assisted Deployment

The deployment agent must read this brief before making changes:

[Agent Deployment Brief](docs/agent-deployment.md)

Recommended instruction:

```text
Read docs/agent-deployment.md first, then follow the linked setup and
deployment docs. Do not call live providers, deploy infrastructure, publish
media, rotate secrets, or change tokens until I explicitly approve that step.
First report what prerequisites are missing and what commands you plan to run.
```

The brief defines the deployment order, validation criteria, operations that
require explicit approval, and security boundaries for `.env`, private feed
URLs, account IDs, source audio, transcripts, and generated audio.

## Installation and Startup

```shell
git clone https://github.com/socekin/TransPodcast.git
cd TransPodcast
make setup
make check
cp .env.example .env
make dev
```

TransPodcast Host is available at <http://transpodcast.localhost:8000> by
default.

These commands install dependencies, validate the repository, and start the
local services. The background service checks whether configured Codex, Modal,
Cloudflare, Telegram, and YouTube capabilities are ready, so startup may make
provider API requests. The default manual-processing mode does not import an
episode, start a Processing Run, or execute a GPU speech stage automatically.
Real episode processing requires the remaining service configuration in
[Getting Started](docs/getting-started.md).

## Processing and Publication

1. Complete the local environment and service configuration in
   [Getting Started](docs/getting-started.md).
2. Follow [Deployment](docs/deployment/README.md) to configure the Mac running
   TransPodcast, the Modal speech applications, and the Cloudflare publication
   Worker required for remote listening.
3. Add a YouTube channel or individual episode URL for learning or research in
   TransPodcast Host.
4. Start processing and inspect stage progress, retries, and revisions.
5. Listen to the complete private preview and review translation, speakers,
   pronunciation, and audio quality.
6. Publish manually to the private RSS feed after listening to the full preview
   and confirming episode metadata.
7. Follow the private RSS URL in Apple Podcasts or another podcast client.

## Processing Pipeline

```mermaid
flowchart TB
    Archive["1. Source archive"] --> Analysis["2. Speaker analysis and transcription"]
    Analysis --> Translation["3. Translation"]
    Translation --> Review["4. Independent review"]
    Review --> ContentEdit["5. Ad and opening-highlight removal"]
    ContentEdit --> Voice["6. Voice rendering and assembly"]
    Voice --> Quality["7. Mastering and audio QA"]
    Quality --> Queue["8. Ready to publish"]
```

These stages match the run log in TransPodcast Host. The run log preserves
stage status and attempt history, and processing can resume from the latest
valid checkpoint. An episode enters the publication queue after mastering and
audio QA, then reaches the private RSS feed only after a complete preview and
manual approval.

The Mac running TransPodcast is the TransPodcast Host. It stores workflow state,
coordinates the processing stages, and serves the local web interface. Modal runs
compute-intensive speech stages.
Private R2 storage and a Cloudflare Worker serve the protected RSS feed and
episode audio. Telegram is an optional notification channel.

## Open-source and Open-weight Models

TransPodcast integrates the following speech models. Model weights are not
included in this repository, and their terms are independent of the
TransPodcast software license.

| Model | Role |
| --- | --- |
| [NVIDIA Streaming Sortformer](https://huggingface.co/nvidia/diar_streaming_sortformer_4spk-v2.1) | Identifies speakers and their speaking intervals |
| [Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR) | Transcribes English speech |
| [Qwen3 ForcedAligner](https://huggingface.co/Qwen/Qwen3-ForcedAligner-0.6B) | Aligns transcript text to precise audio times |
| [NVIDIA TitaNet](https://huggingface.co/nvidia/speakerverification_en_titanet_large) | Extracts voice characteristics for speaker matching |
| [IndexTTS2](https://github.com/index-tts/index-tts) | Renders Mandarin speech with target voice and delivery characteristics |

Read [Third-party Model Terms](docs/model-licenses.md) before deployment and
verify the license, attribution, and use restrictions for each deployed model
revision.

## Documentation

- [Agent Deployment Brief](docs/agent-deployment.md)
- [Getting Started](docs/getting-started.md)
- [Configuration](docs/configuration.md)
- [Deployment](docs/deployment/README.md)
- [Apple Podcasts and Private RSS](docs/publishing/apple-podcasts.md)
- [Architecture](docs/architecture.md)
- [Third-party Models](docs/model-licenses.md)
- [Troubleshooting](docs/troubleshooting.md)

## Support

- Email: <socekin@gmail.com>
- X / Twitter: [@jayfx42](https://x.com/jayfx42)

This repository does not currently accept GitHub Issues or external code
contributions.

## Intended Use

TransPodcast is intended only for learning, research, and technical
experimentation. Do not download, translate, synthesize, or publish content you
are not authorized to use. Public distribution of translated episodes is not
recommended. Users must follow the applicable terms of source platforms,
models, and service providers.

## License

TransPodcast is source-available for personal and other noncommercial use under
the [PolyForm Noncommercial License 1.0.0](LICENSE.md).

Commercial use requires a separate license from Jay at
<socekin@gmail.com>. See [COMMERCIAL_LICENSE.md](COMMERCIAL_LICENSE.md).

## Credits

TransPodcast builds on the work of these open-source projects and open-model
communities:

- [Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR), from the Qwen team, provides
  the speech recognition and forced-alignment models.
- [NVIDIA NeMo](https://github.com/NVIDIA-NeMo/NeMo) provides Streaming
  Sortformer, TitaNet, and the supporting speech tooling.
- [IndexTTS2](https://github.com/index-tts/index-tts) provides controllable
  zero-shot speech synthesis.
- [FFmpeg](https://ffmpeg.org/) and [yt-dlp](https://github.com/yt-dlp/yt-dlp)
  provide the media acquisition and audio-processing foundation.

Thank you to their maintainers, researchers, and community contributors.
