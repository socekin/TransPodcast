from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
LINK_PATTERN = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
IGNORED_PREFIXES = ("#", "http://", "https://", "mailto:")


def markdown_files() -> list[Path]:
    ignored = {".git", ".venv", "node_modules", "dist", ".cache"}
    return sorted(
        path
        for path in ROOT.rglob("*.md")
        if not ignored.intersection(path.relative_to(ROOT).parts)
    )


def local_target(raw_target: str) -> str | None:
    target = raw_target.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1]
    if target.startswith(IGNORED_PREFIXES):
        return None
    target = target.split("#", maxsplit=1)[0]
    return unquote(target) or None


def main() -> int:
    missing: list[str] = []
    for document in markdown_files():
        for line_number, line in enumerate(
            document.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            for match in LINK_PATTERN.finditer(line):
                target = local_target(match.group(1))
                if target is None:
                    continue
                resolved = (document.parent / target).resolve()
                if resolved.exists():
                    continue
                relative_document = document.relative_to(ROOT)
                missing.append(f"{relative_document}:{line_number} -> {target}")

    if missing:
        print("Broken local Markdown links:", file=sys.stderr)
        for item in missing:
            print(f"- {item}", file=sys.stderr)
        return 1

    print("Documentation link check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
