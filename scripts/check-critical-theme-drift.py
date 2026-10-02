#!/usr/bin/env python3
"""Check the first-viewport CSS contract against the shared AskJamie theme.

The critical sheet is intentionally smaller than theme.css and has a few
documented fallback/reservation differences. This check protects the shared
parts without requiring the two files to become a second copy of one another.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import NamedTuple


ROOT = Path(__file__).resolve().parents[1]
CRITICAL_CSS = Path("assets/css/critical-hero.css")
THEME_CSS = Path("assets/css/theme.css")


class Contract(NamedTuple):
    name: str
    critical_selector: str
    theme_selectors: tuple[str, ...]
    required_properties: tuple[str, ...] = ()
    equal_properties: tuple[str, ...] = ()


# This is the maintained coverage map. A new first-viewport critical selector
# must either be added here or explicitly documented as an intentional
# critical-only fallback in critical-hero.css.
CONTRACTS = (
    Contract(
        "header surface",
        ".askjamie-main .site-header",
        (".askjamie-main .site-header",),
        ("background", "border-bottom"),
        ("background", "border-bottom"),
    ),
    Contract(
        "header container",
        ".askjamie-main .site-header > .container",
        (".site-header .container",),
        ("display", "align-items", "justify-content"),
        ("display", "align-items", "justify-content"),
    ),
    Contract(
        "mobile header container sizing",
        ".askjamie-main .site-header .container",
        (".askjamie-main .site-header .container",),
        ("height", "min-height", "flex-wrap"),
        ("height", "min-height", "flex-wrap"),
    ),
    Contract(
        "skip link layout",
        ".askjamie-main .skip-link",
        (".skip-link",),
        ("position", "width", "height", "margin", "overflow", "clip", "white-space", "border"),
        ("position", "width", "height", "margin", "overflow", "clip", "white-space", "border"),
    ),
    Contract(
        "header logo",
        ".askjamie-main .logo img",
        (".logo img",),
        ("border-radius",),
        ("border-radius",),
    ),
    Contract(
        "navigation link",
        ".askjamie-main .primary-nav a",
        (".askjamie-main .primary-nav a",),
        ("color",),
        ("color",),
    ),
    Contract(
        "mobile navigation toggle",
        ".askjamie-main .nav-toggle",
        (".askjamie-main .nav-toggle",),
        ("padding", "border-radius", "border", "background"),
        ("padding", "border-radius", "border", "background"),
    ),
    Contract(
        "navigation bars",
        ".askjamie-main .nav-toggle .bar",
        (".askjamie-main .nav-toggle .bar",),
        ("background",),
        ("background",),
    ),
    Contract(
        "Jamie announcement",
        ".askjamie-main .site-specials--jamie",
        (".site-specials--jamie", ".site-specials"),
        ("background", "margin-block"),
        ("background", "margin-block"),
    ),
    Contract(
        "capability transition panel",
        ".askjamie-main .capability-transition",
        (".askjamie-main .capability-transition",),
        ("padding", "border", "border-inline-start", "border-radius"),
        ("padding", "border", "border-inline-start", "border-radius"),
    ),
    Contract(
        "capability transition eyebrow",
        ".askjamie-main .capability-transition .eyebrow",
        (".askjamie-main .eyebrow", "p"),
        ("margin", "font-size", "letter-spacing", "text-transform", "color"),
        ("margin", "font-size", "letter-spacing", "text-transform", "color"),
    ),
    Contract(
        "capability transition heading",
        ".askjamie-main .capability-transition h2",
        (".askjamie-main .capability-transition h2", "h2"),
        ("margin", "font-size"),
        ("margin", "font-size"),
    ),
    Contract(
        "capability transition paragraphs",
        ".askjamie-main .capability-transition p",
        ("p",),
        ("margin",),
        ("margin",),
    ),
    Contract(
        "capability transition final paragraph",
        ".askjamie-main .capability-transition p:last-of-type",
        (".askjamie-main .capability-transition p:last-of-type",),
        ("margin-bottom",),
        ("margin-bottom",),
    ),
    Contract(
        "hero stripe artwork",
        ".askjamie-main .askjamie-hero > .brand-stripes",
        (".brand-stripes",),
        ("position", "inset", "z-index", "pointer-events"),
    ),
    Contract(
        "hero paper reservation",
        ".askjamie-main .askjamie-paper",
        (".askjamie-paper",),
        ("inset",),
    ),
    Contract(
        "hero grid",
        ".askjamie-main .askjamie-hero-grid",
        (".askjamie-hero-grid",),
        ("display", "align-items"),
        ("display", "align-items", "gap"),
    ),
    Contract(
        "hero heading typography",
        ".askjamie-main .askjamie-hero-copy h1",
        (".askjamie-hero-copy h1",),
        ("font-family",),
        ("font-family",),
    ),
    Contract(
        "hero subtitle typography",
        ".askjamie-main .askjamie-hero-copy .hero-subtitle",
        (".askjamie-hero-copy .hero-subtitle",),
        ("color", "font-size", "margin-bottom"),
        ("color", "font-size", "margin-bottom"),
    ),
    Contract(
        "hero tagline typography",
        ".askjamie-main .askjamie-hero-copy .hero-tagline",
        (".askjamie-hero-copy .hero-tagline",),
        ("color", "margin-bottom"),
        ("color", "margin-bottom"),
    ),
    Contract(
        "breadcrumb label wrapping",
        ".askjamie-main .askjamie-breadcrumb .breadcrumb-label",
        (".askjamie-main .askjamie-breadcrumb .breadcrumb-label",),
        ("white-space",),
        ("white-space",),
    ),
    Contract(
        "BrandGuard mobile breadcrumb width reservation",
        ".askjamie-brandguard-page .askjamie-breadcrumb .breadcrumb-label",
        (".askjamie-brandguard-page .askjamie-breadcrumb .breadcrumb-label",),
        ("min-width",),
        ("min-width",),
    ),
    Contract(
        "BrandGuard transition summary fallback wrapping",
        ".askjamie-main.askjamie-brandguard-page .capability-transition > p:nth-of-type(2)",
        (".askjamie-main.askjamie-brandguard-page .capability-transition > p:nth-of-type(2)",),
        ("letter-spacing",),
        ("letter-spacing",),
    ),
    Contract(
        "BrandGuard mobile heading fallback wrapping",
        ".askjamie-main.askjamie-brandguard-page .askjamie-hero-copy h1",
        (".askjamie-main.askjamie-brandguard-page .askjamie-hero-copy h1",),
        ("letter-spacing",),
        ("letter-spacing",),
    ),
    Contract(
        "BrandGuard mobile subtitle fallback wrapping",
        ".askjamie-main.askjamie-brandguard-page .askjamie-hero-copy .hero-subtitle",
        (".askjamie-main.askjamie-brandguard-page .askjamie-hero-copy .hero-subtitle",),
        ("letter-spacing",),
        ("letter-spacing",),
    ),
    Contract(
        "BrandGuard mobile tagline fallback wrapping",
        ".askjamie-main.askjamie-brandguard-page .askjamie-hero-copy .hero-tagline",
        (".askjamie-main.askjamie-brandguard-page .askjamie-hero-copy .hero-tagline",),
        ("letter-spacing",),
        ("letter-spacing",),
    ),
    Contract(
        "primary action",
        ".askjamie-main .btn-primary",
        (".askjamie-main .btn-primary",),
        ("background", "color"),
        ("background", "color"),
    ),
    Contract(
        "quiet action",
        ".askjamie-main .btn-quiet",
        (".askjamie-main .btn-quiet",),
        ("border-color", "color"),
        ("border-color", "color"),
    ),
    Contract(
        "Universe visual alignment",
        ".askjamie-main .askjamie-hero--universe .askjamie-hero-visual",
        (".askjamie-hero--universe .askjamie-hero-visual",),
        ("align-items",),
        ("align-items",),
    ),
    Contract(
        "Mermaid shell",
        ".askjamie-main .askjamie-mermaid-shell",
        (".askjamie-mermaid-shell",),
        ("width", "margin"),
        ("width", "margin"),
    ),
    Contract(
        "Mermaid scroll reservation",
        ".askjamie-main .askjamie-hero--universe .mermaid-scroll-wrap",
        (".askjamie-hero--universe .mermaid-scroll-wrap",),
        ("min-height",),
        ("min-height",),
    ),
    Contract(
        "Mermaid loading reservation",
        ".askjamie-main .askjamie-mermaid-shell .mermaid:not([data-universe-ready=\"1\"])",
        (".askjamie-main .askjamie-mermaid-shell .mermaid:not([data-universe-ready=\"1\"])",),
        ("visibility", "font-size", "min-height"),
        ("visibility", "font-size", "min-height"),
    ),
    Contract(
        "hero reveal fallback",
        ".askjamie-main .askjamie-hero > .reveal-on-scroll",
        (".askjamie-hero > .reveal-on-scroll",),
        ("opacity", "transform"),
        ("opacity", "transform"),
    ),
)


def _strip_comments(source: str) -> str:
    return re.sub(r"/\*.*?\*/", "", source, flags=re.S)


def _matching_brace(source: str, opening: int) -> int:
    depth = 1
    index = opening + 1
    while index < len(source):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return len(source)


def _normalise_selector(selector: str) -> str:
    return re.sub(r"\s+", " ", selector.strip())


def _normalise_value(value: str) -> str:
    value = re.sub(r"\s+", " ", value.strip())
    return re.sub(r"\s*!important$", "", value, flags=re.I)


def _declarations(body: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in body.split(";"):
        if ":" not in item:
            continue
        prop, value = item.split(":", 1)
        prop = prop.strip().lower()
        if prop and not prop.startswith("@"):
            result[prop] = _normalise_value(value)
            if prop in {"margin", "padding"}:
                parts = result[prop].split()
                if len(parts) == 1:
                    parts *= 4
                elif len(parts) == 2:
                    parts = [parts[0], parts[1], parts[0], parts[1]]
                elif len(parts) == 3:
                    parts = [parts[0], parts[1], parts[2], parts[1]]
                elif len(parts) > 4:
                    parts = parts[:4]
                for side, value in zip(("top", "right", "bottom", "left"), parts):
                    result[f"{prop}-{side}"] = value
    return result


def parse_rules(source: str) -> dict[str, list[dict[str, str]]]:
    """Return declarations by selector, including rules nested in media blocks."""
    source = _strip_comments(source)
    rules: dict[str, list[dict[str, str]]] = {}

    def visit(start: int, end: int) -> None:
        index = start
        while index < end:
            opening = source.find("{", index, end)
            closing = source.find("}", index, end)
            if closing != -1 and (opening == -1 or closing < opening):
                return
            if opening == -1:
                return
            prelude = source[index:opening].strip()
            finish = _matching_brace(source, opening)
            body = source[opening + 1:finish]
            if prelude.startswith("@"):
                visit(opening + 1, finish)
            else:
                declaration_map = _declarations(body)
                for selector in prelude.split(","):
                    normalised = _normalise_selector(selector)
                    if normalised and declaration_map:
                        rules.setdefault(normalised, []).append(declaration_map)
            index = finish + 1

    visit(0, len(source))
    return rules


def _last_value(rules: dict[str, list[dict[str, str]]], selector: str, prop: str) -> str | None:
    for declarations in reversed(rules.get(_normalise_selector(selector), [])):
        if prop in declarations:
            return declarations[prop]
    return None


def check_contracts(root: Path = ROOT) -> list[str]:
    critical_path = root / CRITICAL_CSS
    theme_path = root / THEME_CSS
    if not critical_path.is_file():
        return [f"missing critical stylesheet: {CRITICAL_CSS}"]
    if not theme_path.is_file():
        return [f"missing shared stylesheet: {THEME_CSS}"]

    critical = parse_rules(critical_path.read_text(encoding="utf-8"))
    theme = parse_rules(theme_path.read_text(encoding="utf-8"))
    findings: list[str] = []

    for contract in CONTRACTS:
        critical_selector = _normalise_selector(contract.critical_selector)
        if critical_selector not in critical:
            findings.append(f"{contract.name}: critical selector missing: {contract.critical_selector}")
            continue

        theme_selectors = tuple(_normalise_selector(selector) for selector in contract.theme_selectors)
        if not any(selector in theme for selector in theme_selectors):
            findings.append(
                f"{contract.name}: shared selector missing: {' / '.join(contract.theme_selectors)}"
            )
            continue

        for prop in contract.required_properties:
            if _last_value(critical, critical_selector, prop) is None:
                findings.append(f"{contract.name}: critical property missing: {prop}")
            if not any(_last_value(theme, selector, prop) is not None for selector in theme_selectors):
                findings.append(f"{contract.name}: shared property missing: {prop}")

        for prop in contract.equal_properties:
            critical_value = _last_value(critical, critical_selector, prop)
            theme_value = next(
                (
                    _last_value(theme, selector, prop)
                    for selector in theme_selectors
                    if _last_value(theme, selector, prop) is not None
                ),
                None,
            )
            if critical_value != theme_value:
                findings.append(
                    f"{contract.name}: {prop} drifted "
                    f"(critical={critical_value!r}, shared={theme_value!r})"
                )

    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    findings = check_contracts()
    if findings:
        print("Critical/theme drift found:")
        for finding in findings:
            print(f"  - {finding}")
        return 1
    if not args.quiet:
        print(f"Critical/theme contract clean ({len(CONTRACTS)} covered selectors).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())