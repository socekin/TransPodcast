from __future__ import annotations

from typing import Any


class R2PublicationStorage:
    def __init__(
        self,
        *,
        endpoint_url: str,
        bucket: str,
        access_key_id: str,
        secret_access_key: str,
    ) -> None:
        import boto3

        self.bucket = bucket
        self.configured = all(
            (
                endpoint_url,
                bucket,
                access_key_id,
                secret_access_key,
            )
        )
        self.client: Any = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name="auto",
        )

    def readiness(self) -> bool:
        return self.configured

    def usage_by_class(self) -> dict[str, int]:
        prefixes = {
            "private_previews": "previews/",
            "publications": "publications/",
            "source_archives": "sources/",
            "state_backups": "backups/",
        }
        return {
            name: self._prefix_size(prefix)
            for name, prefix in prefixes.items()
        }

    def put_preview(self, key: str, content: bytes) -> None:
        self._put_immutable(f"previews/{key}", content)

    def read_preview(self, key: str) -> bytes | None:
        return self._get(f"previews/{key}")

    def create_preview_url(
        self,
        key: str,
        expires_seconds: int,
    ) -> str | None:
        if expires_seconds <= 0:
            return None
        return str(
            self.client.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": self.bucket,
                    "Key": f"previews/{key}",
                },
                ExpiresIn=min(expires_seconds, 7 * 24 * 60 * 60),
            )
        )

    def put_source_archive(self, key: str, content: bytes) -> None:
        self._put_immutable(f"sources/{key}", content)

    def read_source_archive(self, key: str) -> bytes | None:
        return self._get(f"sources/{key}")

    def promote_preview(self, preview_key: str, publication_key: str) -> None:
        destination = f"publications/{publication_key}"
        existing = self._get(destination)
        if existing is not None:
            source = self._get(f"previews/{preview_key}")
            if source != existing:
                raise ValueError(
                    f"Immutable object already exists: {publication_key}"
                )
            return
        self.client.copy_object(
            Bucket=self.bucket,
            CopySource={
                "Bucket": self.bucket,
                "Key": f"previews/{preview_key}",
            },
            Key=destination,
            ContentType="audio/mp4",
        )

    def put_publication(
        self,
        key: str,
        content: bytes,
        content_type: str = "audio/mp4",
    ) -> None:
        self._put_immutable(
            f"publications/{key}",
            content,
            content_type=content_type,
        )

    def read_publication(self, key: str) -> bytes | None:
        return self._get(f"publications/{key}")

    def publication_media_paths(self) -> list[str]:
        return [
            key.removeprefix("publications/")
            for key in self._keys_with_prefix("publications/")
            if key.lower().endswith(".m4a")
        ]

    def put_feed_snapshot(self, content: bytes) -> None:
        self.client.put_object(
            Bucket=self.bucket,
            Key="feed/current.xml",
            Body=content,
            ContentType="application/rss+xml; charset=utf-8",
            CacheControl="private, no-store",
        )

    def put_feed_token_hash(self, token_hash: str) -> None:
        self.client.put_object(
            Bucket=self.bucket,
            Key="configuration/feed-token.sha256",
            Body=token_hash.encode("ascii"),
            ContentType="text/plain",
            CacheControl="private, no-store",
        )

    def read_feed_token_hash(self) -> str | None:
        content = self._get("configuration/feed-token.sha256")
        return content.decode("ascii") if content is not None else None

    def put_backup(self, key: str, content: bytes) -> None:
        self.client.put_object(
            Bucket=self.bucket,
            Key=f"backups/{key}",
            Body=content,
            ContentType="application/gzip",
            CacheControl="private, no-store",
        )

    def read_backup(self, key: str) -> bytes | None:
        return self._get(f"backups/{key}")

    def backup_paths(self) -> list[str]:
        return [
            key.removeprefix("backups/")
            for key in self._keys_with_prefix("backups/")
        ]

    def delete_backup(self, key: str) -> None:
        self.client.delete_object(
            Bucket=self.bucket,
            Key=f"backups/{key}",
        )

    def _put_immutable(
        self,
        key: str,
        content: bytes,
        *,
        content_type: str = "audio/mp4",
    ) -> None:
        existing = self._get(key)
        if existing is not None and existing != content:
            raise ValueError(f"Immutable object already exists: {key}")
        if existing is None:
            self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=content,
                ContentType=content_type,
            )

    def _get(self, key: str) -> bytes | None:
        from botocore.exceptions import ClientError

        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as error:
            status = error.response.get("ResponseMetadata", {}).get(
                "HTTPStatusCode"
            )
            if status == 404:
                return None
            raise
        return bytes(response["Body"].read())

    def _prefix_size(self, prefix: str) -> int:
        total = 0
        for page in self._pages(prefix):
            total += sum(
                int(item.get("Size", 0))
                for item in page.get("Contents", [])
            )
        return total

    def _keys_with_prefix(self, prefix: str) -> list[str]:
        return sorted(
            str(item["Key"])
            for page in self._pages(prefix)
            for item in page.get("Contents", [])
        )

    def _pages(self, prefix: str) -> list[dict[str, Any]]:
        pages: list[dict[str, Any]] = []
        continuation: str | None = None
        while True:
            arguments: dict[str, Any] = {
                "Bucket": self.bucket,
                "Prefix": prefix,
            }
            if continuation:
                arguments["ContinuationToken"] = continuation
            response = self.client.list_objects_v2(**arguments)
            pages.append(response)
            if not response.get("IsTruncated"):
                return pages
            continuation = str(response["NextContinuationToken"])
