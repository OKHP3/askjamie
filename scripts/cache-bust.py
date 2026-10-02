#!/usr/bin/env python3
"""Generate/check shared asset fingerprints and deferred app loading.

Adapted from scripts/archive/cache-bust.py. Run without arguments to update
canonical tracked pages and templates; --check never writes. After updates,
run generate-csp.py because the brand import map is an inline script.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
ASSETS = (
    "assets/css/theme.css", "assets/css/critical-hero.css", "assets/js/app.js", "assets/js/mermaid-init.js",
    "assets/js/universe-map.js", "assets/js/askjamie-analytics.js", "assets/js/deferred-fonts.js",
    "assets/js/analytics-loader.js",
)
MARKER = "<!-- AUTOGEN:SHARED-ASSETS -->"
MAP_START = "<!-- AUTOGEN:BRAND-IMPORT-MAP -->"
MAP_END = "<!-- /AUTOGEN:BRAND-IMPORT-MAP -->"
MAP_RE = re.compile(re.escape(MAP_START) + r".*?" + re.escape(MAP_END) + r"\s*", re.S)
TAG_RE = re.compile(r"<(?:link|script)\b[^>]*>", re.I)
URL_RE = re.compile(r'''\b(?:href|src)\s*=\s*(["'])(.*?)\1''', re.I)


def file_hash(path: Path) -> str:
    normalized = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(normalized).hexdigest()[:8]


def iter_html_files(root: Path):
    # Git is the source inventory; generated copies and ignored reports never
    # become authoring inputs. Templates are intentional authoring inputs.
    names = subprocess.check_output(
        ["git", "ls-files", "-z", "--", "*.html"], cwd=root
    ).decode("utf-8").split("\0")
    for name in sorted(filter(None, names)):
        parts = Path(name).parts
        if name.startswith("assets/templates/") or (
            parts[0] in {"about", "contact", "found-ry", "whats-next", "how-askjamie-works", "legal",
                         "lens-system", "search", "universe"}
            or len(parts) == 1
        ):
            yield root / name


def rewrite_one(source: str, hashes: dict[str, str]) -> str:
    # Remove only our generated markers/map before rebuilding. Preserve all
    # other source bytes, inline code, URL paths, query parameters and fragments.
    source = MAP_RE.sub("", source)
    source = source.replace(MARKER, "")
    brand_url = "/assets/js/askjamie-analytics.js"
    brand_map = json.dumps({"imports": {
        brand_url: f"{brand_url}?v={hashes[brand_url.lstrip('/')]}"
    }}, separators=(",", ":"))

    def replace_tag(match: re.Match) -> str:
        tag = match.group(0)
        url_match = URL_RE.search(tag)
        if not url_match:
            return tag
        url = urlsplit(url_match[2])
        key = url.path.lstrip("/")
        if url.scheme or url.netloc or key not in hashes:
            return tag
        query = [(k, v) for k, v in parse_qsl(url.query, keep_blank_values=True) if k != "v"]
        query.append(("v", hashes[key]))
        target = urlunsplit(("", "", url.path, urlencode(query), url.fragment))
        tag = tag[:url_match.start(2)] + target + tag[url_match.end(2):]
        prefix = MARKER
        if tag.lower().startswith("<script") and key == "assets/js/app.js":
            if re.search(r"\sasync(?:\s|=|>)", tag, re.I):
                raise ValueError("app.js must not use async: preserve ordered deferred execution")
            if not re.search(r"\sdefer(?:\s|=|>)", tag, re.I):
                tag = re.sub(r"<script\b", "<script defer", tag, count=1, flags=re.I)
            prefix = (MAP_START + '<script type="importmap">' + brand_map
                      + "</script>" + MAP_END + MARKER)
        return prefix + tag

    return TAG_RE.sub(replace_tag, source)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    missing_assets = [asset for asset in ASSETS if not (ROOT / asset).is_file()]
    if missing_assets:
        for asset in missing_assets:
            print(
                f"ERROR: Missing shared asset required by the release fingerprint check: {asset}",
                file=sys.stderr,
            )
        print(
            "  Affected release check: python3 scripts/cache-bust.py --check",
            file=sys.stderr,
        )
        return 1
    hashes = {asset: file_hash(ROOT / asset) for asset in ASSETS}
    changed = 0
    pages = list(iter_html_files(ROOT))
    for path in pages:
        # Keep checkout line endings; fingerprints alone normalize asset bytes.
        source = path.read_bytes().decode("utf-8")
        updated = rewrite_one(source, hashes)
        if updated != source:
            changed += 1
            print(f"{'STALE' if args.check else 'Updated'}: {path.relative_to(ROOT).as_posix()}")
            if not args.check:
                path.write_bytes(updated.encode("utf-8"))
    print(f"Shared asset check: {len(pages)} pages/templates, {changed} {'stale' if args.check else 'updated'}.")
    return int(args.check and changed > 0)


if __name__ == "__main__":
    raise SystemExit(main())
