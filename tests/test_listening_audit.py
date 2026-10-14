from __future__ import annotations

import pytest

from transpodcast.listening_audit import (
    UnitObservation,
    audit_unit,
    audit_units,
    character_error_rate,
    normalize_spoken_text,
)


def observation(**overrides: object) -> UnitObservation:
    values: dict[str, object] = {
        "unit_id": "seg-0001",
        "speaker_label": "speaker_0",
        "target_text": "我觉得这个想法很有意思，值得试一试。",
        "transcript": "我觉得这个想法很有意思值得试一试",
        "speech_seconds": 3.4,
    }
    values.update(overrides)
    return UnitObservation(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("为12亿人打造产品", "为十二亿人打造产品"),
        ("120亿", "一百二十亿"),
        ("1005", "一千零五"),
        ("100005", "十万零五"),
        ("5.90 美元", "五点九零美元"),
        ("30%", "百分之三十"),
        ("用 ChatGPT 写代码！", "用chatgpt写代码"),
    ],
)
def test_normalization_reads_numbers_and_drops_punctuation(
    text: str, expected: str
) -> None:
    assert normalize_spoken_text(text) == expected


def test_spoken_and_written_numbers_compare_equal() -> None:
    assert character_error_rate("为12亿人打造产品", "为十二亿人打造产品") == 0.0


def test_clean_unit_has_no_findings() -> None:
    audit = audit_unit(observation())
    assert audit.cer == 0.0
    assert audit.findings == ()


def test_truncated_tail_is_a_hard_finding() -> None:
    audit = audit_unit(observation(transcript="我觉得这个想法很有"))
    kinds = {finding.kind for finding in audit.findings}
    assert "truncation" in kinds
    assert audit.hard


def test_repetition_is_a_hard_insertion_finding() -> None:
    target = "很重要。"
    audit = audit_unit(
        observation(
            target_text=target,
            transcript="很重要很重要很重要很重要",
            speech_seconds=2.0,
        )
    )
    assert any(f.kind == "insertion" and f.severity == "hard" for f in audit.findings)


def test_untranslated_english_run_is_soft_english_leakage() -> None:
    audit = audit_unit(
        observation(transcript="我觉得这个 you know it is 很有意思值得试一试")
    )
    leak = [f for f in audit.findings if f.kind == "english_leakage"]
    assert leak and leak[0].severity == "soft"
    assert "you know it is" in leak[0].detail


def test_single_english_name_variant_is_not_leakage() -> None:
    # The target says "Dots"; the back-transcription heard "Docs".
    audit = audit_unit(
        observation(
            target_text="这就是我们通过Dots推出的东西。",
            transcript="这就是我们通过 Docs 推出的东西",
            speech_seconds=2.4,
        )
    )
    assert not [f for f in audit.findings if f.kind == "english_leakage"]


def test_latin_present_in_target_is_not_leakage() -> None:
    audit = audit_unit(
        observation(
            target_text="你可以直接用 ChatGPT。",
            transcript="你可以直接用ChatGPT",
            speech_seconds=1.6,
        )
    )
    assert not [f for f in audit.findings if f.kind == "english_leakage"]


def test_run_29_style_duration_anomaly_is_hard() -> None:
    # A recorded defect class: 28 characters rendered as 25.8 s.
    target = "这是一个二十八个汉字左右的句子用来模拟那次异常的配音时长情况好"
    audit = audit_unit(
        observation(target_text=target, transcript=target, speech_seconds=25.8)
    )
    assert any(
        f.kind == "duration_anomaly" and f.severity == "hard" for f in audit.findings
    )


def test_fast_speech_is_a_soft_rate_finding() -> None:
    audit = audit_unit(observation(speech_seconds=1.2))
    assert [f.kind for f in audit.findings] == ["speech_rate"]


def test_long_internal_silence_is_soft() -> None:
    audit = audit_unit(observation(longest_internal_silence_seconds=1.8))
    assert [f.kind for f in audit.findings] == ["internal_silence"]


def test_voice_drift_flags_unit_closer_to_other_speaker() -> None:
    guest = (1.0, 0.0, 0.0)
    host = (0.0, 1.0, 0.0)
    observations = [
        observation(unit_id="u1", speaker_label="speaker_0", voiceprint=guest),
        observation(unit_id="u2", speaker_label="speaker_0", voiceprint=guest),
        observation(unit_id="u3", speaker_label="speaker_1", voiceprint=host),
        observation(unit_id="u4", speaker_label="speaker_1", voiceprint=host),
        observation(
            unit_id="u5", speaker_label="speaker_0", voiceprint=(0.1, 0.95, 0.0)
        ),
    ]
    result = audit_units(observations)
    drift = [f for f in result.findings if f.kind == "voice_drift"]
    assert [f.unit_id for f in drift] == ["u5"]
    payload = result.to_payload()
    assert payload["flagged_unit_count"] == 1
    assert payload["hard_unit_ids"] == []


def test_unit_without_a_transcript_is_scored_on_energy_alone() -> None:
    # The same defect class: far too long for its text, ASR unavailable.
    audit = audit_unit(observation(transcript=None, speech_seconds=25.5))
    assert audit.cer is None
    assert [(f.kind, f.severity) for f in audit.findings] == [
        ("duration_anomaly", "hard")
    ]
    payload = audit_units([observation(transcript=None)]).to_payload()
    assert payload["mean_cer"] is None
    assert payload["transcribed_unit_count"] == 0
