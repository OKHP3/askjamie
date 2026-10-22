#!/usr/bin/env python3
"""
AskJamie™ — static site validation harness.

Checks every production HTML page for:
  - <title> present
  - meta description present
  - canonical link present
  - single <h1>
  - JSON-LD structured data present
  - inclusion in sitemap.xml (for non-noindex pages)
  - broken internal links (relative or /-rooted hrefs that resolve to no file)
  - broken asset references (CSS/JS/images)
  - external target="_blank" links missing rel="noopener" / "noreferrer"
  - placeholder hrefs ("#", "javascript:void(0)", empty href)
  - first meaningful use of flagged brand terms has a nearby plain-language
    definition
  - GA4 configuration in the page shell

Exits 0 if no errors. Exits 1 if any errors. Warnings do not fail the build.
Run from repo root:  python3 scripts/validate-site.py
"""

from __future__ import annotations

import os
import re
import sys
import importlib.util
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse, unquote

from csp import build_policies, page_class


_DRIFT_CHECKER_PATH = Path(__file__).with_name("check-critical-theme-drift.py")
_DRIFT_CHECKER_SPEC = importlib.util.spec_from_file_location(
    "critical_theme_drift", _DRIFT_CHECKER_PATH
)
if _DRIFT_CHECKER_SPEC is None or _DRIFT_CHECKER_SPEC.loader is None:
    raise ImportError(f"unable to load {_DRIFT_CHECKER_PATH}")
_DRIFT_CHECKER = importlib.util.module_from_spec(_DRIFT_CHECKER_SPEC)
_DRIFT_CHECKER_SPEC.loader.exec_module(_DRIFT_CHECKER)
check_contracts = _DRIFT_CHECKER.check_contracts

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".local", ".scratch", ".git", "node_modules", "attached_assets", "dist", "dist-pages", "templates", ".agents"}
SITEMAP = ROOT / "sitemap.xml"
MERMAID_VENDOR_ROOT = ROOT / "assets/vendor/mermaid"
MERMAID_VENDOR_ENTRY = MERMAID_VENDOR_ROOT / "mermaid.esm.min.mjs"
MERMAID_VERSION_FILE = MERMAID_VENDOR_ROOT / "VERSION"
SITE_ORIGIN = "https://askjamie.bot"
GA4_ID = "G-MT9Y10YY0G"

# These terms are useful internal labels, but should not be the first
# unexplained concept a visitor encounters in explanatory copy. Navigation,
# shared announcements, breadcrumb/eyebrow orientation labels, decorative
# diagrams, and link/button-only labels are intentionally excluded by
# MeaningfulBodyTextParser below.
PLAIN_LANGUAGE_TERMS = (
    (
        "BrandGuard",
        re.compile(r"\bBrandGuard\b", re.IGNORECASE),
        re.compile(
            r"brand[- ]voice|brand protection|protect(?:s|ing)?\s+"
            r"(?:tone|identity)|family of .*GPT|reusable GPT patterns",
            re.IGNORECASE,
        ),
    ),
    (
        "OKHP³",
        re.compile(r"\bOKHP[³3]\b", re.IGNORECASE),
        re.compile(
            r"R&D studio|engineering spine|experimental playground|"
            r"studio behind AskJamie|backbone",
            re.IGNORECASE,
        ),
    ),
    (
        "OverKill Hill P³",
        re.compile(r"\bOverKill Hill P[³3]\b", re.IGNORECASE),
        re.compile(
            r"R&D studio|engineering spine|experimental playground|"
            r"studio behind AskJamie|backbone",
            re.IGNORECASE,
        ),
    ),
    (
        "Lens System",
        re.compile(r"\bLens System\b", re.IGNORECASE),
        re.compile(
            r"focused ways? to examine|focused way of seeing|"
            r"structured way to ex|way of seeing and using AI|focused ways",
            re.IGNORECASE,
        ),
    ),
)


