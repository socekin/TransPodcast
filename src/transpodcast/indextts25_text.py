from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass

INDEXTTS25_PRONUNCIATION_POLICY_VERSION = "indextts25-pronunciation-v2"

_ANNOTATION_PATTERN = re.compile(r"<([^|>\n]+)\|([^>\n]+)>")
_LATIN_LETTERS = r"A-Za-z\u00c0-\u024f\u1e00-\u1eff"
_LATIN_ALNUM = rf"0-9{_LATIN_LETTERS}"
_EMAIL_PATTERN = re.compile(
    rf"(?<![{_LATIN_ALNUM}])"
    rf"([{_LATIN_ALNUM}._%+-]+@[{_LATIN_ALNUM}.-]+"
    rf"\.[{_LATIN_LETTERS}]{{2,}})"
    rf"(?![{_LATIN_ALNUM}])"
)
_URL_PATTERN = re.compile(
    rf"\b((?:https?://|www\.)[{_LATIN_ALNUM}]"
    rf"[{_LATIN_ALNUM}./?&=_#%+\-]*[{_LATIN_ALNUM}/#])",
    re.IGNORECASE,
)
_VERSION_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(v\d+(?:\.\d+)+(?:-[A-Za-z0-9]+)?)"
    r"(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_LATIN_TOKEN = (
    rf"(?=[{_LATIN_ALNUM}.'’+#-]*[{_LATIN_LETTERS}])"
    rf"[{_LATIN_ALNUM}]+(?:[.'’+#-][{_LATIN_ALNUM}]+)*(?:\+\+|#)?"
)
_LATIN_SPAN_PATTERN = re.compile(
    rf"(?<![{_LATIN_ALNUM}])({_LATIN_TOKEN}(?:\s+{_LATIN_TOKEN})*)"
    rf"(?![{_LATIN_ALNUM}])"
)
_CAMEL_CASE_BOUNDARY = re.compile(
    r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])"
)
_ARPABET_TOKEN = re.compile(
    r"^(?:"
    r"AA[012]?|AE[012]?|AH[012]?|AO[012]?|AW[012]?|AY[012]?|"
    r"EH[012]?|ER[012]?|EY[012]?|IH[012]?|IY[012]?|OW[012]?|"
    r"OY[012]?|UH[012]?|UW[012]?|"
    r"B|CH|D|DH|F|G|HH|JH|K|L|M|N|NG|P|R|S|SH|T|TH|V|W|Y|Z|ZH"
    r")$"
)
_PINYIN_TOKEN = re.compile(r"^[A-ZÜV]+[1-5]$")

# This allowlist intentionally excludes people. A term belongs here only when
# its Chinese company or product name is established and unambiguous in the
# business and technology podcasts TransPodcast processes.
_OFFICIAL_CHINESE_NAMES = {
    "google": "谷歌",
    "microsoft": "微软",
    "morgan stanley": "摩根士丹利",
    "salesforce": "赛富时",
    "tesla": "特斯拉",
}

_PRONUNCIATION_OVERRIDES = {
    "3d": "TH R IY1 . D IY1",
    "api": "EY1 . P IY1 . AY1",
    "c#": "S IY1 . SH AA1 R P",
    "c++": "S IY1 . P L AH1 S . P L AH1 S",
    "deepmind": "D IY1 P . M AY1 N D",
    "lv": "EH1 L . V IY1",
    "nasa": "N AE1 S AH0",
    "nato": "N EY1 T OW0",
    "openai": "OW1 P AH0 N . EY1 AY1",
    "saas": "S AE1 S",
    "spacex": "S P EY1 S . EH1 K S",
}

_SYMBOL_PRONUNCIATIONS = {
    ".": "D AA1 T",
    "/": "S L AE1 SH",
    "?": "K W EH1 S CH AH0 N",
    "&": "AE1 N D",
    "=": "IY1 K W AH0 L Z",
    "_": "AH1 N D ER0 S K AO2 R",
    "-": "D AE1 SH",
    "#": "HH AE1 SH",
    "%": "P ER0 S EH1 N T",
    "+": "P L AH1 S",
    "@": "AE1 T",
}

