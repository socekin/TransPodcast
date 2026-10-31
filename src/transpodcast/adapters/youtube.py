from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
from PIL import Image, UnidentifiedImageError

from transpodcast.adapters.ytdlp_runtime import YtDlpRuntime
from transpodcast.domain import (
    ChannelResolution,
    RecentVideoWindow,
    SourceArchive,
    SourceCandidate,
    SourceThumbnail,
    SourceVideoPreview,
    is_youtube_video_key,
)

_YOUTUBE_HOSTS = {"youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
_AUDIO_MEDIA_TYPES = {
    "m4a": "audio/mp4",
    "mp4": "audio/mp4",
    "aac": "audio/aac",
    "mp3": "audio/mpeg",
    "webm": "audio/webm",
    "opus": "audio/ogg",
    "ogg": "audio/ogg",
    "flac": "audio/flac",
    "wav": "audio/wav",
}


class YtDlpSourceAdapter:
    """Every source goes through yt-dlp: any link it can extract one
    episode from can be imported. Following a channel stays YouTube-only."""

    def __init__(
        self,
        executable: str = "yt-dlp",
        proxy_url: str = "",
        runtime: YtDlpRuntime | None = None,
    ) -> None:
        self.executable = executable
        self.proxy_url = proxy_url.strip()
        self.runtime = runtime

    def readiness(self) -> bool:
        if self.runtime is not None:
            return self.runtime.available()
        return shutil.which(self.executable) is not None

    def refresh_downloader(self) -> None:
        """Move to a newer yt-dlp release when one is out (at most daily)."""
        if self.runtime is not None:
            self.runtime.refresh()

    def resolve_channel(self, url: str) -> ChannelResolution:
        if not self._is_youtube_url(url):
            raise ValueError("Only YouTube channels can be followed")
        metadata = self._json(
            self._command(
                "--dump-single-json",
                "--flat-playlist",
                "--playlist-end",
                "1",
                "--skip-download",
                "--no-cookies",
                url,
            )
        )
        channel_id = str(
            metadata.get("channel_id")
            or metadata.get("uploader_id")
            or metadata.get("id")
            or ""
        )
        if not channel_id:
            raise ValueError("YouTube Source Channel could not be resolved")
        follower_count = metadata.get("channel_follower_count")
        playlist_count = metadata.get("playlist_count")
        return ChannelResolution(
            youtube_channel_id=channel_id,
            title=str(
                metadata.get("channel")
                or metadata.get("uploader")
                or metadata.get("title")
                or channel_id
            ),
            canonical_url=str(
                metadata.get("channel_url")
                or f"https://www.youtube.com/channel/{channel_id}"
            ),
            avatar_url=self._channel_avatar(metadata),
            subscriber_count=(
                int(follower_count) if follower_count is not None else None
            ),
            video_count=(
                int(playlist_count) if playlist_count is not None else None
            ),
        )

    @staticmethod
    def _channel_avatar(metadata: dict[str, Any]) -> str:
        thumbnails = metadata.get("thumbnails") or []
        avatars = [
            item
            for item in thumbnails
            if isinstance(item, dict)
            and "avatar" in str(item.get("id") or "")
        ] or [item for item in thumbnails if isinstance(item, dict)]
        if not avatars:
            return ""
        return str(avatars[-1].get("url") or "")

    def resolve_source(
        self,
        url: str,
    ) -> tuple[ChannelResolution, SourceCandidate | None]:
        """Resolve a URL with the fewest possible YouTube requests.

        A video URL needs one metadata request; the Source Channel is
        derived from that same document. A channel URL resolves the
        channel only. Captions are never downloaded here.
        """
        if not self._is_youtube_url(url):
            return self._resolve_any_source(url)
        if not self._is_video_url(url):
            return self.resolve_channel(url), None
        metadata = self._json(
            self._command(
                "--dump-single-json",
                "--skip-download",
                "--no-cookies",
                "--no-playlist",
                url,
            )
        )
        channel_id = str(metadata.get("channel_id") or "")
        if not channel_id:
            raise ValueError("YouTube Source Channel could not be resolved")
        follower_count = metadata.get("channel_follower_count")
        channel = ChannelResolution(
            youtube_channel_id=channel_id,
            title=str(
                metadata.get("channel")
                or metadata.get("uploader")
                or channel_id
            ),
            canonical_url=str(
                metadata.get("channel_url")
                or f"https://www.youtube.com/channel/{channel_id}"
            ),
            subscriber_count=(
                int(follower_count) if follower_count is not None else None
            ),
        )
        return channel, self._candidate(metadata, include_captions=False)

    def _resolve_any_source(
        self,
        url: str,
    ) -> tuple[ChannelResolution, SourceCandidate | None]:
        """A non-YouTube link: one episode, or a list that is not imported."""
        metadata = self._metadata(url)
        channel = self._owner(metadata)
        if metadata.get("_type") in {"playlist", "multi_video"} or (
            metadata.get("entries") is not None
        ):
            return channel, None
        return channel, self._candidate(metadata, include_captions=False)

    def _owner(self, metadata: dict[str, Any]) -> ChannelResolution:
        """The show or uploader a non-YouTube episode belongs to."""
        platform = _platform(metadata)
        page = str(
            metadata.get("webpage_url") or metadata.get("original_url") or ""
        )
        host = (urlparse(page).hostname or platform).removeprefix("www.")
        owner = (
            metadata.get("channel_id")
            or metadata.get("uploader_id")
            or metadata.get("series_id")
            or metadata.get("channel")
            or metadata.get("uploader")
            or metadata.get("series")
            or host
        )
        return ChannelResolution(
            youtube_channel_id=_safe_key(platform, str(owner), limit=128),
            title=str(
                metadata.get("channel")
                or metadata.get("uploader")
                or metadata.get("series")
                or metadata.get("playlist_title")
                or host
            ),
            canonical_url=str(
                metadata.get("channel_url")
                or metadata.get("uploader_url")
                or (f"https://{host}" if host else page)
            ),
        )

    def recent_video_previews(
        self,
        youtube_channel_id: str,
    ) -> RecentVideoWindow:
        """Return the flat ten-video Channel Backfill window.

        One flat-playlist request; no per-video metadata retrieval. The
        channel avatar is read from the same playlist document.
        """
        channel_url = (
            f"https://www.youtube.com/channel/{youtube_channel_id}/videos"
        )
        playlist = self._json(
            self._command(
                "--dump-single-json",
                "--flat-playlist",
                "--playlist-end",
                "10",
                "--skip-download",
                "--no-cookies",
                channel_url,
            )
        )
        previews: list[SourceVideoPreview] = []
        for item in playlist.get("entries") or []:
            video_id = str(item.get("id") or "")
            if not video_id:
                continue
            view_count = item.get("view_count")
            previews.append(
                SourceVideoPreview(
                    youtube_video_id=video_id,
                    title=str(item.get("title") or video_id),
                    duration_seconds=int(item.get("duration") or 0),
                    source_url=str(
                        item.get("url")
                        or f"https://www.youtube.com/watch?v={video_id}"
                    ),
                    view_count=(
                        int(view_count) if view_count is not None else None
                    ),
                )
            )
        return RecentVideoWindow(
            previews=tuple(previews),
            channel_avatar_url=self._channel_avatar(playlist),
        )

    def enrich_video_previews(
        self,
        previews: list[SourceVideoPreview],
    ) -> list[SourceVideoPreview]:
        """Fill publication dates through parallel metadata requests.

        A failed lookup keeps the flat preview instead of failing the
        whole Channel Backfill response.
        """
        if not previews:
            return []

        def enrich(preview: SourceVideoPreview) -> SourceVideoPreview:
            try:
                metadata = self._json(
                    self._command(
                        "--dump-single-json",
                        "--skip-download",
                        "--no-cookies",
                        "--no-playlist",
                        (
                            "https://www.youtube.com/watch?v="
                            f"{preview.youtube_video_id}"
                        ),
                    )
                )
            except (RuntimeError, ValueError, TypeError):
                return preview
            timestamp = metadata.get("timestamp")
            view_count = metadata.get("view_count")
            return SourceVideoPreview(
                youtube_video_id=preview.youtube_video_id,
                title=str(metadata.get("title") or preview.title),
                duration_seconds=int(
                    metadata.get("duration") or preview.duration_seconds
                ),
                source_url=preview.source_url,
                published_at=(
                    datetime.fromtimestamp(float(timestamp), tz=UTC)
                    if timestamp
                    else None
                ),
                view_count=(
                    int(view_count)
                    if view_count is not None
                    else preview.view_count
                ),
            )

        with ThreadPoolExecutor(max_workers=min(6, len(previews))) as pool:
            return list(pool.map(enrich, previews))

    def discover(self, youtube_channel_id: str) -> list[SourceCandidate]:
        return self._channel_videos(youtube_channel_id, limit=20)

    def recent_videos(
        self,
        youtube_channel_id: str,
    ) -> list[SourceCandidate]:
        return self._channel_videos(youtube_channel_id, limit=10)

    def _channel_videos(
        self,
        youtube_channel_id: str,
        *,
        limit: int,
    ) -> list[SourceCandidate]:
        channel_url = (
            f"https://www.youtube.com/channel/{youtube_channel_id}/videos"
        )
        playlist = self._json(
            self._command(
                "--dump-single-json",
                "--flat-playlist",
                "--playlist-end",
                str(limit),
                "--skip-download",
                "--no-cookies",
                channel_url,
            )
        )
        candidates: list[SourceCandidate] = []
        for item in playlist.get("entries") or []:
            video_id = str(item.get("id") or "")
            if not video_id:
                continue
            metadata = self._json(
                self._command(
                    "--dump-single-json",
                    "--skip-download",
                    "--no-cookies",
                    f"https://www.youtube.com/watch?v={video_id}",
                )
            )
            candidates.append(self._candidate(metadata))
        return candidates

    def _candidate(
        self,
        metadata: dict[str, Any],
        *,
        include_captions: bool = True,
    ) -> SourceCandidate:
        platform = _platform(metadata)
        source_url = str(
            metadata.get("webpage_url")
            or metadata.get("original_url")
            or f"https://www.youtube.com/watch?v={metadata['id']}"
        )
        if platform == "youtube":
            video_key = str(metadata["id"])
            channel_key = str(metadata.get("channel_id") or "") or None
        else:
            video_key = _safe_key(
                platform,
                str(metadata.get("id") or ""),
                limit=64,
                fallback=source_url,
            )
            channel_key = self._owner(metadata).youtube_channel_id
        duration = metadata.get("duration") or self._probe_duration(metadata)
        return SourceCandidate(
            youtube_video_id=video_key,
            title=str(metadata.get("title") or metadata.get("id") or source_url),
            description=str(metadata.get("description") or ""),
            duration_seconds=int(duration or 0),
            language=str(metadata.get("language") or "en"),
            published_at=_published_at(metadata),
            source_url=source_url,
            captions=self._captions(metadata) if include_captions else None,
            youtube_channel_id=channel_key,
            thumbnail_url=str(metadata.get("thumbnail") or "") or None,
        )

    def _probe_duration(self, metadata: dict[str, Any]) -> float | None:
        """A direct audio link often has no duration; read it from the file."""
        media_url = metadata.get("url")
        if not media_url or shutil.which("ffprobe") is None:
            return None
        try:
            completed = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(media_url),
                ],
                text=True,
                capture_output=True,
                check=False,
                timeout=60,
            )
            return float(completed.stdout.strip())
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return None

    def _metadata(self, url: str) -> dict[str, Any]:
        return self._json(
            self._command(
                "--dump-single-json",
                "--skip-download",
                "--no-cookies",
                "--no-playlist",
                "--flat-playlist",
                url,
            )
        )

    def video(self, url: str) -> SourceCandidate:
        return self._candidate(self._metadata(url))

    def supported_source_video(
        self,
        url: str,
    ) -> SourceCandidate | None:
        if self._is_youtube_url(url):
            return self.video(url) if self._is_video_url(url) else None
        _channel, candidate = self._resolve_any_source(url)
        return candidate

    @staticmethod
    def _is_youtube_url(url: str) -> bool:
        host = (urlparse(url).hostname or "").lower().removeprefix("www.")
        return host in _YOUTUBE_HOSTS

    @staticmethod
    def _is_video_url(url: str) -> bool:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().removeprefix("www.")
        path_parts = [part for part in parsed.path.split("/") if part]
        if host == "youtu.be":
            return bool(path_parts)
        if host not in {"youtube.com", "m.youtube.com"}:
            return False
        if parsed.path == "/watch":
            return bool(parse_qs(parsed.query).get("v"))
        return bool(
            len(path_parts) >= 2
            and path_parts[0] in {"embed", "live", "shorts"}
        )

    def archive(self, candidate: SourceCandidate) -> SourceArchive:
        """Download the episode audio; a failure retries once on a newer
        yt-dlp if one has been released since."""
        if self.runtime is not None:
            self.runtime.refresh()
        try:
            return self._download_audio(candidate)
        except RuntimeError:
            if self.runtime is None or not self.runtime.refresh(force=True):
                raise
            return self._download_audio(candidate)

    def _download_audio(self, candidate: SourceCandidate) -> SourceArchive:
        with TemporaryDirectory(prefix="transpodcast-source-") as temp:
            root = Path(temp)
            completed = subprocess.run(
                self._command(
                    "--no-cookies",
                    "--no-playlist",
                    "--format",
                    "bestaudio[ext=m4a]/bestaudio/best",
                    # Keep an audio-only download as it is; take the audio
                    # track out of a video-only offering.
                    "--extract-audio",
                    "--paths",
                    str(root),
                    "--output",
                    "source.%(ext)s",
                    candidate.source_url,
                ),
                text=True,
                capture_output=True,
                check=False,
                timeout=1800,
            )
            if completed.returncode != 0:
                raise RuntimeError(
                    "Source archive failed: " + _error_summary(completed.stderr)
                )
            files = [
                path
                for path in root.glob("source.*")
                if path.suffix.removeprefix(".").lower() in _AUDIO_MEDIA_TYPES
            ]
            if len(files) != 1:
                raise RuntimeError(
                    "Source archive did not produce one audio file"
                )
            path = files[0]
            content = path.read_bytes()
            extension = path.suffix.removeprefix(".").lower()
            return SourceArchive(
                content=content,
                extension=extension,
                media_type=_AUDIO_MEDIA_TYPES[extension],
                checksum=hashlib.sha256(content).hexdigest(),
            )

    def thumbnail(self, candidate: SourceCandidate) -> SourceThumbnail:
        if not is_youtube_video_key(candidate.youtube_video_id):
            return self._page_thumbnail(candidate)
        base = f"https://i.ytimg.com/vi/{candidate.youtube_video_id}"
        candidates = (
            ("maxresdefault", f"{base}/maxresdefault.jpg"),
            ("hq720", f"{base}/hq720.jpg"),
            ("sddefault", f"{base}/sddefault.jpg"),
            ("hqdefault", f"{base}/hqdefault.jpg"),
        )
        errors: list[str] = []
        for quality, url in candidates:
            try:
                response = (
                    httpx.get(url, timeout=30, proxy=self.proxy_url)
                    if self.proxy_url
                    else httpx.get(url, timeout=30)
                )
                response.raise_for_status()
                media_type = response.headers.get(
                    "content-type",
                    "",
                ).split(";", maxsplit=1)[0].lower()
                if media_type not in {"image/jpeg", "image/png"}:
                    raise ValueError("response is not a supported image")
                if not response.content:
                    raise ValueError("response is empty")
                image = Image.open(io.BytesIO(response.content))
                image.load()
                actual_media_type = {
                    "JPEG": "image/jpeg",
                    "PNG": "image/png",
                }.get(str(image.format).upper())
                if actual_media_type != media_type:
                    raise ValueError(
                        "response image does not match its media type"
                    )
                return SourceThumbnail(
                    content=response.content,
                    source_url=url,
                    media_type=media_type,
                    width=image.width,
                    height=image.height,
                    retrieval_result=f"selected:{quality}",
                )
            except (
                httpx.HTTPError,
                OSError,
                UnidentifiedImageError,
                ValueError,
            ) as error:
                errors.append(f"{url}: {error}")
        raise RuntimeError(
            "YouTube source thumbnail failed: " + "; ".join(errors)[-1000:]
        )

    def _page_thumbnail(self, candidate: SourceCandidate) -> SourceThumbnail:
        """The image another platform shows for the episode, as JPEG."""
        metadata = self._metadata(candidate.source_url)
        thumbnails = [
            item
            for item in metadata.get("thumbnails") or []
            if isinstance(item, dict) and item.get("url")
        ]
        url = metadata.get("thumbnail") or (
            thumbnails[-1]["url"] if thumbnails else None
        )
        if not url:
            raise RuntimeError("Source has no thumbnail")
        try:
            response = (
                httpx.get(
                    str(url),
                    timeout=30,
                    follow_redirects=True,
                    proxy=self.proxy_url,
                )
                if self.proxy_url
                else httpx.get(str(url), timeout=30, follow_redirects=True)
            )
            response.raise_for_status()
            image = Image.open(io.BytesIO(response.content))
            image.load()
        except (httpx.HTTPError, OSError, UnidentifiedImageError) as error:
            raise RuntimeError(f"Source thumbnail failed: {error}") from error
        content = response.content
        if str(image.format).upper() not in {"JPEG", "PNG"}:
            buffer = io.BytesIO()
            image.convert("RGB").save(buffer, format="JPEG", quality=92)
            content = buffer.getvalue()
        return SourceThumbnail(
            content=content,
            source_url=str(url),
            media_type=(
                "image/png" if str(image.format).upper() == "PNG" else "image/jpeg"
            ),
            width=image.width,
            height=image.height,
            retrieval_result="selected:page",
        )

    def _captions(self, metadata: dict[str, Any]) -> str | None:
        tracks = (
            (metadata.get("subtitles") or {}).get("en")
            or (metadata.get("automatic_captions") or {}).get("en")
            or []
        )
        if not tracks:
            return None
        preferred = next(
            (
                track
                for track in tracks
                if track.get("ext") in {"vtt", "json3"}
            ),
            tracks[0],
        )
        url = preferred.get("url")
        if not url:
            return None
        try:
            response = (
                httpx.get(
                    str(url),
                    timeout=30,
                    proxy=self.proxy_url,
                )
                if self.proxy_url
                else httpx.get(str(url), timeout=30)
            )
            response.raise_for_status()
        except httpx.HTTPError:
            return None
        return response.text

    def _command(self, *arguments: str) -> list[str]:
        executable = (
            self.runtime.executable if self.runtime is not None else self.executable
        )
        command = [executable]
        if self.proxy_url:
            command.extend(("--proxy", self.proxy_url))
        command.extend(arguments)
        return command

    @staticmethod
    def _json(command: list[str]) -> dict[str, Any]:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
            timeout=120,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "Source request failed: " + _error_summary(completed.stderr)
            )
        value = json.loads(completed.stdout)
        if not isinstance(value, dict):
            raise TypeError("YouTube metadata must be an object")
        return value