class TagCounter(HTMLParser):
    """Collect everything we need for one HTML page in a single pass."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: str | None = None
        self._in_title = False
        self._title_buf: list[str] = []
        self.h1_count = 0
        self.has_meta_description = False
        self.has_canonical = False
        self.has_jsonld = False
        self.is_noindex = False
        self.anchors: list[dict[str, str]] = []
        self.asset_refs: list[str] = []

    def handle_starttag(self, tag: str, attrs_list):
        attrs = {k: (v or "") for k, v in attrs_list}
        if tag == "title":
            self._in_title = True
        elif tag == "h1":
            self.h1_count += 1
        elif tag == "meta":
            name = attrs.get("name", "").lower()
            content = attrs.get("content", "")
            if name == "description" and content.strip():
                self.has_meta_description = True
            if name == "robots" and "noindex" in content.lower():
                self.is_noindex = True
        elif tag == "link":
            rel = attrs.get("rel", "").lower()
            href = attrs.get("href", "")
            if rel == "canonical" and href:
                self.has_canonical = True
            if rel in ("stylesheet", "icon", "apple-touch-icon", "manifest") and href:
                self.asset_refs.append(href)
        elif tag == "script":
            t = attrs.get("type", "").lower()
            s = attrs.get("src", "")
            if t == "application/ld+json":
                self.has_jsonld = True
            if s:
                self.asset_refs.append(s)
        elif tag == "img":
            s = attrs.get("src", "")
            if s:
                self.asset_refs.append(s)
        elif tag == "a":
            href = attrs.get("href", "")
            if href is not None:
                self.anchors.append({"href": href, "target": attrs.get("target", ""),
                                     "rel": attrs.get("rel", "")})

    def handle_endtag(self, tag: str):
        if tag == "title":
            self._in_title = False
            self.title = "".join(self._title_buf).strip()
            self._title_buf = []

    def handle_data(self, data: str):
        if self._in_title:
            self._title_buf.append(data)


class MeaningfulBodyTextParser(HTMLParser):
    """Collect explanatory text while excluding shared/decorative UI."""

    # Headings, breadcrumbs, labels, and list navigation name concepts; prose
    # blocks are where a first-time visitor needs the explanation.
    BLOCK_TAGS = {"p", "blockquote", "dt", "dd"}
    VOID_TAGS = {
        "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.excluded_depth = 0
        self.interactive_depth = 0
        self._element_stack: list[tuple[str, bool, bool]] = []
        self.blocks: list[list[str]] = []
        self._open_blocks: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs_list) -> None:
        attrs = {k: (v or "") for k, v in attrs_list}
        classes = set(attrs.get("class", "").split())
        excluded = (
            tag in {"head", "header", "nav", "footer", "script", "style"}
            or bool(classes.intersection({"site-specials", "askjamie-breadcrumb", "eyebrow"}))
            or attrs.get("aria-hidden", "").lower() == "true"
        )
        if excluded:
            self.excluded_depth += 1
        if tag in {"a", "button"}:
            self.interactive_depth += 1
        if tag not in self.VOID_TAGS:
            self._element_stack.append((tag, excluded, tag in {"a", "button"}))
        if (
            tag in self.BLOCK_TAGS
            and self.excluded_depth == 0
            and self.interactive_depth == 0
        ):
            block: list[str] = []
            self.blocks.append(block)
            self._open_blocks.append(block)

    def handle_startendtag(self, tag: str, attrs_list) -> None:
        # Void elements cannot contain meaningful prose.
        return

    def handle_endtag(self, tag: str) -> None:
        if tag in self.BLOCK_TAGS and self._open_blocks:
            self._open_blocks.pop()
        for index in range(len(self._element_stack) - 1, -1, -1):
            if self._element_stack[index][0] != tag:
                continue
            removed = self._element_stack[index:]
            del self._element_stack[index:]
            self.excluded_depth -= sum(item[1] for item in removed)
            self.interactive_depth -= sum(item[2] for item in removed)
            break

    def handle_data(self, data: str) -> None:
        if self.excluded_depth == 0 and self.interactive_depth == 0:
            for block in self._open_blocks:
                block.append(data)


def check_plain_language_terms(path: Path, raw: str) -> list[Finding]:
    """Require a nearby definition for each term's first meaningful use."""
    parser = MeaningfulBodyTextParser()
    try:
        parser.feed(raw)
    except Exception as exc:
        return [Finding("WARN", path.relative_to(ROOT).as_posix(),
                        f"plain-language term parser exception: {exc}")]

    text = " ".join(" ".join(block) for block in parser.blocks)
    text = " ".join(text.split())
    findings: list[Finding] = []
    rel = path.relative_to(ROOT).as_posix()
    for label, term_re, definition_re in PLAIN_LANGUAGE_TERMS:
        match = term_re.search(text)
        if not match:
            continue
        nearby = text[max(0, match.start() - 220): match.end() + 320]
        if not definition_re.search(nearby):
            findings.append(
                Finding(
                    "ERROR",
                    rel,
                    f"first meaningful use of {label} lacks a nearby plain-language definition",
                )
            )
    return findings


