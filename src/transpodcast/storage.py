from __future__ import annotations

from pathlib import Path


class LocalPublicationStorage:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.preview_root = root / "previews"
        self.publication_root = root / "publications"
        self.source_root = root / "sources"
        self.backup_root = root / "backups"
        self.preview_root.mkdir(parents=True, exist_ok=True)
        self.publication_root.mkdir(parents=True, exist_ok=True)
        self.source_root.mkdir(parents=True, exist_ok=True)
        self.backup_root.mkdir(parents=True, exist_ok=True)

    def readiness(self) -> bool:
        return self.root.is_dir()

    def usage_by_class(self) -> dict[str, int]:
        return {
            "private_previews": self._tree_size(self.preview_root),
            "publications": self._tree_size(self.publication_root),
            "source_archives": self._tree_size(self.source_root),
            "state_backups": self._tree_size(self.backup_root),
        }

    def put_preview(self, key: str, content: bytes) -> None:
        self._write(self.preview_root, key, content)

    def read_preview(self, key: str) -> bytes | None:
        path = self._safe_path(self.preview_root, key)
        return path.read_bytes() if path.is_file() else None

    def create_preview_url(
        self,
        key: str,
        expires_seconds: int,
    ) -> str | None:
        del key, expires_seconds
        return None

    def put_source_archive(self, key: str, content: bytes) -> None:
        self._write(self.source_root, key, content)

    def read_source_archive(self, key: str) -> bytes | None:
        path = self._safe_path(self.source_root, key)
        return path.read_bytes() if path.is_file() else None

    def source_archive_paths(self) -> list[str]:
        return sorted(
            str(path.relative_to(self.source_root))
            for path in self.source_root.rglob("*")
            if path.is_file()
        )

    def promote_preview(self, preview_key: str, publication_key: str) -> None:
        preview_path = self._safe_path(self.preview_root, preview_key)
        if not preview_path.is_file():
            raise FileNotFoundError(f"Private Preview was not found: {preview_key}")
        self.put_publication(publication_key, preview_path.read_bytes())

    def put_publication(
        self,
        key: str,
        content: bytes,
        content_type: str = "audio/mp4",
    ) -> None:
        del content_type
        self._write(self.publication_root, key, content)

    def read_publication(self, key: str) -> bytes | None:
        path = self._safe_path(self.publication_root, key)
        return path.read_bytes() if path.is_file() else None

    def publication_media_paths(self) -> list[str]:
        return sorted(
            str(path.relative_to(self.publication_root))
            for path in self.publication_root.rglob("*")
            if path.is_file() and path.suffix.lower() == ".m4a"
        )

    def put_feed_snapshot(self, content: bytes) -> None:
        path = self.root / "feed" / "current.xml"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(content)
        temporary.replace(path)

    def put_feed_token_hash(self, token_hash: str) -> None:
        path = self.root / "configuration" / "feed-token.sha256"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(token_hash, encoding="ascii")
        temporary.replace(path)

    def read_feed_token_hash(self) -> str | None:
        path = self.root / "configuration" / "feed-token.sha256"
        return path.read_text(encoding="ascii") if path.is_file() else None

    def put_backup(self, key: str, content: bytes) -> None:
        path = self._safe_path(self.backup_root, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(content)
        temporary.replace(path)

    def read_backup(self, key: str) -> bytes | None:
        path = self._safe_path(self.backup_root, key)
        return path.read_bytes() if path.is_file() else None

    def backup_paths(self) -> list[str]:
        return sorted(
            str(path.relative_to(self.backup_root))
            for path in self.backup_root.rglob("*")
            if path.is_file()
        )

    def delete_backup(self, key: str) -> None:
        path = self._safe_path(self.backup_root, key)
        if path.is_file():
            path.unlink()

    @staticmethod
    def _safe_path(root: Path, key: str) -> Path:
        candidate = (root / key).resolve()
        resolved_root = root.resolve()
        if candidate != resolved_root and resolved_root not in candidate.parents:
            raise ValueError("Storage key escapes the configured root")
        return candidate

    def _write(self, root: Path, key: str, content: bytes) -> None:
        path = self._safe_path(root, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_bytes() != content:
            raise ValueError(f"Immutable object already exists: {key}")
        path.write_bytes(content)

    @staticmethod
    def _tree_size(root: Path) -> int:
        return sum(
            path.stat().st_size
            for path in root.rglob("*")
            if path.is_file()
        )
