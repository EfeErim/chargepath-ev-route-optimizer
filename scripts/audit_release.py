"""Fail-closed release-content audit for the ChargePath repository."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST_PATH = REPOSITORY_ROOT / "docs/evidence/m5/release-allowlist.txt"
MAX_RELEASE_FILE_BYTES = 1_048_576
TEXT_SUFFIXES = {
    ".css",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".osm",
    ".ps1",
    ".py",
    ".toml",
    ".txt",
    ".yml",
}


def _release_candidates() -> set[str]:
    command = [
        "git",
        "-c",
        f"safe.directory={REPOSITORY_ROOT.as_posix()}",
        "ls-files",
        "--cached",
        "--others",
        "--exclude-standard",
        "-z",
    ]
    completed = subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
    )
    return {
        item.decode("utf-8").replace("\\", "/") for item in completed.stdout.split(b"\0") if item
    }


def _allowlist() -> list[str]:
    entries = [
        line.strip()
        for line in ALLOWLIST_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if entries != sorted(set(entries)):
        raise ValueError("release allowlist must be sorted and contain no duplicates")
    return entries


def _secret_patterns() -> tuple[re.Pattern[str], ...]:
    private_key = "PRIVATE " + "KEY"
    return (
        re.compile(r"AKIA[0-9A-Z]{16}"),
        re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"),
        re.compile(r"github_pat_[A-Za-z0-9_]{50,}"),
        re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
        re.compile(rf"-----BEGIN (?:RSA |EC |OPENSSH )?{private_key}-----"),
        re.compile(
            r"(?i)(?:api[_-]?key|secret|token|password)\s*[:=]\s*"
            r"[\"']?[A-Za-z0-9+/=_-]{20,}"
        ),
    )


def _audit_paths(candidates: set[str], expected: set[str]) -> int:
    missing = sorted(expected - candidates)
    unexpected = sorted(candidates - expected)
    if missing or unexpected:
        details = []
        if missing:
            details.append(f"missing allowlisted files: {missing}")
        if unexpected:
            details.append(f"unexpected release files: {unexpected}")
        raise ValueError("; ".join(details))

    total_bytes = 0
    forbidden_parts = {".mypy_cache", ".pytest_cache", ".ruff_cache", ".venv", "__pycache__"}
    for relative in sorted(candidates):
        path = REPOSITORY_ROOT / relative
        if forbidden_parts.intersection(path.parts):
            raise ValueError(f"cache or environment entered release: {relative}")
        if relative == ".env" or relative.startswith(("data/raw/", "data/processed/")):
            raise ValueError(f"private or unresolved data entered release: {relative}")
        if path.name.endswith(".osm.pbf") or ".osrm" in path.name or path.suffix == ".pyc":
            raise ValueError(f"generated road or Python artifact entered release: {relative}")
        size = path.stat().st_size
        total_bytes += size
        if size > MAX_RELEASE_FILE_BYTES:
            raise ValueError(f"release file exceeds 1 MiB: {relative} ({size} bytes)")
    return total_bytes


def _audit_text(candidates: set[str]) -> None:
    patterns = _secret_patterns()
    for relative in sorted(candidates):
        path = REPOSITORY_ROOT / relative
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in {
            ".env.example",
            ".gitignore",
            "LICENSE",
        }:
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in patterns:
            if pattern.search(text):
                raise ValueError(f"possible secret matched in release file: {relative}")


def _audit_markdown_links(candidates: set[str]) -> None:
    link_pattern = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
    for relative in sorted(path for path in candidates if path.endswith(".md")):
        source = REPOSITORY_ROOT / relative
        for raw_target in link_pattern.findall(source.read_text(encoding="utf-8")):
            target = raw_target.strip().strip("<>").split("#", 1)[0]
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            resolved = (source.parent / target).resolve()
            try:
                resolved.relative_to(REPOSITORY_ROOT)
            except ValueError as error:
                raise ValueError(
                    f"Markdown link escapes repository: {relative} -> {target}"
                ) from error
            if not resolved.exists():
                raise ValueError(f"broken local Markdown link: {relative} -> {target}")


def _audit_contracts() -> None:
    pyproject = (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
    demo = (REPOSITORY_ROOT / "src/chargepath/demo.py").read_text(encoding="utf-8")
    workflow = (REPOSITORY_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    fixture = json.loads(
        (REPOSITORY_ROOT / "data/sample/synthetic_corridor.json").read_text(encoding="utf-8")
    )

    required_pyproject = (
        'license = "MIT"',
        'license-files = ["LICENSE", "THIRD_PARTY_NOTICES.md", '
        '"licenses/leaflet-BSD-2-Clause.txt"]',
        '"share/chargepath" = ["data/sample/synthetic_corridor.json"]',
        'version = "0.1.0"',
    )
    if not all(value in pyproject for value in required_pyproject):
        raise ValueError("pyproject release version or license contract is missing")
    if "(LICENSE)" not in readme or "(THIRD_PARTY_NOTICES.md)" not in readme:
        raise ValueError("README license or third-party notice link is missing")
    if "© OpenStreetMap contributors" not in demo:
        raise ValueError("visible OpenStreetMap attribution contract is missing")
    if fixture.get("metadata", {}).get("synthetic") is not True:
        raise ValueError("bundled sample is not explicitly marked synthetic")

    workflow_pins = (
        "actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd",
        "actions/setup-python@a309ff8b426b58ec0e2a45f0f869d46889d02405",
        'python-version: "3.11.9"',
    )
    if not all(value in workflow for value in workflow_pins):
        raise ValueError("CI actions or Python are not pinned to the reviewed release versions")


def main() -> None:
    candidates = _release_candidates()
    allowlist = set(_allowlist())
    total_bytes = _audit_paths(candidates, allowlist)
    _audit_text(candidates)
    _audit_markdown_links(candidates)
    _audit_contracts()
    print(
        "Release audit passed: "
        f"{len(candidates)} allowlisted files, {total_bytes} bytes, "
        "no prohibited paths, oversized files, or high-confidence secret patterns."
    )


if __name__ == "__main__":
    main()
