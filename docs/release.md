# Release evidence and procedure

## Candidate identity

- project version: `0.1.0`;
- release candidate date: 2026-08-10;
- Python: CPython 3.11.9;
- supported CI environments: `windows-2025` x64 and `ubuntu-24.04` x64;
- Python runtime dependencies: none;
- verification graph: [`constraints/release-py311.txt`](../constraints/release-py311.txt);
- release contents: [`docs/evidence/m5/release-allowlist.txt`](evidence/m5/release-allowlist.txt).

This is a source release candidate for a local portfolio project. It is not a hosted navigation
service, safety-certified route planner, live charging feed, or claim of real-world SOC accuracy.

## Clean installation

Windows PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade "pip==26.2" "setuptools==83.0.0"
python -m pip install --constraint constraints/release-py311.txt --editable ".[dev]"
.\scripts\check.ps1
```

Ubuntu 24.04 with CPython 3.11 and PowerShell 7:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade "pip==26.2" "setuptools==83.0.0"
python -m pip install --constraint constraints/release-py311.txt --editable ".[dev]"
pwsh ./scripts/check.ps1
```

The Windows clean-install transcript is recorded in
[`docs/evidence/m5/windows-clean-install.txt`](evidence/m5/windows-clean-install.txt). Ubuntu is
validated by the same repository gate in GitHub Actions; a local Windows result is not presented as
Linux evidence.

The canonical gate additionally builds the `0.1.0` wheel without build isolation, verifies that its
web assets, exact synthetic fixture, MIT license, and third-party notices are present, installs that
wheel non-editably into another new environment, and loads the default fixture from outside the
repository. Generated wheel and environment files remain temporary and are not release-source files.

## Release-content controls

`python scripts/audit_release.py` compares every tracked or non-ignored untracked candidate against
the exact sorted allowlist. It fails on missing or unexpected files, ignored/private data paths,
caches, Python bytecode, OSRM build outputs, files over 1 MiB, and high-confidence secret patterns.
It also checks the MIT metadata, visible OpenStreetMap attribution, synthetic sample flag, and exact
CI action/Python pins. `scripts/check_wheel.py` separately audits the built artifact rather than
assuming that a source/editable install proves wheel completeness.

The 15.4 MB EPDK response under ignored `data/raw/` is deliberately outside the candidate. Its reuse
status remains unresolved. The candidate contains only the response's metadata/checksum, reviewed
normalization code, and project-authored synthetic fixtures. The included OSRM request/response
fixture was generated from the included project-authored synthetic OSM ring.

## Licensing and attribution

Project-authored code and documentation use the [MIT License](../LICENSE). Browser/provider software,
verification tools, OpenStreetMap data terms, and the unresolved EPDK redistribution boundary are
listed in the [third-party notices](../THIRD_PARTY_NOTICES.md). OpenStreetMap attribution appears in
the map UI even when the basemap fails.

## Evidence already in the candidate

- architecture and optimizer contracts: [`docs/architecture.md`](architecture.md) and
  [`docs/algorithm.md`](algorithm.md);
- deterministic benchmark and limits: [`docs/results.md`](results.md), with normalized JSON evidence
  under `docs/evidence/m3/`;
- reviewed primary demo image: `docs/evidence/m4/fixture-primary.png`;
- local interactive browser acceptance: `docs/evidence/m5/browser-smoke-2026-08-12.md`;
- full readiness boundary: [`docs/limitations.md`](limitations.md);
- milestone acceptance records: [`PROJECT_STATE.md`](../PROJECT_STATE.md).

## Approval-gated finalization

M5 is complete only after the exact candidate is committed and pushed with explicit approval, both
matrix jobs are green, the immutable commit and run URLs are recorded in `PROJECT_STATE.md`, and the
allowlist is rechecked at that commit. Tagging and creating a GitHub release remain separate explicit
approval steps.
