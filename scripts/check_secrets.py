from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_FILES = (
    r"(^|/)(\.git|\.venv|\.cache|\.mypy_cache|\.ruff_cache|"
    r"node_modules|dist|coverage|htmlcov)(/|$)|"
    r"^workers/publication/worker-configuration\.d\.ts$"
)


def main() -> int:
    executable = shutil.which("detect-secrets")
    if executable is None:
        print("detect-secrets is not installed; run `make setup`.", file=sys.stderr)
        return 2

    completed = subprocess.run(
        [
            executable,
            "scan",
            "--all-files",
            "--exclude-files",
            EXCLUDED_FILES,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        print(completed.stderr or completed.stdout, file=sys.stderr)
        return completed.returncode

    report: dict[str, Any] = json.loads(completed.stdout)
    results: dict[str, list[dict[str, Any]]] = report.get("results", {})
    if not results:
        print("Secret scan passed: no unreviewed candidates found.")
        return 0

    print("Potential secrets detected:", file=sys.stderr)
    for filename, findings in sorted(results.items()):
        for finding in findings:
            line = finding.get("line_number", "?")
            kind = finding.get("type", "unknown detector")
            print(f"- {filename}:{line} ({kind})", file=sys.stderr)
    print(
        "Remove the secret. If the value is a public checksum or revision, "
        "add an inline `pragma: allowlist secret` comment with an explanation.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
