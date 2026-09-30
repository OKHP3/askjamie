#!/usr/bin/env python3
"""Prepare the exact public directory uploaded to GitHub Pages.

The repository is a static site, but it also contains scripts, tests, task
metadata, and CI configuration. This helper copies only deployable public
files into a clean directory so publishing cannot accidentally expose the
repository working surface.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "dist-pages"
PUBLIC_ROOT_FILES = {
    "404.html",
    "CNAME",
    "_headers",
    "favicon.ico",
    "humans.txt",
    "index.html",
    "llms.txt",
    "robots.txt",
    "sitemap.xml",
    "site.webmanifest",
    "under-construction.html",
}
PUBLIC_ROOT_DIRS = {"assets", ".well-known"}
PUBLIC_ASSET_DIRS = {"css", "data", "fonts", "img", "js", "vendor"}
PUBLIC_PAGE_DIRS = {
    "about",
    "contact",
    "how-askjamie-works",
    "found-ry",
    "whats-next",
    "legal",
    "lens-system",
    "search",
    "universe",
}
NON_PUBLIC_SUFFIXES = {".pdn"}  # Editable artwork stays in the source repository.


def is_public(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    if not rel.parts:
        return False
    if any(part.startswith(".") for part in rel.parts if part != ".well-known"):
        return False
    if ".well-known" in rel.parts[1:]:
        return False
    if path.suffix.lower() in NON_PUBLIC_SUFFIXES:
        return False
    # copy2 follows file symlinks. Reject links and linked ancestors before
    # they can import material from outside the intended public tree.
    if any(candidate.is_symlink() for candidate in (path, *path.parents)
           if candidate != ROOT and ROOT in candidate.parents):
        return False
    if rel.parts[0] in PUBLIC_PAGE_DIRS:
        return True
    if rel.parts[0] in PUBLIC_ROOT_DIRS:
        if rel.parts[0] != "assets":
            return True
        return len(rel.parts) > 1 and rel.parts[1] in PUBLIC_ASSET_DIRS
    if len(rel.parts) == 1 and rel.name in PUBLIC_ROOT_FILES:
        return True
    return False


def _digest_files(output: Path, copied: list[str]) -> str:
    digest = hashlib.sha256()
    for rel in copied:
        digest.update(rel.encode())
        digest.update((output / rel).read_bytes())
    return digest.hexdigest()


def _manifest_path(output: Path) -> Path:
    return output.parent / f"{output.name}.manifest.json"


def _validate_output(output: Path) -> Path:
    if any(path.is_symlink() for path in (output, *output.parents)):
        raise ValueError("Output and its ancestors must not be symbolic links")
    output = output.resolve()
    root = ROOT.resolve()
    if output == root or output in root.parents:
        raise ValueError("Output must not be the repository or one of its ancestors")
    if root in output.parents:
        rel = output.relative_to(root)
        if rel == Path(".scratch"):
            raise ValueError("Output must be a dedicated child inside .scratch")
        if rel.parts[0] != ".scratch" and rel != Path("dist-pages"):
            raise ValueError("In-repository output must be dist-pages or inside .scratch")
    sidecar = _manifest_path(output)
    if sidecar.is_symlink():
        raise ValueError("Output manifest must not be a symbolic link")
    if not output.exists():
        if sidecar.exists():
            raise ValueError("Output is absent but its manifest exists; inspect it first")
        return output
    if not output.is_dir() or not sidecar.is_file():
        raise ValueError("Existing output requires a matching Pages artifact manifest")
    try:
        manifest = json.loads(sidecar.read_text(encoding="utf-8"))
        entries = list(output.rglob("*"))
        if any(path.is_symlink() for path in entries):
            raise ValueError("Existing output contains a symbolic link")
        copied = sorted(path.relative_to(output).as_posix() for path in entries if path.is_file())
        matches = (
            manifest.get("root_files") == sorted(PUBLIC_ROOT_FILES)
            and manifest.get("asset_directories") == sorted(PUBLIC_ASSET_DIRS)
            and manifest.get("files") == len(copied)
            and manifest.get("sha256") == _digest_files(output, copied)
        )
    except (OSError, json.JSONDecodeError, AttributeError) as error:
        raise ValueError("Cannot verify existing output manifest") from error
    if not matches:
        raise ValueError("Existing output differs from its manifest; preserve and inspect it first")
    return output


def prepare(output: Path) -> dict:
    output = _validate_output(output)
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    copied = []
    for source in sorted(ROOT.rglob("*")):
        if source == output or output in source.parents or not is_public(source):
            continue
        if source.is_dir():
            continue
        target = output / source.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append(source.relative_to(ROOT).as_posix())

    manifest = {
        "files": len(copied),
        "sha256": _digest_files(output, copied),
        "root_files": sorted(PUBLIC_ROOT_FILES),
        "asset_directories": sorted(PUBLIC_ASSET_DIRS),
    }
    _manifest_path(output).write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        manifest = prepare(args.output)
    except ValueError as error:
        print(f"Cannot prepare Pages artifact: {error}", file=sys.stderr)
        return 1
    output = args.output.resolve()
    manifest_path = _manifest_path(output)
    print(f"✓ Prepared {manifest['files']} public files in {output}")
    print(f"  SHA-256: {manifest['sha256']}")
    print(f"  Manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