def find_html_files() -> list[Path]:
    files: list[Path] = []
    def raise_scan_error(error: OSError) -> None:
        # Skipped workspace directories are pruned before traversal.
        # An unreadable public directory must fail validation.
        raise error

    for root, directories, filenames in os.walk(
        ROOT, topdown=True, onerror=raise_scan_error
    ):
        directories[:] = sorted(
            directory for directory in directories if directory not in SKIP_DIRS
        )
        for filename in sorted(filenames):
            if not filename.endswith(".html"):
                continue
            path = Path(root) / filename
            rel = path.relative_to(ROOT)
            if rel.as_posix().startswith("assets/templates/"):
                continue
            files.append(path)
    return sorted(files)


def load_sitemap_urls() -> set[str]:
    if not SITEMAP.exists():
        return set()
    text = SITEMAP.read_text(encoding="utf-8")
    return set(re.findall(r"<loc>([^<]+)</loc>", text))


def html_to_route(path: Path) -> str:
    rel = path.relative_to(ROOT).as_posix()
    if rel == "index.html":
        return "/"
    if rel.endswith("/index.html"):
        return "/" + rel[: -len("index.html")]
    return "/" + rel


def resolve_internal(href: str, source: Path) -> Path | None:
    parsed = urlparse(href)
    if parsed.scheme in ("http", "https", "mailto", "tel", "javascript"):
        return None
    if not parsed.path:
        return None
    p = unquote(parsed.path)
    if p.startswith("/"):
        target = ROOT / p.lstrip("/")
    else:
        target = source.parent / p
    return target


@lru_cache(maxsize=None)
def target_exists(target: Path) -> bool:
    if target.exists():
        if target.is_dir():
            return (target / "index.html").exists()
        return True
    if str(target).endswith("/") and (target / "index.html").exists():
        return True
    return False


class Finding:
    __slots__ = ("severity", "page", "msg")
    def __init__(self, severity: str, page: str, msg: str):
        self.severity = severity
        self.page = page
        self.msg = msg


def validate_page(path: Path, sitemap_urls: set[str]) -> list[Finding]:
    findings: list[Finding] = []
    rel = path.relative_to(ROOT).as_posix()
    raw = path.read_text(encoding="utf-8", errors="replace")

    # GA4 configuration must be present on every production page (loaded
    # unconditionally, same pattern as overkill-hill and glee-fully.tools).
    consent_marker = "Analytics loads only after visitor consent via assets/js/app.js."
    if GA4_ID not in raw and consent_marker not in raw:
        findings.append(Finding("WARN", rel, f"GA4 configuration ({GA4_ID}) or consent loader marker not found"))

    # placeholder hrefs
    for m in re.finditer(r'href="(#|javascript:[^"]*|)"', raw):
        href = m.group(1)
        if href in ("", "#"):
            findings.append(Finding("WARN", rel, f"placeholder href={href!r}"))
        elif href.startswith("javascript:"):
            findings.append(Finding("ERROR", rel, f"javascript: href found ({href!r})"))

    # parsed DOM checks
    parser = TagCounter()
    try:
        parser.feed(raw)
    except Exception as exc:
        findings.append(Finding("WARN", rel, f"HTML parser exception: {exc}"))
        return findings

    findings.extend(check_plain_language_terms(path, raw))

    if not parser.title:
        findings.append(Finding("ERROR", rel, "missing <title>"))
    if not parser.has_meta_description:
        findings.append(Finding("ERROR", rel, "missing meta description"))
    if not parser.has_canonical:
        findings.append(Finding("WARN", rel, "missing canonical link"))
    if parser.h1_count == 0:
        findings.append(Finding("ERROR", rel, "no <h1> found"))
    elif parser.h1_count > 1:
        findings.append(Finding("WARN", rel, f"{parser.h1_count} <h1> elements (should be 1)"))
    if not parser.has_jsonld:
        findings.append(Finding("WARN", rel, "no JSON-LD structured data"))

    canonical_url = SITE_ORIGIN + html_to_route(path)
    if not parser.is_noindex and rel not in ("404.html", "under-construction.html"):
        if sitemap_urls and canonical_url not in sitemap_urls:
            findings.append(Finding("ERROR", rel, f"missing from sitemap.xml ({canonical_url})"))

    if parser.is_noindex and canonical_url in sitemap_urls:
        findings.append(Finding("ERROR", rel, f"noindex page listed in sitemap.xml — remove it ({canonical_url})"))

    for ref in parser.asset_refs:
        target = resolve_internal(ref, path)
        if target is not None and not target_exists(target):
            findings.append(Finding("ERROR", rel, f"broken asset reference: {ref}"))

    for a in parser.anchors:
        href = a["href"]
        target = resolve_internal(href, path)
        if target is not None and not target_exists(target):
            if not href.startswith("#"):
                findings.append(Finding("ERROR", rel, f"broken internal link: {href}"))
        parsed = urlparse(href)
        if parsed.scheme in ("http", "https") and "askjamie.bot" not in parsed.netloc:
            if a["target"] == "_blank" and "noopener" not in a["rel"]:
                findings.append(Finding("ERROR", rel, f"external target=_blank without rel=noopener: {href}"))

    return findings



