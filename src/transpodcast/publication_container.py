from __future__ import annotations

import json
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PublicationChapter:
    start_seconds: int
    title: str


@dataclass(frozen=True)
class EmbeddedPublication:
    audio: bytes
    source_audio_packet_hash: str
    output_audio_packet_hash: str
    chapter_count: int
    artwork_embedded: bool


def embed_publication_metadata(
    audio: bytes,
    *,
    artwork: bytes | None,
    chapters: Sequence[PublicationChapter],
    duration_seconds: int,
    ffmpeg_executable: str = "ffmpeg",
    ffprobe_executable: str = "ffprobe",
) -> EmbeddedPublication:
    """Remux immutable publication audio with Apple-readable metadata."""
    normalized_chapters = tuple(
        PublicationChapter(
            start_seconds=int(chapter.start_seconds),
            title=chapter.title.strip(),
        )
        for chapter in chapters
    )
    _validate_inputs(
        audio,
        artwork=artwork,
        chapters=normalized_chapters,
        duration_seconds=duration_seconds,
    )
    if artwork is None and not normalized_chapters:
        raise ValueError("Publication metadata cannot be empty")

    with tempfile.TemporaryDirectory(
        prefix="transpodcast-publication-container-"
    ) as temporary_directory:
        root = Path(temporary_directory)
        source_path = root / "source.m4a"
        output_path = root / "publication.m4a"
        source_path.write_bytes(audio)

        command = [
            ffmpeg_executable,
            "-y",
            "-v",
            "error",
            "-i",
            str(source_path),
        ]
        artwork_input: int | None = None
        if artwork is not None:
            artwork_path = root / "cover.jpg"
            artwork_path.write_bytes(artwork)
            artwork_input = 1
            command.extend(["-i", str(artwork_path)])

        chapter_input: int | None = None
        if normalized_chapters:
            chapter_path = root / "chapters.ffmetadata"
            chapter_path.write_text(
                _ffmetadata(
                    normalized_chapters,
                    duration_seconds=duration_seconds,
                ),
                encoding="utf-8",
            )
            chapter_input = 2 if artwork_input is not None else 1
            command.extend(["-f", "ffmetadata", "-i", str(chapter_path)])

        command.extend(["-map", "0:a:0", "-map_metadata", "0"])
        if artwork_input is not None:
            command.extend(
                [
                    "-map",
                    f"{artwork_input}:v:0",
                    "-c:v",
                    "copy",
                    "-disposition:v:0",
                    "attached_pic",
                ]
            )
        if chapter_input is not None:
            command.extend(["-map_chapters", str(chapter_input)])
        command.extend(
            [
                "-c:a",
                "copy",
                "-movflags",
                "+faststart",
                str(output_path),
            ]
        )
        try:
            subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError) as error:
            detail = (
                error.stderr.strip()
                if isinstance(error, subprocess.CalledProcessError) and error.stderr
                else str(error)
            )
            raise RuntimeError(
                f"Publication metadata remux failed: {detail[:500]}"
            ) from error
        if not output_path.is_file() or output_path.stat().st_size == 0:
            raise RuntimeError("Publication metadata remux produced no audio")
        source_packet_hash = _audio_packet_hash(
            source_path,
            ffmpeg_executable=ffmpeg_executable,
        )
        output_packet_hash = _audio_packet_hash(
            output_path,
            ffmpeg_executable=ffmpeg_executable,
        )
        if source_packet_hash != output_packet_hash:
            raise RuntimeError("Publication metadata remux changed AAC packets")
        _verify_container(
            output_path,
            artwork_expected=artwork is not None,
            chapters=normalized_chapters,
            ffprobe_executable=ffprobe_executable,
        )
        return EmbeddedPublication(
            audio=output_path.read_bytes(),
            source_audio_packet_hash=source_packet_hash,
            output_audio_packet_hash=output_packet_hash,
            chapter_count=len(normalized_chapters),
            artwork_embedded=artwork is not None,
        )


def _validate_inputs(
    audio: bytes,
    *,
    artwork: bytes | None,
    chapters: Sequence[PublicationChapter],
    duration_seconds: int,
) -> None:
    if not audio:
        raise ValueError("Publication audio cannot be empty")
    if artwork is not None and not artwork:
        raise ValueError("Publication artwork cannot be empty")
    if duration_seconds <= 0:
        raise ValueError("Publication duration must be positive")
    if not chapters:
        return
    starts = [chapter.start_seconds for chapter in chapters]
    if starts[0] != 0:
        raise ValueError("Publication chapters must begin at zero")
    if starts != sorted(set(starts)):
        raise ValueError("Publication chapter starts must be ascending")
    if any(start < 0 or start >= duration_seconds for start in starts):
        raise ValueError("Publication chapter start exceeds duration")
    if any(not chapter.title for chapter in chapters):
        raise ValueError("Publication chapter title cannot be empty")


def _ffmetadata(
    chapters: Sequence[PublicationChapter],
    *,
    duration_seconds: int,
) -> str:
    lines = [";FFMETADATA1"]
    for index, chapter in enumerate(chapters):
        end = (
            chapters[index + 1].start_seconds
            if index + 1 < len(chapters)
            else duration_seconds
        )
        lines.extend(
            [
                "[CHAPTER]",
                "TIMEBASE=1/1000",
                f"START={chapter.start_seconds * 1000}",
                f"END={end * 1000}",
                f"title={_escape_ffmetadata(chapter.title)}",
            ]
        )
    return "\n".join(lines) + "\n"


def _audio_packet_hash(
    path: Path,
    *,
    ffmpeg_executable: str,
) -> str:
    command = [
        ffmpeg_executable,
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
    ]
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        detail = (
            error.stderr.strip()
            if isinstance(error, subprocess.CalledProcessError) and error.stderr
            else str(error)
        )
        raise RuntimeError(
            f"Publication audio packet verification failed: {detail[:500]}"
        ) from error
    packet_hash = completed.stdout.strip()
    if not packet_hash.startswith("SHA256="):
        raise RuntimeError("Publication audio packet hash is unavailable")
    return packet_hash


def _verify_container(
    path: Path,
    *,
    artwork_expected: bool,
    chapters: Sequence[PublicationChapter],
    ffprobe_executable: str,
) -> None:
    try:
        completed = subprocess.run(
            [
                ffprobe_executable,
                "-v",
                "error",
                "-show_chapters",
                "-show_streams",
                "-of",
                "json",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        probe = json.loads(completed.stdout)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        detail = (
            error.stderr.strip()
            if isinstance(error, subprocess.CalledProcessError) and error.stderr
            else str(error)
        )
        raise RuntimeError(
            f"Publication container verification failed: {detail[:500]}"
        ) from error

    probed_chapters = probe.get("chapters", [])
    expected_chapters = [
        (chapter.start_seconds, chapter.title) for chapter in chapters
    ]
    actual_chapters = [
        (
            round(float(chapter.get("start_time", -1))),
            str(chapter.get("tags", {}).get("title", "")),
        )
        for chapter in probed_chapters
    ]
    if actual_chapters != expected_chapters:
        raise RuntimeError("Publication container chapters failed verification")
    if artwork_expected and not any(
        stream.get("codec_type") == "video"
        and stream.get("disposition", {}).get("attached_pic") == 1
        for stream in probe.get("streams", [])
    ):
        raise RuntimeError("Publication container artwork failed verification")


def _escape_ffmetadata(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("=", "\\=")
        .replace(";", "\\;")
        .replace("#", "\\#")
        .replace("\n", " ")
    )
