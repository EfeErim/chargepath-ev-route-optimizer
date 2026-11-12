"""Build a static, browser-only copy of the synthetic fixture demo.

The output runs the ChargePath Python package in Pyodide and needs no server-side code. Only the
bundled synthetic fixture is included. The script builds files; the Pages workflow deploys them.
"""

from __future__ import annotations

import argparse
import re
import shutil
import zipfile
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = REPOSITORY_ROOT / "src" / "chargepath" / "web"
PACKAGE_ROOT = REPOSITORY_ROOT / "src" / "chargepath"
FIXTURE = REPOSITORY_ROOT / "data" / "sample" / "synthetic_corridor.json"
BRIDGE = Path(__file__).resolve().parent / "static-demo" / "static-api.js"
# Pyodide 0.27 ships CPython 3.12, inside the project's supported 3.11+ range.
PYODIDE_VERSION = "0.27.7"
# Subresource-integrity hashes of each supported release's pyodide.js loader. The loader then
# fetches the WebAssembly runtime and standard library from the same pinned CDN directory.
PYODIDE_INTEGRITY = {
    "0.27.7": "sha384-90so5tCKvl0xs9agU29IMKlAVzhfzFX7QO//YxQkRhJG58bBZrFN+2ZTRB026X5X",
}
WEB_ASSETS = ("app.css", "app.js", "leaflet.css", "leaflet.js")


def build(output: Path, *, pyodide_version: str = PYODIDE_VERSION) -> list[Path]:
    """Write the static demo into ``output`` and return the files written, sorted."""

    if not re.fullmatch(r"\d+\.\d+\.\d+", pyodide_version):
        raise ValueError("pyodide_version must be an exact x.y.z release")
    if pyodide_version not in PYODIDE_INTEGRITY:
        raise ValueError(f"no integrity hash is recorded for Pyodide {pyodide_version}")
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    for name in WEB_ASSETS:
        shutil.copyfile(WEB_ROOT / name, output / name)
    shutil.copyfile(FIXTURE, output / "synthetic_corridor.json")
    (output / "static-api.js").write_text(
        BRIDGE.read_text(encoding="utf-8").replace("__PYODIDE_VERSION__", pyodide_version),
        encoding="utf-8",
    )
    (output / "index.html").write_text(
        static_index(
            (WEB_ROOT / "index.html").read_text(encoding="utf-8"),
            pyodide_version=pyodide_version,
        ),
        encoding="utf-8",
    )
    _write_source_archive(output / "chargepath-src.zip")
    # GitHub Pages would otherwise run Jekyll over the output.
    (output / ".nojekyll").write_text("", encoding="utf-8")
    return sorted(path for path in output.iterdir())


def static_index(html: str, *, pyodide_version: str) -> str:
    """Make asset links relative and load Pyodide plus the bridge before the app script."""

    html = html.replace('href="/"', 'href="./"')
    html = re.sub(r'(href|src)="/([\w.-]+)"', r'\1="\2"', html)
    marker = '<script src="leaflet.js"></script>'
    if html.count(marker) != 1 or "/api/" in html:
        raise ValueError("index.html no longer matches the static build's expectations")
    loader = (
        f'<script src="https://cdn.jsdelivr.net/pyodide/v{pyodide_version}/full/pyodide.js" '
        f'integrity="{PYODIDE_INTEGRITY[pyodide_version]}" crossorigin="anonymous"></script>\n    '
        '<script src="static-api.js"></script>\n    '
    )
    return html.replace(marker, loader + marker)


def _write_source_archive(target: Path) -> None:
    # Fixed timestamps keep the archive byte-identical for identical sources.
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(PACKAGE_ROOT.rglob("*.py")):
            relative = path.relative_to(PACKAGE_ROOT.parent).as_posix()
            info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=REPOSITORY_ROOT / "outputs" / "static-demo")
    parser.add_argument("--pyodide-version", default=PYODIDE_VERSION)
    args = parser.parse_args()
    written = build(args.output, pyodide_version=args.pyodide_version)
    print(f"Static demo written to {args.output} ({len(written)} files). It is not deployed.")


if __name__ == "__main__":
    main()
