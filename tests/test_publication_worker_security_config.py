from __future__ import annotations

import json
from pathlib import Path


def test_worker_observability_does_not_persist_private_request_urls() -> None:
    root = Path(__file__).resolve().parents[1]
    config = json.loads(
        (root / "workers/publication/wrangler.jsonc").read_text(
            encoding="utf-8"
        )
    )

    observability = config["observability"]
    assert observability["logs"]["invocation_logs"] is False
    assert observability["traces"]["enabled"] is False
