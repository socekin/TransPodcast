from __future__ import annotations

import json
import re

from transpodcast.domain import (
    CanonicalSegment,
    SourceAnalysisResult,
    TranslationResult,
)
from transpodcast.evaluation import EpisodeInfo, build_report
from transpodcast.evaluation_html import render_report_html


def _report(seek: bool = True):
    analysis = SourceAnalysisResult(
        segments=(
            CanonicalSegment(
                segment_id="seg-0040",
                speaker_label="speaker_0",
                start_seconds=0.0,
                end_seconds=7.3,
                text="the thing about this is",
            ),
            CanonicalSegment(
                segment_id="seg-0041",
                speaker_label="speaker_1",
                start_seconds=7.3,
                end_seconds=7.9,
                text="No way",
                overlap=True,
                words=(("No", 7.31, 7.53), ("way", 7.53, 7.88)),
            ),
            CanonicalSegment(
                segment_id="seg-0042",
                speaker_label="speaker_0",
                start_seconds=7.9,
                end_seconds=14.0,
                text="that it keeps happening.",
            ),
        ),
        pipeline_version="transpodcast-modal-pipeline-v6",
        diarization_model="nvidia/diar_streaming_sortformer_4spk-v2",
        diarization_model_revision="fixture-revision",
        transcription_model="Qwen/Qwen3-ASR-1.7B",
        alignment_model="Qwen/Qwen3-ForcedAligner-0.6B",
    )
    translation = TranslationResult(
        title="中文标题",
        overview="中文简介",
        target_locale="zh-CN",
        segments=("这件事的关键在于", "不可能", "它总是不断发生。"),
        provider="codex",
        model="codex-default",
        input_tokens=10,
        output_tokens=10,
    )
    return build_report(
        analysis=analysis,
        translation=translation,
        segment_timings=((0.0, 7.0), (7.0, 7.6), (7.6, 13.4)) if seek else (),
        episode=EpisodeInfo(
            guid="ep-guid-1",
            title="中文标题",
            source_show="Useful Show",
            source_title="A source episode",
            run_id=12,
            generated_at="2026-07-30T12:00:00",
            publication_duration_seconds=228,
        ),
        speaker_names={"speaker_0": "Jordan", "speaker_1": "Casey"},
    )


def _embedded_payload(page: str) -> dict[str, object]:
    match = re.search(
        r'<script type="application/json" id="report-data">(.*?)</script>',
        page,
        re.DOTALL,
    )
    assert match is not None, "report payload must be embedded for verdict export"
    return json.loads(match.group(1))


def test_page_embeds_the_report_model_and_wires_the_audio_source() -> None:
    page = render_report_html(_report(), audio_filename="publication.m4a")

    payload = _embedded_payload(page)
    assert [item["segment_id"] for item in payload["segments"]] == [
        "seg-0040",
        "seg-0041",
        "seg-0042",
    ]
    assert payload["seek_available"] is True
    assert [finding["key"] for finding in payload["findings"]] == [
        "interleave_suspect:seg-0040+seg-0041+seg-0042",
        "midsplit_suspect:seg-0040+seg-0041",
        "midsplit_suspect:seg-0041+seg-0042",
        "crosstalk:seg-0041",
    ]
    assert 'src="publication.m4a"' in page
    # Owner-facing surface renders in the Owner's language.
    assert "节目评测报告" in page
    assert "穿插嫌疑" in page
    # Episode content survives into the page.
    assert "不可能" in page


def test_page_states_that_seeking_is_unavailable_without_durable_timings() -> None:
    page = render_report_html(_report(seek=False), audio_filename=None)

    assert _embedded_payload(page)["seek_available"] is False
    assert "<audio" not in page
    assert "无法按成片时间跳转" in page


def test_episode_text_cannot_break_out_of_the_embedded_payload() -> None:
    # Transcript text is untrusted input to the page. A literal
    # </script> in an episode must not end the data block.
    analysis = SourceAnalysisResult(
        segments=(
            CanonicalSegment(
                segment_id="seg-0001",
                speaker_label="speaker_0",
                start_seconds=0.0,
                end_seconds=6.0,
                text="he said </script><script>alert(1)</script> on air",
            ),
        ),
        pipeline_version="v6",
        diarization_model="d",
        transcription_model="t",
        alignment_model="a",
    )
    report = build_report(
        analysis=analysis,
        translation=TranslationResult(
            title="t",
            overview="o",
            target_locale="zh-CN",
            segments=("译文",),
            provider="codex",
            model="codex-default",
            input_tokens=1,
            output_tokens=1,
        ),
        segment_timings=((0.0, 5.5),),
        episode=EpisodeInfo(
            guid="g",
            title="t",
            source_show="s",
            source_title="st",
            run_id=1,
            generated_at="2026-07-30T12:00:00",
            publication_duration_seconds=6,
        ),
        speaker_names={},
    )

    page = render_report_html(report, audio_filename=None)

    payload = _embedded_payload(page)
    assert "</script>" in str(payload["segments"][0]["source_text"])
    assert "<script>alert(1)</script>" not in page


def test_audition_survives_missing_timings_with_seeking_disabled() -> None:
    page = render_report_html(_report(seek=False), audio_filename="publication.m4a")

    # The Owner can still listen; only timestamp seeking is unavailable.
    assert 'src="publication.m4a"' in page
    assert "无法按成片时间跳转" in page
    assert _embedded_payload(page)["seek_available"] is False


def test_page_states_the_pipeline_version_and_models() -> None:
    page = render_report_html(_report(), audio_filename=None)

    assert "transpodcast-modal-pipeline-v6" in page
    assert "Qwen/Qwen3-ASR-1.7B" in page
    assert "nvidia/diar_streaming_sortformer_4spk-v2" in page
    assert "@fixture-revision" in page


def test_page_labels_an_omission_from_a_legacy_run() -> None:
    analysis = SourceAnalysisResult(
        segments=(
            CanonicalSegment(
                segment_id="seg-0001",
                speaker_label="speaker_0",
                start_seconds=0.0,
                end_seconds=5.0,
                text="Legacy advertisement.",
                omitted_as_sponsor=True,
            ),
        ),
        pipeline_version="legacy-v1",
        diarization_model="d",
        transcription_model="t",
        alignment_model="a",
    )
    report = build_report(
        analysis=analysis,
        translation=TranslationResult(
            title="t",
            overview="o",
            target_locale="zh-CN",
            segments=(),
            provider="codex",
            model="codex-default",
            input_tokens=1,
            output_tokens=1,
        ),
        segment_timings=(),
        episode=EpisodeInfo(
            guid="g",
            title="t",
            source_show="s",
            source_title="st",
            run_id=1,
            generated_at="2026-07-30T12:00:00",
            publication_duration_seconds=0,
        ),
        speaker_names={},
    )

    page = render_report_html(report, audio_filename=None)

    assert "历史版本未生成" in page
    assert _embedded_payload(page)["summary"]["omitted_sponsor_count"] == 1
