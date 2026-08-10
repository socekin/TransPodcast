from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFilter, ImageOps, UnidentifiedImageError

from transpodcast.domain import SourceThumbnail

EPISODE_ARTWORK_TEMPLATE_VERSION = "episode-artwork-template-v1"
_OUTPUT_SIZE = 3000


@dataclass(frozen=True)
class EpisodeArtworkArtifact:
    content: bytes
    sha256: str
    source_url: str
    source_sha256: str
    source_media_type: str
    source_width: int
    source_height: int
    retrieval_result: str
    show_artwork_sha256: str
    width: int
    height: int
    media_type: str = "image/jpeg"
    template_version: str = EPISODE_ARTWORK_TEMPLATE_VERSION

    def metadata_json(self) -> str:
        return json.dumps(
            {
                "status": "completed",
                "template_version": self.template_version,
                "source_url": self.source_url,
                "source_sha256": self.source_sha256,
                "source_media_type": self.source_media_type,
                "source_width": self.source_width,
                "source_height": self.source_height,
                "retrieval_result": self.retrieval_result,
                "show_artwork_sha256": self.show_artwork_sha256,
                "output_sha256": self.sha256,
                "width": self.width,
                "height": self.height,
                "media_type": self.media_type,
                "bytes": len(self.content),
            },
            sort_keys=True,
        )


def render_episode_artwork(
    thumbnail: SourceThumbnail,
    show_artwork: bytes,
) -> EpisodeArtworkArtifact:
    if not thumbnail.content:
        raise ValueError("Source thumbnail is empty")
    if not show_artwork:
        raise ValueError("Show Artwork is unavailable")
    source = _decode_rgb(thumbnail.content, "Source thumbnail")
    badge = _decode_rgb(show_artwork, "Show Artwork")

    background = ImageOps.fit(
        source,
        (_OUTPUT_SIZE, _OUTPUT_SIZE),
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.5),
    ).filter(ImageFilter.GaussianBlur(radius=90))
    background = Image.blend(
        background,
        Image.new("RGB", background.size, (12, 16, 22)),
        0.28,
    )

    foreground = ImageOps.contain(
        source,
        (2760, 1740),
        method=Image.Resampling.LANCZOS,
    )
    foreground_mask = _rounded_mask(foreground.size, radius=52)
    foreground_x = (_OUTPUT_SIZE - foreground.width) // 2
    foreground_y = (_OUTPUT_SIZE - foreground.height) // 2
    shadow_mask = foreground_mask.filter(ImageFilter.GaussianBlur(radius=30))
    shadow = Image.new("RGB", foreground.size, (0, 0, 0))
    background.paste(
        shadow,
        (foreground_x, foreground_y + 28),
        shadow_mask.point(lambda value: round(value * 0.46)),
    )
    background.paste(
        foreground,
        (foreground_x, foreground_y),
        foreground_mask,
    )

    badge_size = 540
    badge_inset = 120
    badge_border = 16
    badge_image = ImageOps.fit(
        badge,
        (badge_size, badge_size),
        method=Image.Resampling.LANCZOS,
    )
    badge_mask = _rounded_mask((badge_size, badge_size), radius=96)
    framed_size = badge_size + badge_border * 2
    framed = Image.new("RGB", (framed_size, framed_size), (255, 255, 255))
    framed_mask = _rounded_mask((framed_size, framed_size), radius=112)
    framed.paste(
        badge_image,
        (badge_border, badge_border),
        badge_mask,
    )
    badge_x = _OUTPUT_SIZE - badge_inset - framed_size
    badge_y = _OUTPUT_SIZE - badge_inset - framed_size
    badge_shadow = framed_mask.filter(ImageFilter.GaussianBlur(radius=24))
    background.paste(
        Image.new("RGB", framed.size, (0, 0, 0)),
        (badge_x, badge_y + 18),
        badge_shadow.point(lambda value: round(value * 0.42)),
    )
    background.paste(framed, (badge_x, badge_y), framed_mask)

    output = io.BytesIO()
    background.save(
        output,
        format="JPEG",
        quality=92,
        subsampling=0,
        optimize=False,
        progressive=False,
    )
    content = output.getvalue()
    validate_episode_artwork(content)
    return EpisodeArtworkArtifact(
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
        source_url=thumbnail.source_url,
        source_sha256=hashlib.sha256(thumbnail.content).hexdigest(),
        source_media_type=thumbnail.media_type,
        source_width=source.width,
        source_height=source.height,
        retrieval_result=thumbnail.retrieval_result,
        show_artwork_sha256=hashlib.sha256(show_artwork).hexdigest(),
        width=_OUTPUT_SIZE,
        height=_OUTPUT_SIZE,
    )


def artwork_cache_key(
    thumbnail: SourceThumbnail,
    show_artwork: bytes,
) -> str:
    payload = {
        "source_sha256": hashlib.sha256(thumbnail.content).hexdigest(),
        "show_artwork_sha256": hashlib.sha256(show_artwork).hexdigest(),
        "template_version": EPISODE_ARTWORK_TEMPLATE_VERSION,
        "output": "3000x3000-rgb-jpeg",
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()
    ).hexdigest()


def _decode_rgb(content: bytes, label: str) -> Image.Image:
    try:
        image = Image.open(io.BytesIO(content))
        image.load()
    except (OSError, UnidentifiedImageError) as error:
        raise ValueError(f"{label} is not a valid image") from error
    if image.width < 1 or image.height < 1:
        raise ValueError(f"{label} has invalid dimensions")
    return image.convert("RGB")


def _rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size[0] - 1, size[1] - 1),
        radius=radius,
        fill=255,
    )
    return mask


def validate_episode_artwork(content: bytes) -> None:
    try:
        image = Image.open(io.BytesIO(content))
        image.load()
    except (OSError, UnidentifiedImageError) as error:
        raise ValueError("Episode Artwork is not a valid image") from error
    if (
        image.size != (_OUTPUT_SIZE, _OUTPUT_SIZE)
        or image.mode != "RGB"
        or image.format not in {"JPEG", "PNG"}
    ):
        raise ValueError("Episode Artwork output contract is invalid")
