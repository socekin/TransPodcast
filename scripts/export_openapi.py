from __future__ import annotations

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from transpodcast.api import create_app
from transpodcast.testing import AcceptanceHarness


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export the TransPodcast FastAPI OpenAPI contract."
    )
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    with TemporaryDirectory(prefix="transpodcast-openapi-") as temp:
        dependencies = AcceptanceHarness.ready(Path(temp)).dependencies
        schema = create_app(dependencies).openapi()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(schema, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
