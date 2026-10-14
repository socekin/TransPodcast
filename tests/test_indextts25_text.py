from __future__ import annotations

import pytest

from transpodcast.indextts25_text import (
    SpeechTextAdaptationError,
    adapt_indextts25_speech_text,
)


def _phonemize(value: str) -> list[str]:
    pronunciations = {
        "Morgan": ["M", "AO1", "R", "G", "AH0", "N"],
        "Reed": ["R", "IY1", "D"],
        "Jose": ["HH", "OW0", "Z", "EY1"],
        "ordinary": ["AO1", "R", "D", "AH0", "N", "EH2", "R", "IY0"],
        "docs": ["D", "AA1", "K", "S"],
        "example": ["IH0", "G", "Z", "AE1", "M", "P", "AH0", "L"],
        "com": ["K", "AA1", "M"],
        "2": ["T", "UW1"],
        "5": ["F", "AY1", "V"],
    }
    return pronunciations.get(value, [])


def test_adaptation_preserves_people_localizes_tesla_and_annotates_terms() -> None:
    result = adapt_indextts25_speech_text(
        "Morgan Reed 讨论 Tesla、SpaceX、LV 和 API。",
        _phonemize,
    )

    assert result.text == (
        "<Morgan Reed|M AO1 R G AH0 N . R IY1 D> "
        "讨论 特斯拉、<SpaceX|S P EY1 S . EH1 K S>、"
        "<LV|EH1 L . V IY1> 和 <API|EY1 . P IY1 . AY1>。"
    )
    assert result.annotation_count == 4
    assert result.pronunciation_annotation_surfaces == (
        "Morgan Reed",
        "SpaceX",
        "LV",
        "API",
    )
    assert result.chinese_replacement_count == 1
    assert len(result.sha256) == 64


def test_adaptation_annotates_other_retained_english() -> None:
    result = adapt_indextts25_speech_text(
        "这是 ordinary 情况。",
        _phonemize,
    )

    assert result.text == (
        "这是 <ordinary|AO1 R D AH0 N EH2 R IY0> 情况。"
    )


def test_adaptation_keeps_diacritics_in_surface_and_phonemizes_ascii_form() -> None:
    result = adapt_indextts25_speech_text(
        "嘉宾是 José。",
        _phonemize,
    )

    assert result.text == "嘉宾是 <José|HH OW0 Z EY1>。"


def test_adaptation_handles_programming_and_mixed_alphanumeric_terms() -> None:
    result = adapt_indextts25_speech_text(
        "使用 C++、C#、3D 和 GPT-5。",
        _phonemize,
    )

    assert result.text == (
        "使用 <C++|S IY1 . P L AH1 S . P L AH1 S>、"
        "<C#|S IY1 . SH AA1 R P>、<3D|TH R IY1 . D IY1> 和 "
        "<GPT-5|JH IY1 . P IY1 . T IY1 . F AY1 V>。"
    )


def test_adaptation_normalizes_urls_emails_versions_and_long_initialisms() -> None:
    result = adapt_indextts25_speech_text(
        "访问 https://docs.example.com/v2.5，邮件 API@example.com，"
        "使用 HTTPAPI。",
        _phonemize,
    )

    assert "<https://docs.example.com/v2.5|" in result.text
    assert "<API@example.com|" in result.text
    assert "<HTTPAPI|" in result.text
    assert "EY1 CH . T IY1 . T IY1 . P IY1 . EY1 . P IY1 . AY1" in (
        result.text
    )
    assert result.annotation_count == 3


def test_adaptation_preserves_valid_english_and_pinyin_annotations() -> None:
    result = adapt_indextts25_speech_text(
        "<API|EY1 . P IY1 . AY1> 在银<行|HANG2>使用。",
        _phonemize,
    )

    assert result.text == "<API|EY1 . P IY1 . AY1> 在银<行|HANG2>使用。"
    assert result.annotation_count == 2


def test_adaptation_keeps_original_text_when_pronunciation_is_unavailable() -> None:
    result = adapt_indextts25_speech_text(
        "未知词 Qwertyuiop。",
        _phonemize,
    )

    assert result.text == "未知词 Qwertyuiop。"
    assert result.annotation_count == 0
    assert result.pronunciation_fallback_count == 1


def test_adaptation_rejects_malformed_existing_annotation() -> None:
    with pytest.raises(
        SpeechTextAdaptationError,
        match="Invalid existing pronunciation annotation",
    ):
        adapt_indextts25_speech_text("<API|not-cmu>。", _phonemize)
