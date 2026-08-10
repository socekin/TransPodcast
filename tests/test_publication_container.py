from __future__ import annotations

import io
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from transpodcast.publication_container import (
    PublicationChapter,
    embed_publication_metadata,
)

FFMPEG_AVAILABLE = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(arguments),
        check=True,
        capture_output=True,
        text=True,
    )


def _audio_packet_hash(path: Path) -> str:
    return _run(
        "ffmpeg",
        "-v",
        "error",
        "-i",
        str(path),
        "-map",
        "0:a:0",
        "-c",
        "copy",
        "-f",
        "hash",
        "-hash",
        "sha256",
        "-",
    ).stdout.strip()


@pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="ffmpeg is unavailable")
def test_embeds_cover_and_chapters_without_reencoding_audio(tmp_path) -> None:
    source = tmp_path / "source.m4a"
    _run(
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=3",
        "-c:a",
        "aac",
        str(source),
    )
    artwork_buffer = io.BytesIO()
    Image.new("RGB", (64, 64), (10, 80, 160)).save(
        artwork_buffer,
        format="JPEG",
    )

    embedded = embed_publication_metadata(
        source.read_bytes(),
        artwork=artwork_buffer.getvalue(),
        chapters=(
            PublicationChapter(0, "Opening"),
            PublicationChapter(1, "Middle"),
            PublicationChapter(2, "Closing"),
        ),
        duration_seconds=3,
    )
    output = tmp_path / "embedded.m4a"
    output.write_bytes(embedded.audio)
    probe = json.loads(
        _run(
            "ffprobe",
            "-v",
            "error",
            "-show_chapters",
            "-show_streams",
            "-of",
            "json",
            str(output),
        ).stdout
    )

    assert [chapter["tags"]["title"] for chapter in probe["chapters"]] == [
        "Opening",
        "Middle",
        "Closing",
    ]
    assert any(
        stream["codec_type"] == "video" and stream["disposition"]["attached_pic"] == 1
        for stream in probe["streams"]
    )
    assert _audio_packet_hash(source) == _audio_packet_hash(output)
    assert embedded.source_audio_packet_hash == embedded.output_audio_packet_hash
    assert embedded.chapter_count == 3
    assert embedded.artwork_embedded is True
