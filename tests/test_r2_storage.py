from __future__ import annotations

from typing import Any

from transpodcast.adapters.r2 import R2PublicationStorage


class RecordedS3Client:
    def __init__(self) -> None:
        self.puts: list[dict[str, Any]] = []

    def put_object(self, **arguments: Any) -> None:
        self.puts.append(arguments)

    def list_objects_v2(self, **arguments: Any) -> dict[str, Any]:
        prefix = str(arguments["Prefix"])
        continuation = arguments.get("ContinuationToken")
        if prefix == "publications/" and continuation is None:
            return {
                "Contents": [
                    {"Key": "publications/show/episode-2.m4a", "Size": 20},
                    {"Key": "publications/show/episode-2.json", "Size": 2},
                ],
                "IsTruncated": True,
                "NextContinuationToken": "page-2",
            }
        if prefix == "publications/":
            return {
                "Contents": [
                    {"Key": "publications/show/episode-1.m4a", "Size": 10}
                ],
                "IsTruncated": False,
            }
        return {
            "Contents": [
                {"Key": "backups/monthly/2026-07.tar.gz", "Size": 12},
                {"Key": "backups/daily/2026-07-28.tar.gz", "Size": 8},
            ],
            "IsTruncated": False,
        }


def storage_with(client: RecordedS3Client) -> R2PublicationStorage:
    storage = object.__new__(R2PublicationStorage)
    storage.bucket = "transpodcast-test"
    storage.client = client
    storage.configured = True
    return storage


def test_r2_backup_replaces_same_day_snapshot() -> None:
    client = RecordedS3Client()
    storage = storage_with(client)

    storage.put_backup("daily/2026-07-28.tar.gz", b"first")
    storage.put_backup("daily/2026-07-28.tar.gz", b"updated")

    assert [request["Body"] for request in client.puts] == [
        b"first",
        b"updated",
    ]
    assert all(
        request["CacheControl"] == "private, no-store"
        for request in client.puts
    )


def test_r2_object_listing_is_paginated_and_sorted() -> None:
    storage = storage_with(RecordedS3Client())

    assert storage.publication_media_paths() == [
        "show/episode-1.m4a",
        "show/episode-2.m4a",
    ]
    assert storage.backup_paths() == [
        "daily/2026-07-28.tar.gz",
        "monthly/2026-07.tar.gz",
    ]
    assert storage.usage_by_class()["publications"] == 32
