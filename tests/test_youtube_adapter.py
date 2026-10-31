from __future__ import annotations

import io
import subprocess
from pathlib import Path

import httpx
from PIL import Image

from transpodcast.adapters.youtube import YtDlpSourceAdapter
from transpodcast.config import TransPodcastSettings
from transpodcast.production import build_dependencies


def _png() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (32, 18), (1, 2, 3)).save(output, format="PNG")
    return output.getvalue()


def test_thumbnail_skips_a_corrupt_high_resolution_candidate(
    monkeypatch,
) -> None:
    requested: list[str] = []

    def thumbnail_get(url: str, timeout: int) -> httpx.Response:
        requested.append(url)
        content = b"not-an-image" if len(requested) == 1 else _png()
        media_type = "image/jpeg" if len(requested) == 1 else "image/png"
        return httpx.Response(
            status_code=200,
            request=httpx.Request("GET", url),
            content=content,
            headers={"content-type": media_type},
        )

    monkeypatch.setattr(httpx, "get", thumbnail_get)
    source = YtDlpSourceAdapter()._candidate(
        {"id": "video-artwork", "title": "Artwork source"}
    )

    thumbnail = YtDlpSourceAdapter().thumbnail(source)

    assert requested[0].endswith("/maxresdefault.jpg")
    assert requested[1].endswith("/hq720.jpg")
    assert thumbnail.width == 32
    assert thumbnail.height == 18
    assert thumbnail.retrieval_result == "selected:hq720"


def test_caption_rate_limit_does_not_block_video_metadata(
    monkeypatch,
) -> None:
    caption_url = "https://www.youtube.com/api/timedtext?v=video-001"

    def rate_limited_get(url: str, timeout: int) -> httpx.Response:
        assert url == caption_url
        assert timeout == 30
        return httpx.Response(
            status_code=429,
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx, "get", rate_limited_get)

    candidate = YtDlpSourceAdapter()._candidate(
        {
            "id": "video-001",
            "title": "An episode",
            "automatic_captions": {
                "en": [
                    {
                        "ext": "json3",
                        "url": caption_url,
                    }
                ]
            },
        }
    )

    assert candidate.youtube_video_id == "video-001"
    assert candidate.captions is None


def test_youtube_proxy_is_passed_to_metadata_requests(
    monkeypatch,
) -> None:
    captured_command: list[str] = []

    def successful_run(command, **_kwargs):
        captured_command.extend(command)
        return type(
            "Completed",
            (),
            {
                "returncode": 0,
                "stdout": (
                    '{"channel_id":"UC_TEST","channel":"Test",'
                    '"channel_url":"https://www.youtube.com/@test"}'
                ),
                "stderr": "",
            },
        )()

    monkeypatch.setattr("subprocess.run", successful_run)

    resolution = YtDlpSourceAdapter(
        proxy_url="http://127.0.0.1:6152"
    ).resolve_channel("https://www.youtube.com/@test")

    assert resolution.youtube_channel_id == "UC_TEST"
    assert captured_command[:3] == [
        "yt-dlp",
        "--proxy",
        "http://127.0.0.1:6152",
    ]


def test_youtube_proxy_is_passed_to_caption_requests(
    monkeypatch,
) -> None:
    caption_url = "https://www.youtube.com/api/timedtext?v=video-002"
    captured_proxy = ""

    def successful_get(
        url: str,
        *,
        timeout: int,
        proxy: str,
    ) -> httpx.Response:
        nonlocal captured_proxy
        assert url == caption_url
        assert timeout == 30
        captured_proxy = proxy
        return httpx.Response(
            status_code=200,
            request=httpx.Request("GET", url),
            text="captions",
        )

    monkeypatch.setattr(httpx, "get", successful_get)

    candidate = YtDlpSourceAdapter(
        proxy_url="http://127.0.0.1:6152"
    )._candidate(
        {
            "id": "video-002",
            "automatic_captions": {
                "en": [{"ext": "vtt", "url": caption_url}]
            },
        }
    )

    assert captured_proxy == "http://127.0.0.1:6152"
    assert candidate.captions == "captions"


class FakeRuntime:
    """A yt-dlp install whose forced update may bring a newer release."""

    def __init__(self, *, newer_release: bool) -> None:
        self.executable = "/tools/yt-dlp/current/bin/yt-dlp"
        self.newer_release = newer_release
        self.refreshes: list[bool] = []

    def available(self) -> bool:
        return True

    def refresh(self, *, force: bool = False) -> bool:
        self.refreshes.append(force)
        return force and self.newer_release


