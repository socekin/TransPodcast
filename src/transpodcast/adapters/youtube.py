from __future__ import annotations

import hashlib
import io
import json
import os
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

from transpodcast.domain import (
    ChannelResolution,
    RecentVideoWindow,
    SourceArchive,
    SourceCandidate,
    SourceThumbnail,
    SourceVideoPreview,
)


class YtDlpSourceAdapter:
    def __init__(
        self,
        executable: str = "yt-dlp",
        proxy_url: str = "",
        browser_executable: Path | None = None,
    ) -> None:
        self.executable = executable
        self.proxy_url = proxy_url.strip()
        self.browser_executable = browser_executable

    def readiness(self) -> bool:
        browser = self.browser_executable
        return (
            shutil.which(self.executable) is not None
            and browser is not None
            and browser.is_file()
            and os.access(browser, os.X_OK)
        )

    def resolve_channel(self, url: str) -> ChannelResolution:
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
        timestamp = metadata.get("timestamp")
        published_at = (
            datetime.fromtimestamp(float(timestamp), tz=UTC)
            if timestamp
            else datetime.now(tz=UTC)
        )
        return SourceCandidate(
            youtube_video_id=str(metadata["id"]),
            title=str(metadata.get("title") or metadata["id"]),
            description=str(metadata.get("description") or ""),
            duration_seconds=int(metadata.get("duration") or 0),
            language=str(metadata.get("language") or "en"),
            published_at=published_at,
            source_url=str(
                metadata.get("webpage_url")
                or f"https://www.youtube.com/watch?v={metadata['id']}"
            ),
            captions=self._captions(metadata) if include_captions else None,
            youtube_channel_id=str(metadata.get("channel_id") or "") or None,
        )

    def video(self, url: str) -> SourceCandidate:
        metadata = self._json(
            self._command(
                "--dump-single-json",
                "--skip-download",
                "--no-cookies",
                "--no-playlist",
                url,
            )
        )
        return self._candidate(metadata)

    def supported_source_video(
        self,
        url: str,
    ) -> SourceCandidate | None:
        if not self._is_video_url(url):
            return None
        return self.video(url)

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
        if self.browser_executable is None:
            raise RuntimeError(
                "YouTube source archive browser is not configured"
            )
        with TemporaryDirectory(prefix="transpodcast-youtube-") as temp:
            root = Path(temp)
            completed = subprocess.run(
                self._command(
                    "--no-cookies",
                    "--no-playlist",
                    "--extractor-args",
                    "youtube:player_client=mweb",
                    "--extractor-args",
                    (
                        "youtubepot-wpc:browser_path="
                        f"{self.browser_executable}"
                    ),
                    "--format",
                    "bestaudio[ext=m4a]/bestaudio",
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
                    "YouTube source archive failed: "
                    + completed.stderr.strip()[-1000:]
                )
            files = list(root.glob("source.*"))
            if len(files) != 1:
                raise RuntimeError(
                    "YouTube source archive did not produce one audio file"
                )
            path = files[0]
            content = path.read_bytes()
            extension = path.suffix.removeprefix(".").lower()
            return SourceArchive(
                content=content,
                extension=extension,
                media_type=(
                    "audio/mp4" if extension == "m4a" else "audio/webm"
                ),
                checksum=hashlib.sha256(content).hexdigest(),
            )

    def thumbnail(self, candidate: SourceCandidate) -> SourceThumbnail:
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
        command = [self.executable]
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
                "YouTube source request failed: "
                + completed.stderr.strip()[-1000:]
            )
        value = json.loads(completed.stdout)
        if not isinstance(value, dict):
            raise TypeError("YouTube metadata must be an object")
        return value
