from __future__ import annotations

import argparse
import json
import re
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from fastapi.testclient import TestClient

from transpodcast.api import create_app
from transpodcast.database import Database
from transpodcast.testing import (
    AcceptanceHarness,
    capture_sql_statements,
    seed_owner_read_performance_fixture,
)

_ARTIFACT_COLUMNS = {
    "episodes.chapters_json",
    "episodes.quality_json",
    "episodes.transcript_json",
    "processing_runs.analysis_json",
    "processing_runs.artwork_json",
    "processing_runs.content_edit_json",
    "processing_runs.publication_metadata_json",
    "processing_runs.render_json",
    "processing_runs.translation_json",
}


def _select_expressions(statement: str) -> list[str]:
    match = re.search(r"\bSELECT\s+(.*?)\s+FROM\s", statement, re.I | re.S)
    if match is None:
        return []
    expressions: list[str] = []
    current: list[str] = []
    depth = 0
    for character in match.group(1):
        if character == "(":
            depth += 1
        elif character == ")":
            depth = max(0, depth - 1)
        if character == "," and depth == 0:
            expressions.append("".join(current))
            current = []
            continue
        current.append(character)
    expressions.append("".join(current))
    return [
        re.split(r"\s+AS\s+", " ".join(expression.split()), flags=re.I)[0]
        for expression in expressions
        if expression.strip()
    ]


def _selected_column_profile(statements: list[str]) -> dict[str, object]:
    statement_columns = [
        _select_expressions(statement)
        for statement in statements
        if statement.lstrip().upper().startswith("SELECT")
    ]
    selected_columns = sorted(
        {column for columns in statement_columns for column in columns}
    )
    return {
        "selected_columns": selected_columns,
        "statement_columns": statement_columns,
        "artifact_columns_selected": sorted(
            set(selected_columns).intersection(_ARTIFACT_COLUMNS)
        ),
    }


def _seed_fixture(database: Database, *, run_count: int, artifact_mib: int) -> None:
    run_artifact_columns = 6
    payload_size = max(
        1,
        (artifact_mib * 1024 * 1024) // (run_count * run_artifact_columns),
    )
    seed_owner_read_performance_fixture(
        database,
        run_count=run_count,
        artifact_payload_bytes=payload_size,
        include_episodes=True,
    )


def _measure_endpoint(
    client: TestClient,
    database: Database,
    endpoint: str,
    warm_requests: int,
) -> dict[str, object]:
    started = perf_counter()
    response = client.get(endpoint)
    cold_ms = (perf_counter() - started) * 1000
    response.raise_for_status()
    warm_ms: list[float] = []
    for _ in range(warm_requests):
        started = perf_counter()
        response = client.get(endpoint)
        warm_ms.append((perf_counter() - started) * 1000)
        response.raise_for_status()
    with capture_sql_statements(database) as statements:
        client.get(endpoint).raise_for_status()
    return {
        "cold_ms": round(cold_ms, 1),
        "warm_ms": [round(value, 1) for value in warm_ms],
        "response_bytes": len(response.content),
        "sql_statements": len(statements),
        "selected_column_profile": _selected_column_profile(statements),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark lightweight Owner Console read endpoints.",
    )
    parser.add_argument("--runs", type=int, default=22)
    parser.add_argument("--artifact-mib", type=int, default=43)
    parser.add_argument("--warm-requests", type=int, default=3)
    args = parser.parse_args()
    if args.runs < 1 or args.artifact_mib < 1 or args.warm_requests < 1:
        parser.error("All numeric options must be positive.")

    with TemporaryDirectory(prefix="transpodcast-owner-read-") as temporary:
        root = Path(temporary)
        harness = AcceptanceHarness.ready(root / "harness")
        fixture_database = Database(root / "benchmark.sqlite3")
        fixture_database.create_schema()
        _seed_fixture(
            fixture_database,
            run_count=args.runs,
            artifact_mib=args.artifact_mib,
        )
        fixture_database.dispose()
        measured_database = Database(root / "benchmark.sqlite3")
        dependencies = replace(
            harness.dependencies,
            database=measured_database,
            artifact_root=root / "artifacts",
        )
        client = TestClient(create_app(dependencies))
        results = {
            "fixture": {
                "processing_runs": args.runs,
                "run_artifact_mib": args.artifact_mib,
            },
            "endpoints": {
                endpoint: _measure_endpoint(
                    client,
                    measured_database,
                    endpoint,
                    args.warm_requests,
                )
                for endpoint in (
                    "/api/v1/episode-library",
                    "/api/v1/processing-runs",
                )
            },
        }
        print(json.dumps(results, indent=2, sort_keys=True))
        measured_database.dispose()


if __name__ == "__main__":
    main()
