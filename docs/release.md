# Releasing

ChargePath is released as a source distribution and wheel attached to a GitHub Release.
Version history is in the [changelog](../CHANGELOG.md).

## Supported environments

- CPython 3.11.9 and 3.13.5
- `windows-2025` and `ubuntu-24.04` (x64), both exercised by CI
- no runtime dependencies; the verification environment is pinned in
  [`constraints/release-py311.txt`](../constraints/release-py311.txt)

## Checklist

1. Install into a fresh environment and run the full gate:

   ```bash
   python -m pip install --constraint constraints/release-py311.txt --editable ".[dev]"
   pwsh ./scripts/check.ps1
   ```

   The gate runs the tests, Ruff, strict mypy, `pip check`, the offline demo, the wheel
   check and the release-content audit described below.
2. Bump `version` in `pyproject.toml` and move the `Unreleased` changelog entries under
   the new version.
3. Wait for CI to pass on the release commit, then create an annotated tag
   (`git tag -a vX.Y.Z`) and a GitHub Release with the changelog section as notes.

## Release-content audit

`scripts/audit_release.py` compares every tracked or non-ignored file with the sorted
allowlist in [`scripts/release-allowlist.txt`](../scripts/release-allowlist.txt). It fails
on unexpected or missing files, private data paths, caches, files over 1 MiB, likely
secrets, missing OpenStreetMap attribution and unpinned CI actions. Adding a file to the
repository therefore means adding it to the allowlist in the same commit.

`scripts/check_wheel.py` builds the wheel, checks that the web assets, sample fixture and
license notices are inside it, installs it into a new environment and serves the demo
from outside the repository.

## Data that is never released

The raw EPDK station snapshot stays under the ignored `data/raw/` directory because its
redistribution terms are unconfirmed. Releases contain only its checksum and metadata,
derived aggregate results, and project-authored synthetic fixtures. See the
[third-party notices](../THIRD_PARTY_NOTICES.md) for software and data licenses.
