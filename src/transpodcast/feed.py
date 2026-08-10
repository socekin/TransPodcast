from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import format_datetime


@dataclass(frozen=True)
class FeedItem:
    guid: str
    title: str
    description: str
    source_url: str
    publication_key: str
    publication_bytes: int
    chapters_key: str | None
    artwork_key: str | None
    duration_seconds: int
    published_at: datetime


def render_private_feed(
    *,
    items: list[FeedItem],
    publication_domain: str,
    feed_token: str,
    show_title: str,
    show_description: str,
    show_author: str,
    show_artwork_key: str,
) -> str:
    feed_url = f"{publication_domain}/feed/{feed_token}.xml"
    artwork_url = (
        f"{publication_domain}/media/{feed_token}/"
        f"{show_artwork_key}"
    )
    rendered_items = "".join(
        _render_item(
            item,
            publication_domain=publication_domain,
            feed_token=feed_token,
            show_artwork_url=artwork_url,
        )
        for item in items
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0" '
        'xmlns:atom="http://www.w3.org/2005/Atom" '
        'xmlns:content="http://purl.org/rss/1.0/modules/content/" '
        'xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" '
        'xmlns:podcast="https://podcastindex.org/namespace/1.0">'
        "<channel>"
        f"<title>{_xml_escape(show_title)}</title>"
        f"<link>{_xml_escape(publication_domain)}</link>"
        f"<description>{_xml_escape(show_description)}</description>"
        "<language>zh-CN</language>"
        f"<itunes:author>{_xml_escape(show_author)}</itunes:author>"
        "<itunes:explicit>false</itunes:explicit>"
        "<itunes:type>episodic</itunes:type>"
        "<itunes:block>Yes</itunes:block>"
        '<itunes:category text="Technology" />'
        f'<itunes:image href="{_xml_escape(artwork_url)}" />'
        f'<atom:link href="{_xml_escape(feed_url)}" '
        'rel="self" type="application/rss+xml" />'
        f"{rendered_items}</channel></rss>"
    )


def _render_item(
    item: FeedItem,
    *,
    publication_domain: str,
    feed_token: str,
    show_artwork_url: str,
) -> str:
    enclosure = (
        f"{publication_domain}/media/{feed_token}/{item.publication_key}"
    )
    chapters = ""
    if item.chapters_key is not None:
        chapters_url = (
            f"{publication_domain}/media/{feed_token}/{item.chapters_key}"
        )
        chapters = (
            f'<podcast:chapters url="{_xml_escape(chapters_url)}" '
            'type="application/json+chapters" />'
        )
    artwork_url = show_artwork_url
    if item.artwork_key is not None:
        artwork_url = (
            f"{publication_domain}/media/{feed_token}/{item.artwork_key}"
        )
    published_at = item.published_at
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=UTC)
    return (
        "<item>"
        f'<guid isPermaLink="false">{_xml_escape(item.guid)}</guid>'
        f"<title>{_xml_escape(item.title)}</title>"
        f"<description>{_xml_escape(item.description)}</description>"
        f"<link>{_xml_escape(item.source_url)}</link>"
        f"<pubDate>{format_datetime(published_at)}</pubDate>"
        f'<itunes:image href="{_xml_escape(artwork_url)}" />'
        f"<itunes:duration>{item.duration_seconds}</itunes:duration>"
        f'<enclosure url="{_xml_escape(enclosure)}" '
        f'length="{item.publication_bytes}" type="audio/x-m4a" />'
        f"{chapters}"
        "</item>"
    )


def _xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )
