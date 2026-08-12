"""Generate the versioned ChargePath M3 correctness and runtime evidence."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from chargepath.evaluation import (
    build_correctness_artifact,
    build_runtime_artifact,
    canonical_json_bytes,
    load_benchmark_manifest,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPOSITORY_ROOT / "data/benchmarks/m3/v1/manifest.json"
DEFAULT_CORRECTNESS_OUT = REPOSITORY_ROOT / "docs/evidence/m3/correctness_v1.json"
DEFAULT_RUNTIME_OUT = REPOSITORY_ROOT / "docs/evidence/m3/runtime_2026-08-09.json"


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--correctness-out", type=Path, default=DEFAULT_CORRECTNESS_OUT)
    parser.add_argument("--runtime-out", type=Path, default=DEFAULT_RUNTIME_OUT)
    parser.add_argument("--warmups", type=int, default=3)
    parser.add_argument("--repetitions", type=int, default=20)
    return parser.parse_args()


def _write(path: Path, payload: dict[str, object]) -> str:
    content = canonical_json_bytes(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def main() -> None:
    args = _arguments()
    manifest = load_benchmark_manifest(args.manifest)
    command = (
        "python scripts/run_m3_benchmark.py "
        f"--warmups {args.warmups} --repetitions {args.repetitions}"
    )
    correctness = build_correctness_artifact(manifest)
    runtime = build_runtime_artifact(
        manifest,
        warmup_count=args.warmups,
        measured_repetitions=args.repetitions,
        command=command,
    )
    correctness_sha = _write(args.correctness_out, correctness)
    runtime_sha = _write(args.runtime_out, runtime)
    print(f"M3 correctness evidence: {args.correctness_out} (sha256={correctness_sha})")
    print(f"M3 runtime evidence: {args.runtime_out} (sha256={runtime_sha})")


if __name__ == "__main__":
    main()