def _archive_run(failures: int):
    commands: list[list[str]] = []

    def run(command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if len(commands) <= failures:
            return subprocess.CompletedProcess(
                command, 1, "", "ERROR: Requested format is not available"
            )
        output_root = Path(command[command.index("--paths") + 1])
        (output_root / "source.m4a").write_bytes(b"archived audio")
        return subprocess.CompletedProcess(command, 0, "", "")

    return run, commands


def test_source_archive_never_launches_a_browser(monkeypatch) -> None:
    run, commands = _archive_run(failures=0)
    monkeypatch.setattr("subprocess.run", run)
    source = YtDlpSourceAdapter()
    candidate = source._candidate({"id": "video-archive", "title": "Archive"})

    archive = source.archive(candidate)

    (command,) = commands
    assert "--extractor-args" not in command
    assert command[command.index("--format") + 1] == (
        "bestaudio[ext=m4a]/bestaudio/best"
    )
    assert "--extract-audio" in command
    assert archive.content == b"archived audio"
    assert archive.media_type == "audio/mp4"


def test_a_failed_download_retries_once_on_a_newer_ytdlp(monkeypatch) -> None:
    run, commands = _archive_run(failures=1)
    monkeypatch.setattr("subprocess.run", run)
    runtime = FakeRuntime(newer_release=True)
    source = YtDlpSourceAdapter(runtime=runtime)  # type: ignore[arg-type]
    candidate = source._candidate({"id": "video-archive", "title": "Archive"})

    archive = source.archive(candidate)

    assert archive.content == b"archived audio"
    assert len(commands) == 2
    assert commands[0][0] == runtime.executable
    assert runtime.refreshes == [False, True]


def test_a_failed_download_without_a_newer_ytdlp_is_reported(
    monkeypatch,
) -> None:
    run, commands = _archive_run(failures=1)
    monkeypatch.setattr("subprocess.run", run)
    source = YtDlpSourceAdapter(
        runtime=FakeRuntime(newer_release=False)  # type: ignore[arg-type]
    )
    candidate = source._candidate({"id": "video-archive", "title": "Archive"})

    try:
        source.archive(candidate)
    except RuntimeError as error:
        assert "Requested format is not available" in str(error)
    else:
        raise AssertionError("the download failure was hidden")
    assert len(commands) == 1


def _metadata_run(metadata: str, probes: list[list[str]] | None = None):
    def run(command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        if command[0] == "ffprobe":
            if probes is not None:
                probes.append(command)
            return subprocess.CompletedProcess(command, 0, "1834.5\n", "")
        return subprocess.CompletedProcess(command, 0, metadata, "")

    return run


def test_any_link_yt_dlp_supports_resolves_to_one_episode(monkeypatch) -> None:
    monkeypatch.setattr(
        "subprocess.run",
        _metadata_run(
            '{"extractor_key":"ApplePodcasts","id":"1000000000001",'
            '"title":"A fictional episode","duration":2700,'
            '"timestamp":1790000000,"series":"Fictional Podcast",'
            '"thumbnail":"https://podcasts.example.com/art.jpg",'
            '"webpage_url":"https://podcasts.apple.com/us/podcast/x/id1?i=1"}'
        ),
    )

    channel, episode = YtDlpSourceAdapter().resolve_source(
        "https://podcasts.apple.com/us/podcast/x/id1?i=1"
    )

    assert episode is not None
    assert episode.youtube_video_id == "applepodcasts:1000000000001"
    assert episode.duration_seconds == 2700
    assert episode.thumbnail_url == "https://podcasts.example.com/art.jpg"
    assert episode.youtube_channel_id == channel.youtube_channel_id
    assert channel.youtube_channel_id == "applepodcasts:Fictional_Podcast"
    assert channel.title == "Fictional Podcast"


def test_a_direct_audio_link_is_keyed_by_its_address(monkeypatch) -> None:
    probes: list[list[str]] = []
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        "subprocess.run",
        _metadata_run(
            '{"extractor_key":"Generic","id":"episode","title":"episode",'
            '"url":"https://media.example.com/episode.mp3",'
            '"webpage_url":"https://media.example.com/episode.mp3"}',
            probes,
        ),
    )

    channel, episode = YtDlpSourceAdapter().resolve_source(
        "https://media.example.com/episode.mp3"
    )

    assert episode is not None
    assert episode.youtube_video_id.startswith("generic:")
    assert episode.duration_seconds == 1834
    assert probes[0][-1] == "https://media.example.com/episode.mp3"
    assert channel.title == "media.example.com"


def test_a_list_link_is_not_an_episode(monkeypatch) -> None:
    monkeypatch.setattr(
        "subprocess.run",
        _metadata_run(
            '{"_type":"playlist","extractor_key":"ApplePodcasts",'
            '"id":"1","title":"Fictional Podcast","entries":[]}'
        ),
    )

    _channel, episode = YtDlpSourceAdapter().resolve_source(
        "https://podcasts.apple.com/us/podcast/x/id1"
    )

    assert episode is None


def test_only_youtube_channels_can_be_followed() -> None:
    try:
        YtDlpSourceAdapter().resolve_channel(
            "https://podcasts.apple.com/us/podcast/x/id1"
        )
    except ValueError as error:
        assert "YouTube" in str(error)
    else:
        raise AssertionError("a non-YouTube channel was followed")


def test_production_runs_yt_dlp_from_its_managed_install(tmp_path: Path) -> None:
    settings = TransPodcastSettings(
        _env_file=None,
        database_path=tmp_path / "transpodcast.sqlite3",
        ytdlp_root=tmp_path / "tools" / "yt-dlp",
    )

    source = build_dependencies(settings).source

    assert isinstance(source, YtDlpSourceAdapter)
    assert source.runtime is not None
    assert source.runtime.root == tmp_path / "tools" / "yt-dlp"
    # Until the first install, the locked yt-dlp is used.
    assert source.runtime.executable.endswith("/yt-dlp")


def test_a_failed_lookup_reports_yt_dlp_errors_without_warnings(
    monkeypatch,
) -> None:
    def run(command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            1,
            "",
            "WARNING: [generic] Falling back on generic information extractor\n"
            "ERROR: Unsupported URL: https://example.com/show",
        )

    monkeypatch.setattr("subprocess.run", run)

    try:
        YtDlpSourceAdapter().resolve_source("https://example.com/show")
    except RuntimeError as error:
        assert str(error) == (
            "Source request failed: ERROR: Unsupported URL: "
            "https://example.com/show"
        )
    else:
        raise AssertionError("the unsupported link was accepted")
