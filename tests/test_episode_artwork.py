from __future__ import annotations

import io

import pytest
from PIL import Image, ImageDraw

from transpodcast.artwork import (
    artwork_cache_key,
    render_episode_artwork,
    validate_episode_artwork,
)
from transpodcast.domain import SourceThumbnail


def _image_bytes(
    size: tuple[int, int],
    left: tuple[int, int, int],
    right: tuple[int, int, int] | None = None,
) -> bytes:
    image = Image.new("RGB", size, left)
    if right is not None:
        ImageDraw.Draw(image).rectangle(
            (size[0] // 2, 0, size[0], size[1]),
            fill=right,
        )
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_episode_artwork_is_deterministic_square_rgb_and_preserves_thumbnail(
) -> None:
    source = SourceThumbnail(
        content=_image_bytes((160, 90), (230, 30, 20), (20, 40, 230)),
        source_url="https://example.com/source.png",
        media_type="image/png",
    )
    show_artwork = _image_bytes((64, 64), (20, 220, 50))

    first = render_episode_artwork(source, show_artwork)
    second = render_episode_artwork(source, show_artwork)

    assert first.content == second.content
    assert first.sha256 == second.sha256
    assert first.source_width == 160
    assert first.source_height == 90
    assert first.source_media_type == "image/png"
    image = Image.open(io.BytesIO(first.content))
    assert image.size == (3000, 3000)
    assert image.mode == "RGB"
    left = image.getpixel((240, 1500))
    right = image.getpixel((2760, 1500))
    badge = image.getpixel((2600, 2600))
    assert left[0] > left[2]
    assert right[2] > right[0]
    assert badge[1] > badge[0] and badge[1] > badge[2]


def test_episode_artwork_cache_key_tracks_both_images() -> None:
    source = SourceThumbnail(
        content=_image_bytes((16, 9), (10, 20, 30)),
        source_url="https://example.com/source.png",
        media_type="image/png",
    )
    first_badge = _image_bytes((8, 8), (40, 50, 60))
    second_badge = _image_bytes((8, 8), (60, 50, 40))

    assert artwork_cache_key(source, first_badge) != artwork_cache_key(
        source,
        second_badge,
    )
    changed_source = SourceThumbnail(
        content=_image_bytes((16, 9), (30, 20, 10)),
        source_url=source.source_url,
        media_type=source.media_type,
    )
    assert artwork_cache_key(source, first_badge) != artwork_cache_key(
        changed_source,
        first_badge,
    )


def test_episode_artwork_validator_rejects_transparency() -> None:
    image = Image.new("RGBA", (3000, 3000), (1, 2, 3, 128))
    output = io.BytesIO()
    image.save(output, format="PNG")

    with pytest.raises(ValueError, match="output contract"):
        validate_episode_artwork(output.getvalue())