GOVERNANCE_DOCS = ["SUPPORT.md", "SECURITY.md", "CONTRIBUTING.md"]
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Captures the first non-blank line after a "## Maintainer" heading.
_MAINTAINER_RE = re.compile(r"##\s+Maintainer\s*\n+([^\n]+)", re.IGNORECASE)


def check_governance_docs_consistency() -> list[Finding]:
    """Ensure the contact email set and maintainer credit line are identical
    across SUPPORT.md, SECURITY.md, and CONTRIBUTING.md.

    Reports ERROR when:
    - No email is found in any governance doc
    - A doc’s email set differs from the consensus set (catches missing *and* extra addresses)
    - A doc’s maintainer credit line differs from the consensus value
    Reports WARN when:
    - A governance doc file is missing entirely
    - Some readable docs have a maintainer section but others do not
    """
    from collections import Counter

    findings: list[Finding] = []
    emails_by_doc: dict[str, set[str]] = {}
    maintainer_by_doc: dict[str, str] = {}

    for doc_name in GOVERNANCE_DOCS:
        doc_path = ROOT / doc_name
        if not doc_path.exists():
            findings.append(Finding(
                "WARN", doc_name,
                f"{doc_name} not found — cannot verify contact email consistency",
            ))
            continue
        raw = doc_path.read_text(encoding="utf-8", errors="replace")
        emails_by_doc[doc_name] = set(_EMAIL_RE.findall(raw))
        m = _MAINTAINER_RE.search(raw)
        if m:
            maintainer_by_doc[doc_name] = m.group(1).strip()

    if len(emails_by_doc) < 2:
        return findings  # not enough docs to compare

    # ── email consistency ──────────────────────────────────────────────────────────
    all_emails: set[str] = set().union(*emails_by_doc.values())

    if not all_emails:
        for doc_name in emails_by_doc:
            findings.append(Finding(
                "ERROR", doc_name,
                "no contact email found — add a consistent contact address to all governance docs",
            ))
    else:
        unique_email_sets = {frozenset(v) for v in emails_by_doc.values()}
        if len(unique_email_sets) > 1:
            set_counter: Counter = Counter(frozenset(v) for v in emails_by_doc.values())
            canonical_set: set[str] = set(set_counter.most_common(1)[0][0])
            for doc_name, emails in emails_by_doc.items():
                if emails != canonical_set:
                    expected = ", ".join(sorted(canonical_set))
                    found = ", ".join(sorted(emails)) if emails else "(none)"
                    findings.append(Finding(
                        "ERROR", doc_name,
                        f"contact email set mismatch: found [{found}], expected [{expected}] "
                        f"(from other governance docs)",
                    ))

    # ── maintainer credit line consistency ──────────────────────────────────────────────
    if len(maintainer_by_doc) >= 2:
        unique_credits = set(maintainer_by_doc.values())
        if len(unique_credits) > 1:
            credit_counter: Counter = Counter(maintainer_by_doc.values())
            canonical_credit = credit_counter.most_common(1)[0][0]
            for doc_name, credit in maintainer_by_doc.items():
                if credit != canonical_credit:
                    findings.append(Finding(
                        "ERROR", doc_name,
                        f"maintainer credit mismatch: found {credit!r}, "
                        f"expected {canonical_credit!r} (from other governance docs)",
                    ))

    # Warn if some readable docs have a maintainer section but others do not
    readable_docs = set(emails_by_doc)
    if maintainer_by_doc and len(maintainer_by_doc) < len(readable_docs):
        for doc_name in sorted(readable_docs - set(maintainer_by_doc)):
            findings.append(Finding(
                "WARN", doc_name,
                "no maintainer credit line found (## Maintainer section missing) — "
                "other governance docs have one",
            ))

    return findings
