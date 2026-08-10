from __future__ import annotations

import io

import httpx
from PIL import Image

from transpodcast.adapters.youtube import YtDlpSourceAdapter


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