_LETTER_PRONUNCIATIONS = {
    "A": "EY1",
    "B": "B IY1",
    "C": "S IY1",
    "D": "D IY1",
    "E": "IY1",
    "F": "EH1 F",
    "G": "JH IY1",
    "H": "EY1 CH",
    "I": "AY1",
    "J": "JH EY1",
    "K": "K EY1",
    "L": "EH1 L",
    "M": "EH1 M",
    "N": "EH1 N",
    "O": "OW1",
    "P": "P IY1",
    "Q": "K Y UW1",
    "R": "AA1 R",
    "S": "EH1 S",
    "T": "T IY1",
    "U": "Y UW1",
    "V": "V IY1",
    "W": "D AH1 B AH0 L Y UW0",
    "X": "EH1 K S",
    "Y": "W AY1",
    "Z": "Z IY1",
}


class SpeechTextAdaptationError(ValueError):
    pass


@dataclass(frozen=True)
class AdaptedSpeechText:
    text: str
    annotation_count: int
    chinese_replacement_count: int
    pronunciation_fallback_count: int
    pronunciation_annotation_surfaces: tuple[str, ...]

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


def adapt_indextts25_speech_text(
    text: str,
    phonemize: Callable[[str], Sequence[str]],
) -> AdaptedSpeechText:
    """Build the render-only IndexTTS 2.5 string for a Mandarin target.

    Existing valid annotations are protected. Reviewed Chinese company names
    are replaced first; every remaining Latin span is retained as written and
    wrapped in the official inline CMU pronunciation syntax.
    """

    if not text.strip():
        raise SpeechTextAdaptationError("Speech text is empty")
    protected: list[str] = []

    def protect(match: re.Match[str]) -> str:
        pronunciation = match.group(2).upper().split()
        has_chinese = bool(re.search(r"[\u4e00-\u9fff]", match.group(1)))
        if not pronunciation or not all(
            token == "."
            or (
                _PINYIN_TOKEN.fullmatch(token)
                if has_chinese
                else _ARPABET_TOKEN.fullmatch(token)
            )
            for token in pronunciation
        ):
            raise SpeechTextAdaptationError(
                f"Invalid existing pronunciation annotation: {match.group(0)}"
            )
        protected.append(match.group(0))
        return f"\ue000{len(protected) - 1}\ue001"

    adapted = _ANNOTATION_PATTERN.sub(protect, text)
    existing_annotation_count = len(protected)
    adapted = adapted.replace("——", "，")
    generated_annotation_count = 0
    pronunciation_fallback_count = 0

    def protect_generated(
        match: re.Match[str],
        pronunciation: str,
    ) -> str:
        nonlocal generated_annotation_count
        protected.append(f"<{match.group(1)}|{pronunciation}>")
        generated_annotation_count += 1
        return f"\ue000{len(protected) - 1}\ue001"

    def protect_automatic(
        match: re.Match[str],
        pronunciation: Callable[[], str],
    ) -> str:
        nonlocal pronunciation_fallback_count
        try:
            return protect_generated(match, pronunciation())
        except SpeechTextAdaptationError:
            protected.append(match.group(1))
            pronunciation_fallback_count += 1
            return f"\ue000{len(protected) - 1}\ue001"

    adapted = _EMAIL_PATTERN.sub(
        lambda match: protect_automatic(
            match,
            lambda: _pronunciation_for_structured_text(
                match.group(1), phonemize
            ),
        ),
        adapted,
    )
    adapted = _URL_PATTERN.sub(
        lambda match: protect_automatic(
            match,
            lambda: _pronunciation_for_url(match.group(1), phonemize),
        ),
        adapted,
    )
    adapted = _VERSION_PATTERN.sub(
        lambda match: protect_automatic(
            match,
            lambda: _pronunciation_for_structured_text(
                match.group(1), phonemize
            ),
        ),
        adapted,
    )
    chinese_replacement_count = 0
    for term, replacement in sorted(
        _OFFICIAL_CHINESE_NAMES.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        pattern = re.compile(
            rf"(?<![{_LATIN_ALNUM}]){re.escape(term)}"
            rf"(?![{_LATIN_ALNUM}])",
            re.IGNORECASE,
        )
        adapted, count = pattern.subn(replacement, adapted)
        chinese_replacement_count += count

    def annotate(match: re.Match[str]) -> str:
        return protect_automatic(
            match,
            lambda: _pronunciation_for_span(match.group(1), phonemize),
        )

    adapted = _LATIN_SPAN_PATTERN.sub(annotate, adapted)
    remaining = _LATIN_SPAN_PATTERN.search(
        _ANNOTATION_PATTERN.sub("", adapted)
    )
    if remaining is not None:
        raise SpeechTextAdaptationError(
            f"Unannotated Latin speech span: {remaining.group(1)}"
        )
    for index, annotation in enumerate(protected):
        adapted = adapted.replace(f"\ue000{index}\ue001", annotation)
    return AdaptedSpeechText(
        text=adapted,
        annotation_count=(
            existing_annotation_count + generated_annotation_count
        ),
        chinese_replacement_count=chinese_replacement_count,
        pronunciation_fallback_count=pronunciation_fallback_count,
        pronunciation_annotation_surfaces=tuple(
            match.group(1) for match in _ANNOTATION_PATTERN.finditer(adapted)
        ),
    )


def _pronunciation_for_span(
    surface: str,
    phonemize: Callable[[str], Sequence[str]],
) -> str:
    override = _PRONUNCIATION_OVERRIDES.get(surface.casefold())
    if override is not None:
        return override
    words = surface.split()
    pronunciations = [
        _pronunciation_for_word(word, phonemize) for word in words
    ]
    return " . ".join(pronunciations)


def _pronunciation_for_word(
    word: str,
    phonemize: Callable[[str], Sequence[str]],
) -> str:
    override = _PRONUNCIATION_OVERRIDES.get(word.casefold())
    if override is not None:
        return override
    pronunciation_word = _ascii_latin_text(word)
    override = _PRONUNCIATION_OVERRIDES.get(pronunciation_word.casefold())
    if override is not None:
        return override
    if len(pronunciation_word) == 1 and pronunciation_word.isalpha():
        return _LETTER_PRONUNCIATIONS[pronunciation_word.upper()]
    letters_only = "".join(
        character
        for character in pronunciation_word
        if character.isalpha()
    )
    if (
        letters_only == pronunciation_word
        and pronunciation_word.isupper()
        and len(pronunciation_word) > 1
    ):
        return " . ".join(
            _LETTER_PRONUNCIATIONS[letter]
            for letter in pronunciation_word
        )
    mixed_components = re.findall(
        r"[A-Za-z]+|[0-9]+",
        pronunciation_word,
    )
    if (
        len(mixed_components) > 1
        and "".join(mixed_components).casefold()
        == re.sub(
            r"[^A-Za-z0-9]",
            "",
            pronunciation_word,
        ).casefold()
    ):
        return " . ".join(
            _pronunciation_for_word(component, phonemize)
            for component in mixed_components
        )
    components = [
        component
        for component in _CAMEL_CASE_BOUNDARY.split(pronunciation_word)
        if component
    ]
    if len(components) > 1:
        return " . ".join(
            _phonemize_component(component, phonemize)
            for component in components
        )
    return _phonemize_component(pronunciation_word, phonemize)


def _ascii_latin_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.replace("’", "'"))
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    if not ascii_value:
        raise SpeechTextAdaptationError(
            f"No ASCII pronunciation form is available for {value}"
        )
    return ascii_value


