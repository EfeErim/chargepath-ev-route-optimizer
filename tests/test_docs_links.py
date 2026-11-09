"""Every relative Markdown link in the tracked docs points at a file in the repository."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
LINK_PATTERN = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")


def _tracked_markdown() -> list[Path]:
    completed = subprocess.run(
        ["git", "ls-files", "-z", "*.md"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
    )
    names = [item.decode("utf-8") for item in completed.stdout.split(b"\0") if item]
    return [REPOSITORY_ROOT / name for name in names]


def test_relative_markdown_links_resolve() -> None:
    broken: list[str] = []
    for source in _tracked_markdown():
        for raw_target in LINK_PATTERN.findall(source.read_text(encoding="utf-8")):
            target = raw_target.strip().strip("<>").split("#", 1)[0]
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            if not (source.parent / target).resolve().exists():
                broken.append(f"{source.relative_to(REPOSITORY_ROOT)} -> {target}")
    assert broken == []