def _platform(metadata: dict[str, Any]) -> str:
    """yt-dlp's extractor name, e.g. ``youtube``, ``applepodcasts``.

    yt-dlp always reports it; metadata without it predates other platforms
    and is YouTube's.
    """
    name = str(metadata.get("extractor_key") or metadata.get("extractor") or "")
    return re.sub(r"[^a-z0-9]", "", name.lower()) or "youtube"


def _safe_key(
    platform: str,
    value: str,
    *,
    limit: int,
    fallback: str = "",
) -> str:
    """``platform:id`` that fits its column; a long or empty id is hashed."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", value)
    key = f"{platform}:{cleaned}"
    if cleaned and platform != "generic" and len(key) <= limit:
        return key
    digest = hashlib.sha256((value or fallback).encode()).hexdigest()[:24]
    return f"{platform}:{digest}"


def _published_at(metadata: dict[str, Any]) -> datetime:
    for field in ("timestamp", "release_timestamp"):
        value = metadata.get(field)
        if value:
            return datetime.fromtimestamp(float(value), tz=UTC)
    for field in ("upload_date", "release_date"):
        value = str(metadata.get(field) or "")
        if re.fullmatch(r"\d{8}", value):
            return datetime.strptime(value, "%Y%m%d").replace(tzinfo=UTC)
    return datetime.now(tz=UTC)


def _error_summary(stderr: str) -> str:
    """yt-dlp's ERROR lines, without the warnings printed before them."""
    errors = [line for line in stderr.splitlines() if line.startswith("ERROR:")]
    return ("\n".join(errors) or stderr.strip())[-1000:]