def _pronunciation_for_url(
    surface: str,
    phonemize: Callable[[str], Sequence[str]],
) -> str:
    value = re.sub(r"^https?://", "", surface, flags=re.IGNORECASE)
    if value.casefold().startswith("www."):
        value = f"WWW{value[3:]}"
    return _pronunciation_for_structured_text(value, phonemize)


def _pronunciation_for_structured_text(
    surface: str,
    phonemize: Callable[[str], Sequence[str]],
) -> str:
    pronunciation_surface = _ascii_latin_text(surface)
    components = re.findall(
        r"[A-Za-z]+|[0-9]+|[./?&=_#%+@-]",
        pronunciation_surface,
    )
    pronunciations: list[str] = []
    for component in components:
        symbol = _SYMBOL_PRONUNCIATIONS.get(component)
        pronunciations.append(
            symbol
            if symbol is not None
            else _pronunciation_for_word(component, phonemize)
        )
    if not pronunciations:
        raise SpeechTextAdaptationError(
            f"No CMU pronunciation is available for {surface}"
        )
    return " . ".join(pronunciations)


def _phonemize_component(
    component: str,
    phonemize: Callable[[str], Sequence[str]],
) -> str:
    tokens = [
        str(token).upper()
        for token in phonemize(component)
        if _ARPABET_TOKEN.fullmatch(str(token).upper())
    ]
    if not tokens:
        raise SpeechTextAdaptationError(
            f"No CMU pronunciation is available for {component}"
        )
    return " ".join(tokens)
