from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from pytest import MonkeyPatch

from transpodcast.database import Database


@pytest.fixture(autouse=True)
def dispose_test_databases(monkeypatch: MonkeyPatch) -> Iterator[None]:
    """Close every database created by a test before its temporary files vanish."""

    databases: list[Database] = []
    original_init = Database.__init__

    def tracked_init(database: Database, path: Path) -> None:
        original_init(database, path)
        databases.append(database)

    monkeypatch.setattr(Database, "__init__", tracked_init)
    try:
        yield
    finally:
        for database in reversed(databases):
            database.dispose()