def validate_mermaid_version_pin(pages: list[Path]) -> list[Finding]:
    """Confirm the pinned VERSION file matches the vendored runtime bundle.

    This does not check npm for a newer release -- that is the job of the
    scheduled "mermaid-version-watch" GitHub Action, which needs network
    access this local validator does not assume. This check only confirms
    internal consistency: the version this repo claims to be running is the
    version actually sitting in assets/vendor/mermaid/, so a partial or
    forgotten re-vendor step cannot silently pass validation.
    """
    findings: list[Finding] = []
    rel_version_file = MERMAID_VERSION_FILE.relative_to(ROOT).as_posix()

    if not MERMAID_VERSION_FILE.is_file():
        findings.append(Finding(
            "ERROR", rel_version_file,
            "Mermaid VERSION pin file is missing; create it with the vendored "
            "release number",
        ))
        return findings

    pinned = MERMAID_VERSION_FILE.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", pinned):
        findings.append(Finding(
            "ERROR", rel_version_file,
            f"Mermaid VERSION pin {pinned!r} is not a plain semver string (X.Y.Z)",
        ))
        return findings

    if not MERMAID_VENDOR_ENTRY.is_file():
        findings.append(Finding(
            "ERROR", str(MERMAID_VENDOR_ENTRY.relative_to(ROOT)),
            "vendored Mermaid entry module is missing",
        ))
        return findings

    bundle_text = MERMAID_VENDOR_ENTRY.read_text(encoding="utf-8", errors="replace")
    if pinned not in bundle_text:
        findings.append(Finding(
            "ERROR", rel_version_file,
            f"Mermaid VERSION pin ({pinned}) was not found inside "
            f"{MERMAID_VENDOR_ENTRY.relative_to(ROOT).as_posix()}; the pin file "
            "and the vendored runtime have drifted out of sync",
        ))

    return findings


def _page_renders_mermaid(raw: str) -> bool:
    return bool(re.search(r"""class=["\'][^"\']*\bmermaid\b""", raw, re.IGNORECASE))


def validate_mermaid_csp_alignment(pages: list[Path]) -> list[Finding]:
    """Flag pages whose CSP class cannot legally style Mermaid's own output.

    Mermaid renders inline style="..." attributes and <style> blocks at
    runtime, per diagram, per page load. scripts/csp.py computes the
    style-src / style-src-attr allowlists by statically scanning HTML, so a
    hash-only allowlist can never cover output Mermaid only generates in the
    browser. This is a consistency safety net: csp.py's "diagram" /
    "embed-diagram" classes are designed to grant a scoped 'unsafe-inline'
    to every page that needs it, so this should always come back clean; it
    exists to catch a future page picking up a live diagram without being
    correctly classified.
    """
    findings: list[Finding] = []
    policies = build_policies()
    for path in pages:
        raw = path.read_text(encoding="utf-8", errors="replace")
        if not _page_renders_mermaid(raw):
            continue
        rel = path.relative_to(ROOT).as_posix()
        kind = page_class(path)
        policy = policies.get(kind, "")
        style_attr_directive = next(
            (part.strip() for part in policy.split(";")
             if part.strip().startswith("style-src-attr")),
            "",
        )
        if "unsafe-inline" not in style_attr_directive:
            findings.append(Finding(
                "WARN", rel,
                f"page renders live Mermaid diagrams under the {kind!r} CSP "
                "class, which only allow-lists static style hashes; Mermaid's "
                "runtime-generated inline styles will be blocked and diagrams "
                "will render without theme styling",
            ))
    return findings


def main() -> int:
    sitemap_urls = load_sitemap_urls()
    if not sitemap_urls:
        print("WARN: sitemap.xml not found or empty.")

    pages = find_html_files()
    print(f"Validating {len(pages)} HTML pages...\n")

    all_findings: list[Finding] = []
    for path in pages:
        all_findings.extend(validate_page(path, sitemap_urls))
    all_findings.extend(validate_mermaid_version_pin(pages))
    all_findings.extend(validate_mermaid_csp_alignment(pages))
    all_findings.extend(check_governance_docs_consistency())
    for finding in check_contracts():
        all_findings.append(Finding("ERROR", "assets/css/critical-hero.css", finding))

    errors   = [f for f in all_findings if f.severity == "ERROR"]
    warnings = [f for f in all_findings if f.severity == "WARN"]

    if errors:
        print(f"ERRORS ({len(errors)}):")
        for f in errors:
            print(f"  ✖ {f.page}: {f.msg}")
        print()
    if warnings:
        print(f"WARNINGS ({len(warnings)}):")
        for f in warnings:
            print(f"  ! {f.page}: {f.msg}")
        print()

    if not errors and not warnings:
        print("✓ all clean.")
    elif not errors:
        print(f"✓ no errors ({len(warnings)} warnings).")
    else:
        print(f"✖ {len(errors)} error(s), {len(warnings)} warning(s).")

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
