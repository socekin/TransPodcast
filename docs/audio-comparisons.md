# Audio Comparisons

This page documents the source-versus-rendered audio comparisons published with
TransPodcast. Each example uses a 20–30 second excerpt for learning and product
demonstration.

## Publication requirements

Every committed comparison must include:

- a stable example ID and episode title;
- source URL and excerpt timestamps;
- processing date and TransPodcast release or commit;
- source and rendered files with matched excerpt boundaries;
- a generated-audio disclosure;
- loudness-normalized files suitable for a fair A/B listen;
- a transcript and translation for accessibility;
- known limitations, including pronunciation or speaker-attribution defects.

Files belong in `docs/assets/audio-comparisons/<example-id>/`. Keep each clip
short and use a browser-compatible compressed format. Never commit full source
episodes, voice-reference files, provider payloads, or intermediate embeddings.

## Hosting

Audio files are versioned under
`docs/assets/audio-comparisons/<example-id>/`. The `Audio Samples` GitHub
Actions workflow publishes that directory through GitHub Pages, and the README
links directly to the published MP3 files.

Use these stable URL and file patterns:

```text
Repository: docs/assets/audio-comparisons/<example-id>/original.en.mp3
Pages URL:  https://socekin.github.io/TransPodcast/audio/<example-id>/original.en.mp3

Repository: docs/assets/audio-comparisons/<example-id>/rendered.zh-CN.mp3
Pages URL:  https://socekin.github.io/TransPodcast/audio/<example-id>/rendered.zh-CN.mp3
```

Configure GitHub Pages with **GitHub Actions** as its source. Keep a direct
source-episode link next to each comparison.

## Published comparisons

### All-In Podcast: the SaaS apocalypse

- Source episode: [Google's AI Brain Drain, SpaceX's Huge Quarter, Airtable's
  90% Collapse, US Data Fuels China
  AI](https://www.youtube.com/watch?v=muRIXCDw-k0)
- Source excerpt: `01:01:06.387–01:01:34.627` (`28.240s`)
- Rendered excerpt: `23.382s`
- Source publisher: All-In Podcast
- Processed: August 8, 2026
- Generated with: TransPodcast 0.1.0

| Version | Audio | Delivery format |
| --- | --- | --- |
| Original English | [Play or download `original.en.mp3`](assets/audio-comparisons/all-in-ai-talent-spacex-saas/original.en.mp3) | MP3, 48 kHz stereo, 128 kbps |
| Generated Mandarin | [Play or download `rendered.zh-CN.mp3`](assets/audio-comparisons/all-in-ai-talent-spacex-saas/rendered.zh-CN.mp3) | MP3, 48 kHz stereo, 128 kbps |

**Source transcript:** “Keep in mind that one of the reasons why Leopold got
blown out is because he bet on the SaaS apocalypse. Remember, it was not just
that he was super long these chip stocks that had a correction.” “Oh, is that
right? He was short?” “He was short Adobe and a whole bunch of other SaaS
companies, and those trades also moved the wrong way on him. So again, I just
think that it is painting with too broad a brush, just saying that all SaaS is
going to get obliterated here.” “Yeah.”

**Mandarin translation:** Read the localized
[`translation.zh-CN.txt`](assets/audio-comparisons/all-in-ai-talent-spacex-saas/translation.zh-CN.txt).

### Lenny's Podcast: talent engineers

- Source episode: [The playbook for building high talent density teams | Adam
  Ward, Head of Talent at Cursor](https://www.youtube.com/watch?v=zegYJ6dhIg4)
- Source excerpt: `01:20:41.221–01:21:08.169` (`26.948s`)
- Rendered excerpt: `22.142s`
- Source publisher: Lenny's Podcast
- Processed: August 9, 2026
- Generated with: TransPodcast 0.1.0

| Version | Audio | Delivery format |
| --- | --- | --- |
| Original English | [Play or download `original.en.mp3`](assets/audio-comparisons/lenny-talent-density-adam-ward/original.en.mp3) | MP3, 48 kHz stereo, 128 kbps |
| Generated Mandarin | [Play or download `rendered.zh-CN.mp3`](assets/audio-comparisons/lenny-talent-density-adam-ward/rendered.zh-CN.mp3) | MP3, 48 kHz stereo, 128 kbps |

**Source transcript:** “And so I want a future state of my recruiting team to
feel like they are talent engineers, versus leaning on someone else on the team
to help them build a tool, if that makes sense.” “Yeah, absolutely. I
completely agree. It is interesting. There are go-to-market engineers. Every
function is going to have a version of engineer.” “Yeah.” “Over time,
engineers, engineers. Well, Adam, with that we have reached our very exciting
lightning round. Five questions for you. Ready?” “Yeah, yeah.”

**Mandarin translation:** Read the localized
[`translation.zh-CN.txt`](assets/audio-comparisons/lenny-talent-density-adam-ward/translation.zh-CN.txt).

## Disclosure and limitations

The Mandarin files are AI-generated translations and are not recordings of
the original speakers speaking Mandarin. These passages were selected because
they contain clear, representative multi-speaker exchanges with natural
boundaries. The episode output was not rewritten or re-rendered for this demo;
only excerpting, 50 ms edge fades, MP3 encoding, and EBU R128 loudness
normalization were applied. Generated pronunciation, timing, emotion, and
speaker similarity may differ from the source and may not represent every
long-form edge case.

These samples are provided only for learning and product demonstration. Do not
reuse them to publish or distribute translated episodes.

## Methodology

Use the same semantic span for both clips. Preserve normal pipeline output,
then apply only excerpting, delivery encoding, edge fades, and matched loudness
normalization for the comparison. Report manual edits and the selection
criterion.
