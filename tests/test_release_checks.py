from __future__ import annotations

import base64
from collections import Counter
import http.server
import importlib.util
import json
import mimetypes
import os
import re
import shlex
import signal
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_COMMAND_RE = re.compile(r"scripts/([A-Za-z0-9_.-]+)")
ARCHIVED_SCRIPT_RE = re.compile(r"scripts/archive/[^\s`'\"|;&]+")


def _responsive_qa_viewport_count():
    source = (ROOT / "scripts/responsive-qa.mjs").read_text(encoding="utf-8")
    match = re.search(r"const VIEWPORTS = \[(.*?)\n\];", source, re.DOTALL)
    assert match, "responsive QA viewport list is missing"
    return len(re.findall(r"\{\s*name:", match.group(1)))


RESPONSIVE_QA_VIEWPORT_COUNT = _responsive_qa_viewport_count()


def _ensure_sitemap_fixture_pages(root, *, allow_fixture_sitemap_roots=False):
    sitemap = (root / "sitemap.xml").read_text(encoding="utf-8")
    locations = re.findall(r"<loc>(.*?)</loc>", sitemap)
    for location in locations:
        route = urlsplit(location).path
        if route.endswith("/"):
            page = root / route.lstrip("/") / "index.html"
        elif route.lower().endswith(".html"):
            page = root / route.lstrip("/")
        else:
            continue
        if page.resolve().is_relative_to(root.resolve()) and not page.exists():
            page.parent.mkdir(parents=True, exist_ok=True)
            page.write_text("<!doctype html><html><body>Fixture</body></html>", encoding="utf-8")

    if allow_fixture_sitemap_roots:
        # These tests exercise browser/report behavior with synthetic routes.
        # Their copied runner may allow those fixture roots without weakening
        # the production allowlist or the dedicated publication-boundary tests.
        runner = root / "scripts/responsive-qa.mjs"
        if runner.is_file():
            source = runner.read_text(encoding="utf-8")
            fixture_roots = sorted({
                urlsplit(location).path.strip("/").split("/", 1)[0]
                for location in locations
                if urlsplit(location).path.strip("/")
            })
            additions = [
                f"PAGES_ARTIFACT_PAGE_DIRS.add({json.dumps(name)});"
                for name in fixture_roots
            ]
            additions = [line for line in additions if line not in source]
            if additions:
                anchor = "function isPagesArtifactPublicHtmlFile(pageFile) {"
                assert anchor in source
                source = source.replace(
                    anchor,
                    "\n".join(additions) + "\n\n" + anchor,
                    1,
                )
                runner.write_text(source, encoding="utf-8")


def _copy_responsive_qa_dependencies(destination):
    for dependency in (
        "universe-not-found.mjs",
        "page-map-sweep-performance.mjs",
        "responsive-qa-warning-trend.mjs",
        "responsive-qa-failure-summary.mjs",
    ):
        shutil.copy2(
            ROOT / "scripts" / dependency,
            destination.with_name(dependency),
        )
def _copy_responsive_qa_fixture(source, destination):
    shutil.copy2(source, destination)
    _copy_responsive_qa_dependencies(destination)


def _active_script_allowlist():
    readme = ROOT / "scripts/README.md"
    active_scripts = set()
    for line in readme.read_text(encoding="utf-8").splitlines():
        match = re.search(r"\| `([^`]+)` \| active \|", line)
        if match:
            active_scripts.add(match.group(1))
    return active_scripts


def _assert_release_commands_use_active_scripts(source, text, active_scripts):
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue

        archived_match = ARCHIVED_SCRIPT_RE.search(line)
        if archived_match:
            raise AssertionError(
                f"{source} command references archived script "
                f"{archived_match.group(0)}: {line.strip()}"
            )

        for script_name in SCRIPT_COMMAND_RE.findall(line):
            if script_name not in active_scripts:
                raise AssertionError(
                    f"{source} command references non-active script "
                    f"scripts/{script_name}: {line.strip()}"
                )


def test_release_commands_use_only_documented_active_scripts():
    active_scripts = _active_script_allowlist()
    release_sources = (
        ROOT / ".github/workflows/validate.yml",
        ROOT / "scripts/post-merge.sh",
    )

    for source in release_sources:
        _assert_release_commands_use_active_scripts(
            source.relative_to(ROOT),
            source.read_text(encoding="utf-8"),
            active_scripts,
        )


def test_post_merge_refreshes_shared_asset_fingerprints_before_validation():
    source = (ROOT / "scripts/post-merge.sh").read_text(encoding="utf-8")

    refresh = source.index("python3 scripts/cache-bust.py\n")
    csp = source.index("python3 scripts/generate-csp.py\n")
    audit = source.index("python3 scripts/audit-site.py --quiet")
    check = source.index("python3 scripts/cache-bust.py --check")

    assert refresh < csp < audit < check


def test_release_command_guard_names_archived_script_and_command():
    with pytest.raises(
        AssertionError,
        match=r"scripts/archive/old-release\.py.*python3 scripts/archive/old-release\.py",
    ):
        _assert_release_commands_use_active_scripts(
            Path("fixture-workflow.yml"),
            "run: python3 scripts/archive/old-release.py",
            _active_script_allowlist(),
        )


def test_desktop_reference_fingerprint_rejects_invalid_reference_data(tmp_path):
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("Node.js is required")

    root = tmp_path
    script_dir = root / "scripts"
    script_dir.mkdir()
    shutil.copy2(
        ROOT / "scripts/lighthouse-routes.mjs",
        script_dir / "lighthouse-routes.mjs",
    )
    reference_path = root / "assets/docs/performance-baseline-desktop-2026-09-07.json"
    reference_path.parent.mkdir(parents=True)

    valid_reference = json.loads(
        (ROOT / "assets/docs/performance-baseline-desktop-2026-09-07.json").read_text(
            encoding="utf-8",
        )
    )
    missing_performance = json.loads(json.dumps(valid_reference))
    del missing_performance["pages"]["homepage"]["performance"]
    missing_lcp = json.loads(json.dumps(valid_reference))
    del missing_lcp["pages"]["homepage"]["lcpMs"]
    excessive_performance = json.loads(json.dumps(valid_reference))
    excessive_performance["pages"]["homepage"]["performance"] = 101
    negative_performance = json.loads(json.dumps(valid_reference))
    negative_performance["pages"]["homepage"]["performance"] = -1
    negative_lcp = json.loads(json.dumps(valid_reference))
    negative_lcp["pages"]["homepage"]["lcpMs"] = -1
    multiple_invalid_measurements = json.loads(json.dumps(valid_reference))
    multiple_invalid_measurements["pages"]["homepage"]["performance"] = 101
    multiple_invalid_measurements["pages"]["brandguard"]["performance"] = -1
    multiple_invalid_measurements["pages"]["universe"]["lcpMs"] = -25
    reference_text = json.dumps(valid_reference)
    homepage_start = reference_text.index('"homepage": {')
    performance_property = (
        f'"performance": {valid_reference["pages"]["homepage"]["performance"]}'
    )
    performance_start = reference_text.index(
        performance_property,
        homepage_start,
    )
    performance_end = performance_start + len(performance_property)
    duplicate_key_reference = (
        reference_text[:performance_end]
        + f", {performance_property}"
        + reference_text[performance_end:]
    )
    escaped_duplicate_key_reference = (
        reference_text[:performance_end]
        + f', "perfor\\u006dance": {valid_reference["pages"]["homepage"]["performance"]}'
        + reference_text[performance_end:]
    )
    invalid_references = [
        ("{", "could not be parsed"),
        (
            duplicate_key_reference,
            'duplicate property "performance" at $["pages"]["homepage"]',
        ),
        (
            escaped_duplicate_key_reference,
            'duplicate property "performance" at $["pages"]["homepage"]',
        ),
        (json.dumps(missing_performance), "homepage"),
        (json.dumps(missing_lcp), "homepage"),
        (json.dumps(excessive_performance), "performance measurement for homepage: 101"),
        (json.dumps(negative_performance), "performance measurement for homepage: -1"),
        (json.dumps(negative_lcp), "LCP measurement for homepage: -1 ms"),
        (json.dumps(multiple_invalid_measurements), "invalid measurements"),
    ]

    for reference_text, expected_error in invalid_references:
        reference_path.write_text(reference_text, encoding="utf-8")
        result = subprocess.run(
            [
                node_bin,
                "scripts/lighthouse-routes.mjs",
                "--print-desktop-reference-fingerprint",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode != 0
        assert result.stdout == ""
        assert "Desktop Lighthouse reference" in result.stderr
        assert "assets/docs/performance-baseline-desktop-2026-09-07.json" in result.stderr
        assert expected_error in result.stderr
        assert "is missing. Restore the desktop reference file" not in result.stderr
        if reference_text == json.dumps(multiple_invalid_measurements):
            assert "performance measurement for homepage: 101" in result.stderr
            assert "performance measurement for brandguard: -1" in result.stderr
            assert "LCP measurement for universe: -25 ms" in result.stderr
            assert result.stderr.index("homepage: 101") < result.stderr.index("brandguard: -1")
            assert result.stderr.index("brandguard: -1") < result.stderr.index("universe: -25 ms")


def test_desktop_reference_fingerprint_accepts_inclusive_measurement_limits(tmp_path):
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("Node.js is required")

    root = tmp_path
    script_dir = root / "scripts"
    script_dir.mkdir()
    shutil.copy2(
        ROOT / "scripts/lighthouse-routes.mjs",
        script_dir / "lighthouse-routes.mjs",
    )
    reference_path = root / "assets/docs/performance-baseline-desktop-2026-09-07.json"
    reference_path.parent.mkdir(parents=True)

    reference = json.loads(
        (ROOT / "assets/docs/performance-baseline-desktop-2026-09-07.json").read_text(
            encoding="utf-8",
        )
    )
    original_pages = json.loads(json.dumps(reference["pages"]))
    reference["pages"]["homepage"]["performance"] = 0
    reference["pages"]["homepage"]["lcpMs"] = 0
    reference["pages"]["brandguard"]["performance"] = 100

    reference_path.write_text(json.dumps(reference), encoding="utf-8")
    result = subprocess.run(
        [
            node_bin,
            "scripts/lighthouse-routes.mjs",
            "--print-desktop-reference-fingerprint",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert re.fullmatch(r"sha256:[0-9a-f]{64}\n", result.stdout)
    assert result.stderr == ""
    assert reference["pages"]["homepage"]["performance"] == 0
    assert reference["pages"]["homepage"]["lcpMs"] == 0
    assert reference["pages"]["brandguard"]["performance"] == 100
    for route, measurements in original_pages.items():
        expected = dict(measurements)
        if route == "homepage":
            expected.update(performance=0, lcpMs=0)
        elif route == "brandguard":
            expected["performance"] = 100
        assert reference["pages"][route] == expected


def test_desktop_reference_fingerprint_fails_without_reference_file(tmp_path):
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("Node.js is required")

    root = tmp_path
    script_dir = root / "scripts"
    script_dir.mkdir()
    shutil.copy2(
        ROOT / "scripts/lighthouse-routes.mjs",
        script_dir / "lighthouse-routes.mjs",
    )
    reference_path = "assets/docs/performance-baseline-desktop-2026-09-07.json"

    result = subprocess.run(
        [
            node_bin,
            "scripts/lighthouse-routes.mjs",
            "--print-desktop-reference-fingerprint",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert result.stdout == ""
    assert "Desktop Lighthouse reference" in result.stderr
    assert reference_path in result.stderr
    assert "is missing" in result.stderr
    assert "Restore the desktop reference file" in result.stderr
    assert "could not be parsed" not in result.stderr


def test_desktop_reference_fingerprint_reports_read_failure_without_fingerprint(tmp_path):
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("Node.js is required")

    root = tmp_path
    script_dir = root / "scripts"
    script_dir.mkdir()
    shutil.copy2(
        ROOT / "scripts/lighthouse-routes.mjs",
        script_dir / "lighthouse-routes.mjs",
    )
    reference_path = root / "assets/docs/performance-baseline-desktop-2026-09-07.json"
    reference_path.mkdir(parents=True)

    result = subprocess.run(
        [
            node_bin,
            "scripts/lighthouse-routes.mjs",
            "--print-desktop-reference-fingerprint",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert result.stdout == ""
    assert "Desktop Lighthouse reference" in result.stderr
    assert "assets/docs/performance-baseline-desktop-2026-09-07.json" in result.stderr
    assert "could not be read" in result.stderr
    assert "could not be parsed" not in result.stderr
    assert "is missing" not in result.stderr


@pytest.mark.parametrize(
    "preset,reference_path,reference_state,expected_message",
    [
        (
            "desktop",
            "assets/docs/performance-baseline-desktop-2026-09-07.json",
            "missing",
            "is missing. Restore the reference file before running Lighthouse.",
        ),
        (
            "desktop",
            "assets/docs/performance-baseline-desktop-2026-09-07.json",
            "malformed",
            "is malformed and could not be parsed",
        ),
        (
            "mobile",
            "assets/docs/performance-baseline-mobile-2026-08-22.json",
            "missing",
            "is missing. Restore the reference file before running Lighthouse.",
        ),
        (
            "mobile",
            "assets/docs/performance-baseline-mobile-2026-08-22.json",
            "malformed",
            "is malformed and could not be parsed",
        ),
    ],
)
def test_lighthouse_run_reports_missing_or_malformed_reference_before_starting(
    tmp_path, preset, reference_path, reference_state, expected_message
):
    if os.name == "nt":
        pytest.skip("POSIX fake executables require shebang support unavailable on Windows")
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("Node.js is unavailable")

    root = tmp_path
    (root / "scripts").mkdir()
    shutil.copy2(ROOT / "scripts/lighthouse-routes.mjs", root / "scripts/lighthouse-routes.mjs")
    selected_reference = root / reference_path
    if reference_state == "malformed":
        selected_reference.parent.mkdir(parents=True)
        selected_reference.write_text("{ malformed reference", encoding="utf-8")

    lighthouse_bin = root / "node_modules/.bin/lighthouse"
    lighthouse_bin.parent.mkdir(parents=True)
    lighthouse_marker = root / "lighthouse-invoked"
    lighthouse_bin.write_text(
        f"#!/bin/sh\nprintf invoked > '{lighthouse_marker}'\n",
        encoding="utf-8",
    )
    lighthouse_bin.chmod(0o755)
    chrome_bin = root / "fake-chrome"
    chrome_marker = root / "chrome-invoked"
    chrome_bin.write_text(
        f"#!/bin/sh\nprintf invoked > '{chrome_marker}'\nprintf 'Chromium 148.0.7778.96\\n'\n",
        encoding="utf-8",
    )
    chrome_bin.chmod(0o755)

    run = subprocess.run(
        [
            node_bin,
            "scripts/lighthouse-routes.mjs",
            f"--preset={preset}",
            "--date=2099-03-01",
        ],
        cwd=root,
        env={**os.environ, "CHROME_PATH": str(chrome_bin)},
        capture_output=True,
        text=True,
    )

    assert run.returncode != 0
    assert run.stdout == ""
    assert f"{preset.title()} Lighthouse reference {reference_path}" in run.stderr
    assert expected_message in run.stderr
    if reference_state == "malformed":
        assert "Replace it with a valid JSON reference file" in run.stderr
    else:
        assert "could not be parsed" not in run.stderr
    assert not lighthouse_marker.exists()
    assert not chrome_marker.exists()
    assert not (
        root / "assets/audit" / f"lighthouse-2099-03-01{'-mobile' if preset == 'mobile' else ''}"
        / "summary.json"
    ).exists()


def test_desktop_reference_fingerprint_is_stable_outside_project_root():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("Node.js is required")

    fingerprint_option = "--print-desktop-reference-fingerprint"
    from_project_root = subprocess.run(
        [
            node_bin,
            "scripts/lighthouse-routes.mjs",
            fingerprint_option,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    from_parent_directory = subprocess.run(
        [
            node_bin,
            str(Path(ROOT.name) / "scripts/lighthouse-routes.mjs"),
            fingerprint_option,
        ],
        cwd=ROOT.parent,
        capture_output=True,
        text=True,
        check=True,
    )

    assert re.fullmatch(r"sha256:[0-9a-f]{64}\n", from_project_root.stdout)
    assert from_project_root.stderr == ""
    assert re.fullmatch(r"sha256:[0-9a-f]{64}\n", from_parent_directory.stdout)
    assert from_parent_directory.stderr == ""
    assert from_parent_directory.stdout == from_project_root.stdout


def test_desktop_reference_fingerprint_ignores_json_key_order(tmp_path):
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("Node.js is required")

    root = tmp_path
    script_dir = root / "scripts"
    script_dir.mkdir()
    shutil.copy2(
        ROOT / "scripts/lighthouse-routes.mjs",
        script_dir / "lighthouse-routes.mjs",
    )
    reference_path = root / "assets/docs/performance-baseline-desktop-2026-09-07.json"
    reference_path.parent.mkdir(parents=True)
    reference = json.loads(
        (ROOT / "assets/docs/performance-baseline-desktop-2026-09-07.json").read_text(
            encoding="utf-8",
        )
    )

    def reverse_object_keys(value):
        if isinstance(value, dict):
            return {
                key: reverse_object_keys(value[key])
                for key in reversed(list(value))
            }
        if isinstance(value, list):
            return [reverse_object_keys(item) for item in value]
        return value

    reordered_reference = reverse_object_keys(reference)
    reference_texts = [
        json.dumps(reference),
        json.dumps(reordered_reference),
    ]
    assert reference_texts[0] != reference_texts[1]

    fingerprints = []
    for reference_text in reference_texts:
        reference_path.write_text(reference_text, encoding="utf-8")
        result = subprocess.run(
            [
                node_bin,
                "scripts/lighthouse-routes.mjs",
                "--print-desktop-reference-fingerprint",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
        assert re.fullmatch(r"sha256:[0-9a-f]{64}\n", result.stdout)
        assert result.stderr == ""
        fingerprints.append(result.stdout)

    assert fingerprints[1] == fingerprints[0]


def test_lazy_image_browser_boundary_records_deferred_engine_expansion():
    policy = " ".join(
        (ROOT / "docs/technology-update-policy.md")
        .read_text(encoding="utf-8")
        .split()
    )
    workflow = (ROOT / ".github/workflows/validate.yml").read_text(encoding="utf-8")

    assert "The current intended support target for this check is Chromium only." in policy
    assert (
        "Firefox and WebKit checks are deferred until a separate reviewed "
        "compatibility decision explicitly names any additional target engines."
    ) in policy
    assert "## Browser target review — 2026-10-18" in policy
    assert (
        "Chromium remains the only target engine for the automated "
        "lazy-image compatibility check."
    ) in policy
    assert "the project does not publish a general supported-browser matrix" in policy
    assert "the recorded sessions were not run" in policy
    assert (
        "run this same fixture independently for each one, and retain one "
        "engine-and-version-specific evidence file per run"
    ) in policy
    assert "Do not reuse Chromium results as evidence for another engine." in policy
    assert "Verify lazy-image behavior on locked Playwright and Chromium" in workflow
    assert (
        "tests/test_release_checks.py::"
        "test_responsive_qa_browser_fixture_isolates_pages_and_preserves_failures"
    ) in workflow
    assert "Retain versioned lazy-image browser evidence" in workflow
    assert "LAZY_IMAGE_EVIDENCE_DIR" in workflow
    assert "npx playwright install --with-deps chromium" in workflow


def test_responsive_qa_browser_engines_match_reviewed_policy():
    policy_text = (ROOT / "docs/technology-update-policy.md").read_text(
        encoding="utf-8"
    )
    policy_match = re.search(
        r"^\*\*Reviewed automated QA browser engines:\*\*\s*(.*?)\s*$",
        policy_text,
        re.MULTILINE,
    )
    assert policy_match, "Declare the reviewed responsive-QA browser engines in policy"
    policy_engines = {
        engine.lower()
        for engine in re.findall(
            r"\b(?:Chromium|Firefox|WebKit)\b", policy_match.group(1), re.IGNORECASE
        )
    }

    # This reviewed expectation is intentionally independent from the policy.
    # A policy/engine change must update this assertion as part of its review.
    assert policy_engines == {"chromium"}, (
        "Update the explicitly reviewed browser-engine expectation in this test "
        "alongside the policy change."
    )

    workflow = (ROOT / ".github/workflows/validate.yml").read_text(
        encoding="utf-8"
    )
    install_commands = re.findall(
        r"(?m)^\s*npx\s+playwright\s+install\b([^\n]*)", workflow
    )
    assert install_commands, "CI must explicitly install the reviewed browser engines"
    installed_engines = set()
    for command in install_commands:
        tokens = shlex.split(command)
        # Playwright's install options are switches; positional arguments name
        # browser engines. This deliberately catches engines beyond today's set.
        installed_engines.update(token.lower() for token in tokens if not token.startswith("-"))
    assert installed_engines == policy_engines

    runner = (ROOT / "scripts/responsive-qa.mjs").read_text(encoding="utf-8")
    launched_engines = {
        engine.lower()
        for engine in re.findall(
            r"\b(?:pw|playwright)\.(\w+)\.launch\s*\(", runner
        )
    }
    assert launched_engines == policy_engines


def test_search_index_check_does_not_rewrite_timestamp():
    index = ROOT / "assets/data/search-index.json"
    before = json.loads(index.read_text(encoding="utf-8"))

    result = subprocess.run(
        [sys.executable, "scripts/build-search-index.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    after = json.loads(index.read_text(encoding="utf-8"))
    assert after == before


def test_search_index_rebuild_is_stable_when_content_is_unchanged():
    index = ROOT / "assets/data/search-index.json"
    before = index.read_text(encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "scripts/build-search-index.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert index.read_text(encoding="utf-8") == before


def test_site_audit_survives_disappearing_workspace_directory(tmp_path, monkeypatch):
    audit_path = ROOT / "scripts/audit-site.py"
    spec = importlib.util.spec_from_file_location("audit_site", audit_path)
    assert spec and spec.loader
    audit_site = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit_site)

    (tmp_path / "index.html").write_text(
        "<html><body><h1>Public page</h1></body></html>",
        encoding="utf-8",
    )
    (tmp_path / "extra.html").write_text(
        "<html><body><h1>Another public page</h1></body></html>",
        encoding="utf-8",
    )
    disappearing_directory = tmp_path / ".local/secondary_skills/recipe-creator"
    disappearing_directory.mkdir(parents=True)
    (tmp_path / "assets/data").mkdir(parents=True)
    (tmp_path / "assets/data/search-index.json").write_text(
        '{"pages": [{"url": "https://askjamie.bot/"}]}',
        encoding="utf-8",
    )
    (tmp_path / "sitemap.xml").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url><loc>https://askjamie.bot/</loc></url>
        </urlset>
        """,
        encoding="utf-8",
    )

    real_scandir = os.scandir

    def flaky_scandir(path):
        if Path(path) == disappearing_directory:
            raise FileNotFoundError(
                2, "No such file or directory", str(disappearing_directory)
            )
        return real_scandir(path)

    monkeypatch.setattr(audit_site, "ROOT", tmp_path)
    monkeypatch.setattr(audit_site.os, "scandir", flaky_scandir)
    monkeypatch.setattr(sys, "argv", ["audit-site.py", "--quiet"])

    assert audit_site.main() == 1
    report = (tmp_path / "assets/docs/audit-report.md").read_text(
        encoding="utf-8"
    )
    assert "**Pages scanned:** 2" in report
    assert "Missing <title>" in report
    assert "Pages on disk not listed in sitemap.xml" in report
    assert "Page on disk not in search index: extra.html" in report


def test_site_audit_accepts_absolute_utf8_report_path(tmp_path, monkeypatch):
    audit_path = ROOT / "scripts/audit-site.py"
    spec = importlib.util.spec_from_file_location("audit_site_absolute", audit_path)
    assert spec and spec.loader
    audit_site = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit_site)

    (tmp_path / "index.html").write_text(
        "<html><body><h1>Résumé</h1></body></html>",
        encoding="utf-8",
    )
    (tmp_path / "assets/data").mkdir(parents=True)
    (tmp_path / "assets/data/search-index.json").write_text(
        '{"pages": [{"url": "https://askjamie.bot/", "title": "Résumé"}]}',
        encoding="utf-8",
    )
    (tmp_path / "sitemap.xml").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url><loc>https://askjamie.bot/</loc></url>
        </urlset>
        """,
        encoding="utf-8",
    )
    report_path = tmp_path / "reports" / "audit-report.md"

    monkeypatch.setattr(audit_site, "ROOT", tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        ["audit-site.py", "--quiet", "--report", str(report_path)],
    )

    assert audit_site.main() == 1
    assert report_path.read_text(encoding="utf-8").startswith("# AskJamie.bot")


def test_pages_artifact_excludes_repository_only_files(tmp_path):
    output = tmp_path / "dist-pages"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/prepare-pages-artifact.py",
            "--output",
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert (output / "index.html").exists()
    assert (output / "assets/js/app.js").exists()
    assert (output / "assets/css/theme.css").exists()
    assert (output / ".well-known/security.txt").read_bytes() == (
        ROOT / ".well-known/security.txt"
    ).read_bytes()
    assert not (output / "assets/fonts").exists()
    assert not (output / ".github").exists()
    assert not (output / "scripts").exists()
    assert not (output / "replit.md").exists()
    assert not (output / ".pytest_cache").exists()
    assert not (output / ".pages-manifest.json").exists()


def _run_post_merge_with_fake_tools(tmp_path, *, reuse_server, fail_browser=False, fail_gate=""):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    server_pid_file = tmp_path / "server.pid"
    curl_count_file = tmp_path / "curl.count"

    (bin_dir / "curl").write_text(
        """#!/bin/sh
if [ "${REUSE_SERVER:-0}" = "1" ]; then exit 0; fi
count=0
if [ -f "$CURL_COUNT_FILE" ]; then count=$(cat "$CURL_COUNT_FILE"); fi
count=$((count + 1))
printf '%s' "$count" > "$CURL_COUNT_FILE"
[ "$count" -gt 1 ] || exit 1
if [ "$count" -eq 2 ]; then
  attempt=0
  while [ "$attempt" -lt 100 ]; do
    if [ -f "$SERVER_PID_FILE" ]; then exit 0; fi
    sleep 0.01
    attempt=$((attempt + 1))
  done
  exit 1
fi
[ -f "$SERVER_PID_FILE" ]
""",
        encoding="utf-8",
    )
    (bin_dir / "python3").write_text(
        """#!/bin/sh
if [ "$1" = "-m" ] && [ "$2" = "http.server" ]; then
  printf '%s' "$$" > "$SERVER_PID_FILE"
  exec sleep 60
fi
if [ -n "${FAIL_GATE:-}" ] && [ "$*" = "$FAIL_GATE" ]; then
  exit 7
fi
exit 0
""",
        encoding="utf-8",
    )
    (bin_dir / "node").write_text(
        """#!/bin/sh
case "$*" in
  *test_js_smoke.spec.mjs*)
    [ "${FAIL_BROWSER:-0}" = "1" ] && exit 9
    ;;
esac
exit 0
""",
        encoding="utf-8",
    )
    for tool in ("curl", "python3", "node"):
        (bin_dir / tool).chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "BROWSER_BASE_URL": "http://127.0.0.1:5000",
        "SERVER_PID_FILE": str(server_pid_file),
        "CURL_COUNT_FILE": str(curl_count_file),
        "REUSE_SERVER": "1" if reuse_server else "0",
        "FAIL_BROWSER": "1" if fail_browser else "0",
        "FAIL_GATE": fail_gate,
    }
    result = subprocess.run(
        ["bash", "scripts/post-merge.sh"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    return result, server_pid_file


def test_post_merge_reuses_existing_server_without_starting_another(tmp_path):
    result, server_pid_file = _run_post_merge_with_fake_tools(
        tmp_path, reuse_server=True
    )

    assert result.returncode == 0, result.stderr
    assert "reusing browser server" in result.stdout
    assert "starting temporary browser server" not in result.stdout
    assert not server_pid_file.exists()


@pytest.mark.parametrize("gate", ["scripts/cache-bust.py --check", "-m pytest"])
def test_post_merge_stops_before_browser_checks_when_source_gate_fails(tmp_path, gate):
    result, server_pid_file = _run_post_merge_with_fake_tools(
        tmp_path, reuse_server=False, fail_gate=gate
    )
    assert result.returncode == 7, result.stderr
    assert "all checks passed" not in result.stdout
    assert "starting temporary browser server" not in result.stdout
    assert not server_pid_file.exists()


def test_post_merge_cleans_up_temporary_server_after_success(tmp_path):
    result, server_pid_file = _run_post_merge_with_fake_tools(
        tmp_path, reuse_server=False
    )

    assert result.returncode == 0, result.stderr
    assert "starting temporary browser server" in result.stdout
    assert server_pid_file.exists()
    pid = int(server_pid_file.read_text(encoding="utf-8"))
    assert subprocess.run(["kill", "-0", str(pid)]).returncode != 0


def test_post_merge_cleans_up_temporary_server_after_browser_failure(tmp_path):
    result, server_pid_file = _run_post_merge_with_fake_tools(
        tmp_path, reuse_server=False, fail_browser=True
    )

    assert result.returncode != 0
    assert server_pid_file.exists()
    pid = int(server_pid_file.read_text(encoding="utf-8"))
    assert subprocess.run(["kill", "-0", str(pid)]).returncode != 0


def test_source_checks_ignore_generated_pages(tmp_path, monkeypatch):
    # A generated artifact may exist before validation or index generation.
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        for filename, root_name, collector in (
            ("validate-site.py", "ROOT", "find_html_files"),
            ("audit-site.py", "ROOT", "iter_public_files"),
            ("build-search-index.py", "REPO_ROOT", "collect_pages"),
        ):
            spec = importlib.util.spec_from_file_location(filename, ROOT / "scripts" / filename)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            for prefix in ("dist-pages", ".scratch/generated"):
                (tmp_path / prefix).mkdir(parents=True, exist_ok=True)
                (tmp_path / prefix / "index.html").write_text("<title>Generated duplicate</title>")
            monkeypatch.setattr(module, root_name, tmp_path)
            assert list(getattr(module, collector)()) == []
    finally:
        sys.path.pop(0)


def test_csp_ignores_tracked_generated_pages(monkeypatch):
    spec = importlib.util.spec_from_file_location("csp_inventory", ROOT / "scripts/csp.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args, 0, stdout="index.html\ndist-pages/index.html\nassets/templates/example.html\n"))
    assert module.all_pages() == [ROOT / "index.html"]


def test_csp_allows_the_configured_google_analytics_pixel():
    spec = importlib.util.spec_from_file_location("csp_policies", ROOT / "scripts/csp.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    expected = "img-src 'self' data: https://www.googletagmanager.com;"
    assert all(expected in policy for policy in module.build_policies().values())
    assert expected in module.build_edge_policy()


def test_generate_csp_check_fails_when_a_page_is_missing_csp(tmp_path, monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("csp_missing_check", ROOT / "scripts/generate-csp.py")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)

    page = tmp_path / "index.html"
    page.write_text(
        '<html><head><title>Test</title></head><body><h1>Test</h1></body></html>',
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "all_pages", lambda: [page])
    policies = {"standard": "default-src 'self'"}
    monkeypatch.setattr(module, "build_policies", lambda: policies)
    monkeypatch.setattr(module, "page_class", lambda _page: "standard")
    policy_file = tmp_path / "csp-policies.json"
    policy_file.write_text(
        json.dumps({"schema": 1, "policies": policies}, indent=2) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "POLICY_FILE", policy_file)

    assert module.main(["--check"]) == 1
    captured = capsys.readouterr()
    assert "missing CSP meta tag" in captured.out


def test_responsive_qa_requires_browser_unless_static_is_explicit(tmp_path):
    fixture_root = tmp_path / "runner"
    script_dir = fixture_root / "scripts"
    script_dir.mkdir(parents=True)
    _copy_responsive_qa_fixture(
        ROOT / "scripts/responsive-qa.mjs",
        script_dir / "responsive-qa.mjs",
    )
    route_path = "/about/"
    page = fixture_root / "about/index.html"
    page.parent.mkdir(parents=True)
    page.write_text("<!doctype html><html><body><h1>About</h1></body></html>", encoding="utf-8")
    (fixture_root / "sitemap.xml").write_text(
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f'<url><loc>https://askjamie.bot{route_path}</loc></url></urlset>',
        encoding="utf-8",
    )

    preload = tmp_path / "preload.cjs"
    preload.write_text(
        """
const Module = require('module');
const originalRequire = Module.prototype.require;
Module.prototype.require = function (id) {
  if (id === 'playwright') {
    throw new Error('playwright unavailable');
  }
  return originalRequire.apply(this, arguments);
};
""".strip()
        + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            "node",
            "scripts/responsive-qa.mjs",
            "--base=http://127.0.0.1:0",
        ],
        cwd=fixture_root,
        text=True,
        capture_output=True,
        env={**os.environ, "NODE_OPTIONS": f"--require {preload}"},
    )

    assert result.returncode == 1
    assert "Required browser QA could not start" in result.stderr
    assert "static-lint mode" not in result.stdout


def test_responsive_qa_can_require_release_hero_routes(tmp_path):
    runner = tmp_path / "runner"
    scripts = runner / "scripts"
    scripts.mkdir(parents=True)
    _copy_responsive_qa_fixture(ROOT / "scripts/responsive-qa.mjs", scripts / "responsive-qa.mjs")
    (runner / "about").mkdir()
    (runner / "about/index.html").write_text(
        "<html><body><h1>About</h1></body></html>",
        encoding="utf-8",
    )
    (runner / "sitemap.xml").write_text(
        '<urlset><url><loc>https://askjamie.bot/about/</loc></url></urlset>',
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            "node",
            "scripts/responsive-qa.mjs",
            "--require-release-hero-routes",
        ],
        cwd=runner,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "Required release hero routes are missing from sitemap.xml" in result.stderr
    assert "/lens-system/okhp3-brandguard/" in result.stderr
    assert "/universe/" in result.stderr


def test_responsive_qa_reports_non_brandguard_sitemap_routes_without_local_pages(tmp_path):
    fixture_root = tmp_path / "runner"
    scripts = fixture_root / "scripts"
    scripts.mkdir(parents=True)
    _copy_responsive_qa_fixture(ROOT / "scripts/responsive-qa.mjs", scripts / "responsive-qa.mjs")
    for page_dir in (
        fixture_root / "about",
        fixture_root / "lens-system/okhp3-brandguard",
    ):
        page_dir.mkdir(parents=True)
        (page_dir / "index.html").write_text(
            "<html><body><h1>Published page</h1></body></html>",
            encoding="utf-8",
        )
    (fixture_root / "index.html").write_text(
        "<html><body><h1>Home</h1></body></html>",
        encoding="utf-8",
    )
    (fixture_root / "robots.txt").write_text("User-agent: *\n", encoding="utf-8")
    (fixture_root / "sitemap.xml").write_text(
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc>https://askjamie.bot/</loc></url>"
        "<url><loc>https://askjamie.bot/about/</loc></url>"
        "<url><loc>https://askjamie.bot/lens-system/okhp3-brandguard/</loc></url>"
        "<url><loc>https://askjamie.bot/missing-core/</loc></url>"
        "<url><loc>https://askjamie.bot/lens-system/missing-lens/</loc></url>"
        "</urlset>",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["node", "scripts/responsive-qa.mjs", "--static"],
        cwd=fixture_root,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "SKIP  /missing-core/ — file not found" in result.stdout
    assert "SKIP  /lens-system/missing-lens/ — file not found" in result.stdout
    assert "STATIC LINT INCOMPLETE: skipped sitemap routes were not checked." in result.stdout
    report = json.loads(
        (fixture_root / "assets/audit/responsive-qa/results.json").read_text(
            encoding="utf-8"
        )
    )
    assert report["coverage_complete"] is False
    assert report["skipped_routes"] == [
        {
            "url": "http://localhost:5000/missing-core/",
            "path": "/missing-core/",
            "reason": "local HTML file not found",
            "checked": False,
        },
        {
            "url": "http://localhost:5000/lens-system/missing-lens/",
            "path": "/lens-system/missing-lens/",
            "reason": "local HTML file not found",
            "checked": False,
        },
    ]
    (fixture_root / "sitemap.xml").write_text(
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc>https://askjamie.bot/</loc></url>"
        "<url><loc>https://askjamie.bot/robots.txt</loc></url>"
        "</urlset>",
        encoding="utf-8",
    )
    invalid_route_result = subprocess.run(
        ["node", "scripts/responsive-qa.mjs", "--static"],
        cwd=fixture_root,
        text=True,
        capture_output=True,
    )
    assert invalid_route_result.returncode == 1
    assert "Sitemap routes without a published local page" in invalid_route_result.stderr
    assert (
        "/robots.txt (not an HTML page route; use a published directory or .html page)"
        in invalid_route_result.stderr
    )


def test_responsive_qa_rejects_existing_html_outside_pages_artifact(tmp_path):
    fixture_root = tmp_path / "runner"
    scripts = fixture_root / "scripts"
    scripts.mkdir(parents=True)
    _copy_responsive_qa_fixture(
        ROOT / "scripts/responsive-qa.mjs",
        scripts / "responsive-qa.mjs",
    )
    for relative_path in ("index.html", "about/index.html", "scripts/unpublished.html"):
        page = fixture_root / relative_path
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(
            "<!doctype html><html><body><h1>Fixture</h1></body></html>",
            encoding="utf-8",
        )

    (fixture_root / "sitemap.xml").write_text(
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc>https://askjamie.bot/</loc></url>"
        "<url><loc>https://askjamie.bot/about/</loc></url>"
        "<url><loc>https://askjamie.bot/scripts/unpublished.html</loc></url>"
        "</urlset>",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["node", "scripts/responsive-qa.mjs", "--static"],
        cwd=fixture_root,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "Sitemap routes without a published local page" in result.stderr
    assert (
        "/scripts/unpublished.html (local HTML file is not included in the "
        "published Pages artifact)"
    ) in result.stderr
    assert "/ (local HTML file is not included" not in result.stderr
    assert "/about/ (local HTML file is not included" not in result.stderr


def test_responsive_qa_reports_published_non_brandguard_pages_missing_from_sitemap(tmp_path):
    fixture_root = tmp_path / "runner"
    scripts = fixture_root / "scripts"
    scripts.mkdir(parents=True)
    _copy_responsive_qa_fixture(ROOT / "scripts/responsive-qa.mjs", scripts / "responsive-qa.mjs")

    for relative_path in (
        "index.html",
        "about/index.html",
        "about/café/index.html",
        "lens-system/okhp3-brandguard/index.html",
        "lens-system/okhp3-brandguard/listed-case-study/index.html",
        "lens-system/okhp3-brandguard/listed-case-study/unlisted-appendix.html",
        "extra-page/index.html",
        "print-version.html",
        "legal/index.html",
        "404.html",
        "under-construction.html",
        "assets/templates/draft.html",
        ".scratch/private/index.html",
        "dist-pages/generated/index.html",
    ):
        page = fixture_root / relative_path
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text("<!doctype html><html><body>Fixture</body></html>", encoding="utf-8")

    (fixture_root / "sitemap.xml").write_text(
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc>https://askjamie.bot/</loc></url>"
        "<url><loc>https://askjamie.bot/about/</loc></url>"
        "<url><loc>https://askjamie.bot/about/caf%C3%A9/</loc></url>"
        "<url><loc>https://askjamie.bot/lens-system/okhp3-brandguard/</loc></url>"
        "<url><loc>https://askjamie.bot/lens-system/okhp3-brandguard/listed-case-study/</loc></url>"
        "</urlset>",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["node", "scripts/responsive-qa.mjs", "--static"],
        cwd=fixture_root,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "Published non-BrandGuard HTML pages missing from sitemap.xml" in result.stderr
    assert "legal/index.html (/legal/)" in result.stderr
    assert "extra-page/index.html" not in result.stderr
    assert "print-version.html" not in result.stderr
    assert "café/index.html" not in result.stderr
    assert "404.html" not in result.stderr
    assert "under-construction.html" not in result.stderr
    assert "assets/templates/draft.html" not in result.stderr
    assert ".scratch/private/index.html" not in result.stderr
    assert "dist-pages/generated/index.html" not in result.stderr
    assert "unlisted-appendix.html" not in result.stderr


@pytest.mark.parametrize("failing", [False, True], ids=["clean", "failing"])
def test_responsive_qa_static_output_omits_browser_versions_and_matches_saved_totals(tmp_path, failing):
    # Keep the current project's saved report out of this reporting fixture.
    original_report = ROOT / "assets/audit/responsive-qa/results.json"
    original_bytes = original_report.read_bytes() if original_report.exists() else None
    fixture_root = tmp_path / "runner"
    (fixture_root / "scripts").mkdir(parents=True)
    (fixture_root / "about").mkdir()
    (fixture_root / "contact").mkdir()
    _copy_responsive_qa_fixture(ROOT / "scripts/responsive-qa.mjs", fixture_root / "scripts/responsive-qa.mjs")
    expected_public_page_urls = {
        "http://localhost:5000/about/",
        "http://localhost:5000/contact/",
    }
    missing_sitemap_path = "/missing-static-page/"
    html = (ROOT / "about/index.html").read_text(encoding="utf-8")
    if failing:
        html = html.replace('class="skip-link"', 'class="fixture-missing-skip-link"', 1)
    (fixture_root / "about/index.html").write_text(html, encoding="utf-8")
    (fixture_root / "contact/index.html").write_text(html, encoding="utf-8")
    (fixture_root / "sitemap.xml").write_text(
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc>https://askjamie.bot/about/</loc></url>"
        "<url><loc>https://askjamie.bot/contact/</loc></url>"
        f"<url><loc>https://askjamie.bot{missing_sitemap_path}</loc></url></urlset>",
        encoding="utf-8",
    )
    results_dir = fixture_root / "assets/audit/responsive-qa"
    results_dir.mkdir(parents=True)
    previous_browser_report = {
        "mode": "playwright",
        "status": "complete",
        "generated": "2025-06-01T00:00:00.000Z",
        "results": [{"warnings": ["prior browser warning"]}],
    }
    (results_dir / "results.json").write_text(
        json.dumps(previous_browser_report),
        encoding="utf-8",
    )
    result = subprocess.run(
        ["node", "scripts/responsive-qa.mjs", "--static"],
        cwd=fixture_root,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1, result.stderr
    assert "Static-lint requested with --static." in result.stdout
    assert "Skipped: 1 sitemap route(s) — not checked because local HTML files were missing." in result.stdout
    assert "STATIC LINT INCOMPLETE: skipped sitemap routes were not checked." in result.stdout
    assert "ALL CHECKS PASS." not in result.stdout
    assert "Browser runtime:" not in result.stdout

    assert "Warnings by message:" not in result.stdout
    report = json.loads(
        (fixture_root / "assets/audit/responsive-qa/results.json").read_text(encoding="utf-8")
    )
    browser_version = re.compile(
        r"\b(?:Playwright \d+\.\d+\.\d+|Chromium \d+(?:\.\d+){2,})\b"
    )
    assert not browser_version.search(result.stdout)
    assert not browser_version.search(json.dumps(report))
    assert report["mode"] == "static-lint"
    assert report["coverage_complete"] is False
    assert report["skipped_routes"] == [
        {
            "url": f"http://localhost:5000{missing_sitemap_path}",
            "path": missing_sitemap_path,
            "reason": "local HTML file not found",
            "checked": False,
        }
    ]
    assert any("skipped_routes were not checked" in note for note in report["note"].split(". "))
    assert "playwright_version" not in report
    assert "chromium_version" not in report
    rows = report["results"]
    pages_in_rows = {row["url"] for row in rows}
    assert pages_in_rows == expected_public_page_urls, (
        "saved static-lint results must contain exactly the fixture's public page URLs; "
        f"missing={expected_public_page_urls - pages_in_rows}, "
        f"unexpected={pages_in_rows - expected_public_page_urls}"
    )
    viewports_in_rows = {row["viewport"] for row in rows}
    expected_page_viewport_pairs = {
        (page, viewport)
        for page in pages_in_rows
        for viewport in viewports_in_rows
    }
    page_viewport_pair_counts = Counter(
        (row["url"], row["viewport"]) for row in rows
    )
    assert page_viewport_pair_counts == Counter(
        {pair: 1 for pair in expected_page_viewport_pairs}
    ), (
        "saved static-lint results must contain each page/viewport pair exactly once; "
        f"missing={expected_page_viewport_pairs - page_viewport_pair_counts.keys()}, "
        f"duplicates={{{pair: count for pair, count in page_viewport_pair_counts.items() if count != 1}}}"
    )
    assert report["pages_checked"] == len(pages_in_rows)
    assert report["viewports_checked"] == len(viewports_in_rows)
    assert report["total_checks"] == len(rows)
    assert report["passing_checks"] == sum(row["pass"] is True for row in rows)
    assert report["failing_checks"] == sum(row["pass"] is False for row in rows)
    assert all(row["pass"] is (len(row["errors"]) == 0) for row in rows)
    assert any(row["errors"] for row in rows) is failing
    assert any(not row["errors"] for row in rows) is (not failing)
    assert (report["failing_checks"] > 0) is failing
    assert (original_report.read_bytes() if original_report.exists() else None) == original_bytes
    preserved_baseline = json.loads(
        (results_dir / "results.completed-browser-baseline.json").read_text(
            encoding="utf-8"
        )
    )
    assert preserved_baseline == {
        "mode": "playwright",
        "status": "complete",
        "generated": previous_browser_report["generated"],
        "warning_count": 1,
    }


@pytest.mark.parametrize(
    ("case", "needle", "replacement", "expected_error"),
    [
        ("clean", "", "", None),
        (
            "missing-viewport-meta",
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            "",
            "LINT: missing viewport meta",
        ),
        (
            "construction-overlay",
            '<main id="main">',
            '<div class="construction-overlay"></div><main id="main">',
            "LINT: construction-overlay present (blocking modal)",
        ),
        (
            "wrong-h1-count",
            "<h1>Fixture page</h1>",
            "",
            "LINT: 0 <h1> elements (expected 1)",
        ),
        (
            "image-missing-alt",
            "</main>",
            '<img src="/fixture.png" width="10"></main>',
            "LINT: 1 <img> missing alt",
        ),
        (
            "image-missing-width",
            "</main>",
            '<img src="/fixture.png" alt="Fixture image"></main>',
            "LINT: 1 <img> missing width (layout-shift risk)",
        ),
        (
            "footer-search-link-missing",
            '<a href="/search/">Search</a>',
            '<a href="/contact/">Contact</a>',
            "LINT: /search/ link missing from footer nav",
        ),
        (
            "copyright-fallback-missing",
            '<span id="current-year-askjamie">2026</span>',
            '<span id="current-year-askjamie">2025</span>',
            "LINT: year span missing 2026 static fallback",
        ),
        (
            "search-link-in-primary-nav",
            '<nav class="primary-nav"></nav>',
            '<nav class="primary-nav"><a href="/search/">Search</a></nav>',
            "LINT: /search/ found inside primary-nav (should be footer only)",
        ),
        (
            "skip-link-missing",
            '<a class="skip-link" href="#main">Skip to content</a>',
            "",
            "LINT: missing skip link",
        ),
        (
            "app-script-missing",
            '<script src="/assets/js/app.js"></script>',
            "",
            "LINT: app.js script tag missing",
        ),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_responsive_qa_static_lint_catches_each_structural_rule(
    tmp_path, case, needle, replacement, expected_error
):
    fixture_root = tmp_path / "runner"
    (fixture_root / "scripts").mkdir(parents=True)
    (fixture_root / "about").mkdir()
    runner = fixture_root / "scripts/responsive-qa.mjs"
    _copy_responsive_qa_fixture(ROOT / "scripts/responsive-qa.mjs", runner)

    clean_html = """<!doctype html>
<html lang="en">
<head><meta name="viewport" content="width=device-width, initial-scale=1"><title>Fixture page</title></head>
<body>
<a class="skip-link" href="#main">Skip to content</a>
<nav class="primary-nav"></nav>
<main id="main"><h1>Fixture page</h1></main>
<footer><a href="/search/">Search</a><span id="current-year-askjamie">2026</span></footer>
<script src="/assets/js/app.js"></script>
</body>
</html>"""
    if needle:
        assert clean_html.count(needle) == 1
        html = clean_html.replace(needle, replacement, 1)
    else:
        html = clean_html
    (fixture_root / "about/index.html").write_text(html, encoding="utf-8")
    (fixture_root / "sitemap.xml").write_text(
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc>https://askjamie.bot/about/</loc></url></urlset>",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["node", "scripts/responsive-qa.mjs", "--static"],
        cwd=fixture_root,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == (1 if expected_error else 0), result.stderr
    report = json.loads(
        (fixture_root / "assets/audit/responsive-qa/results.json").read_text(
            encoding="utf-8"
        )
    )
    assert report["mode"] == "static-lint"
    rows = report["results"]
    assert rows
    for row in rows:
        assert row["pass"] is (expected_error is None), case
        assert row["errors"] == ([expected_error] if expected_error else []), case


def test_responsive_qa_warning_trend_reports_first_unchanged_increased_and_decreased_totals():
    node_bin = os.environ.get("ASKJAMIE_NODE") or shutil.which("node")
    if not node_bin:
        pytest.skip("Node.js is unavailable")

    result = subprocess.run(
        [
            node_bin,
            "--input-type=module",
            "-e",
            """
import {
  formatBrowserWarningTrend,
  completedBrowserWarningBaseline,
} from './scripts/responsive-qa-warning-trend.mjs';
const cases = [
  formatBrowserWarningTrend(7, null),
  formatBrowserWarningTrend(7, { warning_count: 7 }),
  formatBrowserWarningTrend(9, { warning_count: 6 }),
  formatBrowserWarningTrend(3, { warning_count: 6 }),
];
const interrupted = completedBrowserWarningBaseline({
  mode: 'playwright', status: 'early_stop', results: [{ warnings: ['partial'] }],
});
const staticLint = completedBrowserWarningBaseline({
  mode: 'static-lint', status: 'complete', results: [{ warnings: ['lint'] }],
});
console.log(JSON.stringify({ cases, interrupted, staticLint }));
""",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "cases": [
            "Browser QA warning trend: current 7; no previous completed browser report.",
            "Browser QA warning trend: current 7; previous completed browser report: 7 warnings (unchanged).",
            "Browser QA warning trend: current 9; previous completed browser report: 6 warnings (increased by 3).",
            "Browser QA warning trend: current 3; previous completed browser report: 6 warnings (decreased by 3).",
        ],
        "interrupted": None,
        "staticLint": None,
    }


def test_responsive_qa_failure_summary_counts_report_errors_separately_from_warnings():
    node_bin = os.environ.get("ASKJAMIE_NODE") or shutil.which("node")
    if not node_bin:
        pytest.skip("Node.js is unavailable")

    result = subprocess.run(
        [
            node_bin,
            "--input-type=module",
            "-e",
            """
import { summarizeBrowserFailures } from './scripts/responsive-qa-failure-summary.mjs';
import { countBrowserWarnings } from './scripts/responsive-qa-warning-trend.mjs';
const report = {
  results: [
    { pass: false, errors: ['horizontal overflow', 'console error'], warnings: ['blocked request'] },
    { pass: false, errors: ['horizontal overflow'], warnings: ['blocked request', 'slow resource'] },
    { pass: false, errors: ['missing image'], warnings: [] },
    { pass: true, errors: [], warnings: ['blocked request'] },
  ],
};
const failures = summarizeBrowserFailures(report);
const failingRows = report.results.filter(row => row.pass === false);
const errorOccurrences = failingRows.reduce((sum, row) => sum + row.errors.length, 0);
console.log(JSON.stringify({
  failures,
  summarizedOccurrences: failures.reduce((sum, item) => sum + item.count, 0),
  errorOccurrences,
  failingRows: failingRows.length,
  warningCount: countBrowserWarnings(report),
}));
""",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "failures": [
            {"message": "horizontal overflow", "count": 2},
            {"message": "console error", "count": 1},
            {"message": "missing image", "count": 1},
        ],
        "summarizedOccurrences": 4,
        "errorOccurrences": 4,
        "failingRows": 3,
        "warningCount": 4,
    }


def test_responsive_qa_archives_interrupted_reports_and_bounds_retention(tmp_path):
    fixture_root = tmp_path / "runner"
    (fixture_root / "scripts").mkdir(parents=True)
    (fixture_root / "about").mkdir()
    _copy_responsive_qa_fixture(ROOT / "scripts/responsive-qa.mjs", fixture_root / "scripts/responsive-qa.mjs")
    shutil.copy2(ROOT / "about/index.html", fixture_root / "about/index.html")
    (fixture_root / "sitemap.xml").write_text(
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        '<url><loc>https://askjamie.bot/about/</loc></url></urlset>',
        encoding="utf-8",
    )

    results_dir = fixture_root / "assets/audit/responsive-qa"
    archive_dir = results_dir / "interrupted-runs"
    archive_dir.mkdir(parents=True)
    for year in range(2010, 2022):
        timestamp = f"{year}-01-01T00-00-00-000Z"
        (archive_dir / f"{timestamp}--early_stop.json").write_text(
            json.dumps({
                "status": "early_stop",
                "generated": f"{year}-01-01T00:00:00.000Z",
            }),
            encoding="utf-8",
        )

    interrupted = {
        "status": "early_stop",
        "generated": "2025-06-07T08:09:10.111Z",
        "failure_reason": "Received SIGTERM",
        "results": [{"viewport": "mobile-320", "pass": True}],
    }
    (results_dir / "results.json").write_text(
        json.dumps(interrupted),
        encoding="utf-8",
    )
    result = subprocess.run(
        ["node", "scripts/responsive-qa.mjs", "--static"],
        cwd=fixture_root,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "Archived previous interrupted report:" in result.stdout
    archived_files = sorted(archive_dir.glob("*.json"))
    assert len(archived_files) == 10
    assert not (archive_dir / "2010-01-01T00-00-00-000Z--early_stop.json").exists()
    retained = archive_dir / "2025-06-07T08-09-10-111Z--early_stop.json"
    assert retained.is_file()
    archived_report = json.loads(retained.read_text(encoding="utf-8"))
    assert archived_report["status"] == "early_stop"
    assert archived_report["generated"] == interrupted["generated"]
    assert archived_report["failure_reason"] == interrupted["failure_reason"]
    assert archived_report["results"] == interrupted["results"]

    latest_report = json.loads((results_dir / "results.json").read_text(encoding="utf-8"))
    assert latest_report["mode"] == "static-lint"
    assert not (results_dir / "results.completed-browser-baseline.json").exists()


@pytest.mark.parametrize(
    ("timestamp_case", "generated"),
    [
        pytest.param("valid", "2025-06-07T08:09:10.111Z", id="valid-generated-timestamp"),
        pytest.param("missing", None, id="missing-generated-timestamp"),
        pytest.param("invalid", "not-a-timestamp", id="invalid-generated-timestamp"),
    ],
)
def test_responsive_qa_retry_does_not_duplicate_unchanged_interrupted_report(
    tmp_path, timestamp_case, generated
):
    fixture_root = tmp_path / "runner"
    (fixture_root / "scripts").mkdir(parents=True)
    (fixture_root / "about").mkdir()
    _copy_responsive_qa_fixture(ROOT / "scripts/responsive-qa.mjs", fixture_root / "scripts/responsive-qa.mjs")
    shutil.copy2(ROOT / "about/index.html", fixture_root / "about/index.html")
    (fixture_root / "sitemap.xml").write_text(
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        '<url><loc>https://askjamie.bot/about/</loc></url></urlset>',
        encoding="utf-8",
    )

    results_dir = fixture_root / "assets/audit/responsive-qa"
    archive_dir = results_dir / "interrupted-runs"
    archive_dir.mkdir(parents=True)
    for year in range(2010, 2021):
        timestamp = f"{year}-01-01T00-00-00-000Z"
        (archive_dir / f"{timestamp}--early_stop.json").write_text(
            json.dumps({
                "status": "early_stop",
                "generated": f"{year}-01-01T00:00:00.000Z",
            }),
            encoding="utf-8",
        )

    interrupted = {
        "status": "early_stop",
        "failure_reason": "Received SIGTERM",
        "results": [{"viewport": "mobile-320", "pass": True}],
    }
    if timestamp_case != "missing":
        interrupted["generated"] = generated
    report_path = results_dir / "results.json"
    original_report_bytes = json.dumps(interrupted, indent=2).encode("utf-8")
    report_path.write_bytes(original_report_bytes)
    original_report_stat = report_path.stat()
    expected_report_bytes = original_report_bytes
    reordered_interrupted = {
        "results": [{"pass": True, "viewport": "mobile-320"}],
        "failure_reason": "Received SIGTERM",
        "status": "early_stop",
    }
    if timestamp_case != "missing":
        reordered_interrupted["generated"] = generated

    preload = tmp_path / "fail-browser-launch.cjs"
    preload.write_text(
        """
const Module = require('module');
const originalRequire = Module.prototype.require;
Module.prototype.require = function (id) {
  if (id === 'playwright') {
    return { chromium: { launch: async () => { throw new Error('fixture launch failure'); } } };
  }
  if (id === 'playwright/package.json') return { version: 'fixture' };
  return originalRequire.apply(this, arguments);
};
""".strip()
        + "\n",
        encoding="utf-8",
    )

    for attempt in range(12):
        if attempt == 1:
            expected_report_bytes = json.dumps(
                reordered_interrupted, indent=2
            ).encode("utf-8")
            assert expected_report_bytes != original_report_bytes
            report_path.write_bytes(expected_report_bytes)
            os.utime(
                report_path,
                ns=(
                    original_report_stat.st_atime_ns,
                    original_report_stat.st_mtime_ns,
                ),
            )

        result = subprocess.run(
            ["node", "scripts/responsive-qa.mjs"],
            cwd=fixture_root,
            text=True,
            capture_output=True,
            timeout=30,
            env={**os.environ, "NODE_OPTIONS": f"--require {preload}"},
        )

        assert result.returncode == 1
        assert "Required browser QA could not start: Chromium could not be launched" in result.stderr
        assert report_path.read_bytes() == expected_report_bytes
        archived_files = sorted(archive_dir.glob("*.json"))
        assert len(archived_files) <= 10, f"retention exceeded on retry {attempt + 1}"
        identical_archives = [
            path for path in archived_files
            if (
                (archived := json.loads(path.read_text(encoding="utf-8")))["status"]
                == interrupted["status"]
                and archived.get("failure_reason") == interrupted["failure_reason"]
                and archived.get("results") == interrupted["results"]
            )
        ]
        assert len(identical_archives) == 1, f"duplicate or missing archive on retry {attempt + 1}"
        archived_report = json.loads(identical_archives[0].read_text(encoding="utf-8"))
        if timestamp_case == "missing":
            assert archived_report["generated"]
        elif timestamp_case == "valid":
            assert archived_report["generated"] == interrupted["generated"]
        else:
            assert archived_report["generated"] != interrupted["generated"]


def test_responsive_qa_lists_retained_interrupted_reports_without_replacing_latest(tmp_path):
    fixture_root = tmp_path / "runner"
    (fixture_root / "scripts").mkdir(parents=True)
    (fixture_root / "about").mkdir()
    _copy_responsive_qa_fixture(ROOT / "scripts/responsive-qa.mjs", fixture_root / "scripts/responsive-qa.mjs")
    shutil.copy2(ROOT / "about/index.html", fixture_root / "about/index.html")
    (fixture_root / "sitemap.xml").write_text(
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        '<url><loc>https://askjamie.bot/about/</loc></url></urlset>',
        encoding="utf-8",
    )

    results_dir = fixture_root / "assets/audit/responsive-qa"
    archive_dir = results_dir / "interrupted-runs"
    archive_dir.mkdir(parents=True)
    archived_reports = [
        {
            "generated": "2025-06-07T08:09:10.111Z",
            "status": "early_stop",
            "failure_reason": "Received SIGTERM",
        },
        {
            "generated": "2025-06-08T09:10:11.222Z",
            "status": "abandoned",
            "failure_reason": None,
        },
    ]
    for report in archived_reports:
        timestamp = report["generated"].replace(":", "-").replace(".", "-")
        (archive_dir / f"{timestamp}--{report['status']}.json").write_text(
            json.dumps(report),
            encoding="utf-8",
        )

    latest = {"generated": "2025-06-09T10:11:12.333Z", "status": "passed"}
    latest_path = results_dir / "results.json"
    latest_path.write_text(json.dumps(latest), encoding="utf-8")

    result = subprocess.run(
        ["node", "scripts/responsive-qa.mjs", "--list-interrupted"],
        cwd=fixture_root,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "Latest report (current): assets/audit/responsive-qa/results.json" in result.stdout
    assert "Retained interrupted runs (newest first):" in result.stdout
    assert "2025-06-08T09:10:11.222Z | abandoned | (no failure reason recorded)" in result.stdout
    assert "2025-06-07T08:09:10.111Z | early_stop | Received SIGTERM" in result.stdout
    assert result.stdout.index("2025-06-08T09:10:11.222Z") < result.stdout.index(
        "2025-06-07T08:09:10.111Z"
    )
    assert "assets/audit/responsive-qa/interrupted-runs/" in result.stdout
    assert json.loads(latest_path.read_text(encoding="utf-8")) == latest
    assert "Static-lint requested" not in result.stdout


def test_responsive_qa_lists_readable_reports_when_an_archive_is_malformed(tmp_path):
    fixture_root = tmp_path / "runner"
    (fixture_root / "scripts").mkdir(parents=True)
    (fixture_root / "about").mkdir()
    _copy_responsive_qa_fixture(ROOT / "scripts/responsive-qa.mjs", fixture_root / "scripts/responsive-qa.mjs")
    shutil.copy2(ROOT / "about/index.html", fixture_root / "about/index.html")
    (fixture_root / "sitemap.xml").write_text(
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        '<url><loc>https://askjamie.bot/about/</loc></url></urlset>',
        encoding="utf-8",
    )

    archive_dir = fixture_root / "assets/audit/responsive-qa/interrupted-runs"
    archive_dir.mkdir(parents=True)
    readable_archive = archive_dir / "2025-06-07T08-09-10-111Z--early_stop.json"
    readable_archive.write_text(
        json.dumps({
            "generated": "2025-06-07T08:09:10.111Z",
            "status": "early_stop",
            "failure_reason": "Received SIGTERM",
        }),
        encoding="utf-8",
    )
    unreadable_archive = archive_dir / "2025-06-08T09-10-11-222Z--abandoned.json"
    unreadable_archive.write_text("{ malformed archive JSON", encoding="utf-8")

    result = subprocess.run(
        ["node", "scripts/responsive-qa.mjs", "--list-interrupted"],
        cwd=fixture_root,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == 1
    assert "2025-06-07T08:09:10.111Z | early_stop | Received SIGTERM" in result.stdout
    assert f"Unable to read interrupted report assets/audit/responsive-qa/interrupted-runs/{unreadable_archive.name}" in result.stderr


def test_responsive_qa_lists_available_interrupted_run_progress(tmp_path):
    fixture_root = tmp_path / "runner"
    (fixture_root / "scripts").mkdir(parents=True)
    (fixture_root / "about").mkdir()
    _copy_responsive_qa_fixture(ROOT / "scripts/responsive-qa.mjs", fixture_root / "scripts/responsive-qa.mjs")
    shutil.copy2(ROOT / "about/index.html", fixture_root / "about/index.html")
    (fixture_root / "sitemap.xml").write_text(
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        '<url><loc>https://askjamie.bot/about/</loc></url></urlset>',
        encoding="utf-8",
    )

    archive_dir = fixture_root / "assets/audit/responsive-qa/interrupted-runs"
    archive_dir.mkdir(parents=True)
    archived_reports = [
        {
            "generated": "2025-06-10T10:11:12.333Z",
            "status": "early_stop",
            "failure_reason": "Received SIGTERM",
            "pages_expected": 2,
            "viewports_checked": 3,
            "total_checks": 6,
            "passing_checks": 5,
            "failing_checks": 1,
        },
        {
            "generated": "2025-06-09T09:10:11.222Z",
            "status": "abandoned",
            "failure_reason": "Runner stopped",
            "pages_expected": 2,
            "viewports_checked": 3,
            "total_checks": 2,
            "passing_checks": 1,
        },
        {
            "generated": "2025-06-08T08:09:10.111Z",
            "status": "early_stop",
            "failure_reason": "Progress count unavailable",
            "pages_expected": 2,
            "viewports_checked": 3,
        },
        {
            "generated": "2025-06-07T07:08:09.000Z",
            "status": "early_stop",
            "failure_reason": "Legacy report",
        },
    ]
    for report in archived_reports:
        timestamp = report["generated"].replace(":", "-").replace(".", "-")
        (archive_dir / f"{timestamp}--{report['status']}.json").write_text(
            json.dumps(report),
            encoding="utf-8",
        )

    result = subprocess.run(
        ["node", "scripts/responsive-qa.mjs", "--list-interrupted"],
        cwd=fixture_root,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert (
        "2025-06-10T10:11:12.333Z | early_stop | Received SIGTERM | "
        "checks 6/6, 5 passed, 1 failed"
    ) in result.stdout
    assert (
        "2025-06-09T09:10:11.222Z | abandoned | Runner stopped | "
        "checks 2/6, 1 passed"
    ) in result.stdout
    assert (
        "2025-06-08T08:09:10.111Z | early_stop | Progress count unavailable | "
        "checks ?/6"
    ) in result.stdout
    missing_progress_line = next(
        line for line in result.stdout.splitlines()
        if "2025-06-07T07:08:09.000Z" in line
    )
    assert "Legacy report" in missing_progress_line
    assert "checks" not in missing_progress_line
    assert "passed" not in missing_progress_line
    assert "failed" not in missing_progress_line


def test_responsive_qa_distinguishes_navigation_failure_from_delayed_ready_warning(tmp_path):
    node_bin = os.environ.get("ASKJAMIE_NODE") or shutil.which("node")
    node_modules = os.environ.get("NODE_PATH")
    repo_modules = ROOT / "node_modules"
    if not node_modules and (repo_modules / "playwright").exists():
        node_modules = str(repo_modules)
    bundled_modules = Path(
        "/Users/okh/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules"
    )
    if not node_modules and (bundled_modules / "playwright").exists():
        node_modules = str(bundled_modules)
    if not node_bin or not node_modules or not (Path(node_modules) / "playwright").exists():
        pytest.skip("Playwright runtime is unavailable")

    navigation_stall_release = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, _format, *_args):
            return

        def do_GET(self):
            if self.path == "/navigation-stall/":
                navigation_stall_release.wait(timeout=10)
                return
            if self.path == "/server-error/":
                payload = (
                    b'<!doctype html><html><head><meta name="viewport" '
                    b'content="width=device-width"></head><body>'
                    b'<h1>Fixture server error</h1></body></html>'
                )
                self.send_response(503)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            if self.path == "/client-error/":
                payload = (
                    b'<!doctype html><html><head><meta name="viewport" '
                    b'content="width=device-width"></head><body>'
                    b'<h1>Fixture client error</h1></body></html>'
                )
                self.send_response(404)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            if self.path == "/slow-domcontentloaded.js":
                time.sleep(8)
                payload = b"window.fixtureLoaded = true;"
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript")
            else:
                payload = (
                    b'<!doctype html><html><head><meta name="viewport" '
                    b'content="width=device-width"></head><body><h1>Delayed fixture</h1>'
                    b'<script src="/slow-domcontentloaded.js"></script></body></html>'
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    fixture_root = tmp_path / "runner"
    (fixture_root / "scripts").mkdir(parents=True)
    runner_source = (ROOT / "scripts/responsive-qa.mjs").read_text(encoding="utf-8")
    navigation_timeout_setting = "const LOCAL_DOCUMENT_NAVIGATION_TIMEOUT_MS = 30000;"
    assert navigation_timeout_setting in runner_source
    runner_source = runner_source.replace(
        navigation_timeout_setting,
        "const LOCAL_DOCUMENT_NAVIGATION_TIMEOUT_MS = 3000;",
        1,
    )
    (fixture_root / "scripts/responsive-qa.mjs").write_text(
        runner_source,
        encoding="utf-8",
    )
    _copy_responsive_qa_dependencies(fixture_root / "scripts/responsive-qa.mjs")
    (fixture_root / "sitemap.xml").write_text(
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        '<url><loc>https://askjamie.bot/delayed-domcontentloaded/</loc></url>'
        '<url><loc>https://askjamie.bot/server-error/</loc></url>'
        '<url><loc>https://askjamie.bot/client-error/</loc></url>'
        '<url><loc>https://askjamie.bot/navigation-stall/</loc></url></urlset>',
        encoding="utf-8",
    )
    _ensure_sitemap_fixture_pages(fixture_root, allow_fixture_sitemap_roots=True)
    try:
        result = subprocess.run(
            [
                node_bin,
                "scripts/responsive-qa.mjs",
                f"--base=http://127.0.0.1:{server.server_address[1]}",
            ],
            cwd=fixture_root,
            text=True,
            capture_output=True,
                timeout=300,
            env={**os.environ, "NODE_PATH": node_modules},
        )
        report = json.loads(
            (fixture_root / "assets/audit/responsive-qa/results.json").read_text(
                encoding="utf-8"
            )
        )
    finally:
        navigation_stall_release.set()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)

    warning_text = "DOMContentLoaded not observed within 5s after local document commit"
    assert result.returncode == 1, result.stdout + result.stderr
    assert re.search(
        r"^  WARN  /delayed-domcontentloaded/ \([^)]+\)\n"
        r"         → " + re.escape(warning_text) + r"$",
        result.stdout,
        re.MULTILINE,
    ), result.stdout
    route_rows = [
        row for row in report["results"] if "/delayed-domcontentloaded/" in row["url"]
    ]
    assert len(route_rows) == RESPONSIVE_QA_VIEWPORT_COUNT
    assert all(row["pass"] for row in route_rows)
    assert all(any(warning_text in warning for warning in row["warnings"]) for row in route_rows)
    navigation_rows = [
        row for row in report["results"] if "/navigation-stall/" in row["url"]
    ]
    assert len(navigation_rows) == RESPONSIVE_QA_VIEWPORT_COUNT
    assert all(not row["pass"] for row in navigation_rows)
    assert all(
        any(
            error.startswith("navigation timeout: page.goto: Timeout")
            for error in row["errors"]
        )
        for row in navigation_rows
    )
    assert all(
        not any("DOMContentLoaded not observed" in warning for warning in row["warnings"])
        for row in navigation_rows
    )
    server_error_rows = [
        row for row in report["results"] if "/server-error/" in row["url"]
    ]
    assert len(server_error_rows) == RESPONSIVE_QA_VIEWPORT_COUNT
    assert all(not row["pass"] for row in server_error_rows)
    assert all(
        any("HTTP 503: [document]" in error for error in row["errors"])
        for row in server_error_rows
    )
    assert all(
        not any("navigation timeout:" in error for error in row["errors"])
        and not any("navigation failed:" in error for error in row["errors"])
        and not any("DOMContentLoaded not observed" in warning for warning in row["warnings"])
        for row in server_error_rows
    )
    client_error_rows = [
        row for row in report["results"] if "/client-error/" in row["url"]
    ]
    assert len(client_error_rows) == RESPONSIVE_QA_VIEWPORT_COUNT
    assert all(not row["pass"] for row in client_error_rows)
    assert all(
        any("HTTP 404: [document]" in error for error in row["errors"])
        for row in client_error_rows
    )
    assert all(
        not any("navigation timeout:" in error for error in row["errors"])
        and not any("navigation failed:" in error for error in row["errors"])
        and not any("DOMContentLoaded not observed" in warning for warning in row["warnings"])
        for row in client_error_rows
    )
    assert report["failing_checks"] == 3 * RESPONSIVE_QA_VIEWPORT_COUNT
    assert report["passing_checks"] == RESPONSIVE_QA_VIEWPORT_COUNT
    assert report["total_checks"] == 4 * RESPONSIVE_QA_VIEWPORT_COUNT
    assert report["status"] == "complete"
    assert re.search(
        r"^  FAIL  [^\n]* /navigation-stall/$",
        result.stdout,
        re.MULTILINE,
    ), result.stdout
    assert "navigation timeout: page.goto: Timeout" in result.stdout
    assert re.search(
        r"^  FAIL  [^\n]* /client-error/$",
        result.stdout,
        re.MULTILINE,
    ), result.stdout
    assert sum(
        sum("HTTP 404: [document]" in error for error in row["errors"])
        for row in client_error_rows
    ) == RESPONSIVE_QA_VIEWPORT_COUNT
    assert re.search(
        r"^  FAIL  [^\n]* /server-error/$",
        result.stdout,
        re.MULTILINE,
    ), result.stdout
    assert sum(
        sum("HTTP 503: [document]" in error for error in row["errors"])
        for row in server_error_rows
    ) == RESPONSIVE_QA_VIEWPORT_COUNT
    assert re.search(
        rf"^Total: {report['total_checks']} checks — "
        rf"{report['passing_checks']} passed, {report['failing_checks']} failures, "
        rf"{sum(len(row.get('warnings', [])) for row in report['results'])} warnings$",
        result.stdout,
        re.MULTILINE,
    ), result.stdout


def test_responsive_qa_handles_domcontentloaded_delays_inside_and_outside_warning_threshold(
    tmp_path,
):
    node_bin = os.environ.get("ASKJAMIE_NODE") or shutil.which("node")
    node_modules = os.environ.get("NODE_PATH")
    repo_modules = ROOT / "node_modules"
    if not node_modules and (repo_modules / "playwright").exists():
        node_modules = str(repo_modules)
    bundled_modules = Path(
        "/Users/okh/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules"
    )
    if not node_modules and (bundled_modules / "playwright").exists():
        node_modules = str(bundled_modules)
    if not node_bin or not node_modules or not (Path(node_modules) / "playwright").exists():
        pytest.skip("Playwright runtime is unavailable")

    delay_by_path = {
        "/inside-domcontentloaded.js": 0.5,
        "/outside-domcontentloaded.js": 8.0,
    }

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, _format, *_args):
            return

        def do_GET(self):
            delay = delay_by_path.get(self.path)
            if delay is not None:
                time.sleep(delay)
                payload = b"window.fixtureLoaded = true;"
                content_type = "application/javascript"
            else:
                script_name = self.path.strip("/").replace("/", "-")
                payload = (
                    '<!doctype html><html><head><meta name="viewport" '
                    'content="width=device-width"></head><body>'
                    '<h1>Ready fixture</h1>'
                    f'<script src="/{script_name}.js"></script>'
                    '</body></html>'
                ).encode("utf-8")
                content_type = "text/html"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            try:
                self.wfile.write(payload)
            except BrokenPipeError:
                return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    fixture_root = tmp_path / "runner"
    (fixture_root / "scripts").mkdir(parents=True)
    _copy_responsive_qa_fixture(ROOT / "scripts/responsive-qa.mjs", fixture_root / "scripts/responsive-qa.mjs")
    (fixture_root / "sitemap.xml").write_text(
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        '<url><loc>https://askjamie.bot/inside-domcontentloaded/</loc></url>'
        '<url><loc>https://askjamie.bot/outside-domcontentloaded/</loc></url>'
        '</urlset>',
        encoding="utf-8",
    )
    _ensure_sitemap_fixture_pages(fixture_root, allow_fixture_sitemap_roots=True)
    try:
        result = subprocess.run(
            [
                node_bin,
                "scripts/responsive-qa.mjs",
                f"--base=http://127.0.0.1:{server.server_address[1]}",
            ],
            cwd=fixture_root,
            text=True,
            capture_output=True,
            timeout=120,
            env={**os.environ, "NODE_PATH": node_modules},
        )
        report = json.loads(
            (fixture_root / "assets/audit/responsive-qa/results.json").read_text(
                encoding="utf-8"
            )
        )
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)

    warning_text = "DOMContentLoaded not observed within 5s after local document commit"
    assert result.returncode == 0, result.stdout + result.stderr
    assert report["failing_checks"] == 0

    route_rows = {
        route: [
            row for row in report["results"]
            if f"/{route}/" in row["url"]
        ]
        for route in ("inside-domcontentloaded", "outside-domcontentloaded")
    }
    assert all(
        len(rows) == RESPONSIVE_QA_VIEWPORT_COUNT for rows in route_rows.values()
    )
    assert all(row["pass"] for rows in route_rows.values() for row in rows)
    assert all(
        not any(warning_text in warning for warning in row["warnings"])
        for row in route_rows["inside-domcontentloaded"]
    )
    assert all(
        any(warning_text in warning for warning in row["warnings"])
        for row in route_rows["outside-domcontentloaded"]
    )
    assert "WARN  /inside-domcontentloaded/" not in result.stdout
    assert "WARN  /outside-domcontentloaded/" in result.stdout


def test_responsive_qa_reports_git_provenance_in_browser_and_static_modes(tmp_path):
    node_bin = os.environ.get("ASKJAMIE_NODE") or shutil.which("node")
    node_modules = os.environ.get("NODE_PATH")
    repo_modules = ROOT / "node_modules"
    if not node_modules and (repo_modules / "playwright").exists():
        node_modules = str(repo_modules)
    bundled_modules = Path(
        "/Users/okh/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules"
    )
    if not node_modules and (bundled_modules / "playwright").exists():
        node_modules = str(bundled_modules)
    if not node_bin or not node_modules or not (Path(node_modules) / "playwright").exists():
        pytest.skip("Playwright runtime is unavailable")
    git_bin = shutil.which("git")
    if not git_bin:
        pytest.skip("Git executable is unavailable")

    html_path = "about/index.html"
    html = (
        '<!doctype html><html><head><meta name="viewport" '
        'content="width=device-width"></head><body><a class="skip-link" href="#main">Skip</a>'
        '<nav class="primary-nav"><a href="/about/">About</a></nav>'
        '<main id="main"><h1>About</h1></main><footer><a href="/search/">Search</a></footer>'
        '<script src="/assets/js/app.js"></script></body></html>'
    )

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, _format, *_args):
            return

        def do_GET(self):
            if self.path.endswith("/assets/js/app.js"):
                payload = b"// fixture"
                content_type = "application/javascript"
            else:
                payload = (fixture_root / html_path).read_bytes()
                content_type = "text/html"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    fixture_root = tmp_path / "runner"
    (fixture_root / "scripts").mkdir(parents=True)
    (fixture_root / "about").mkdir()
    _copy_responsive_qa_fixture(ROOT / "scripts/responsive-qa.mjs", fixture_root / "scripts/responsive-qa.mjs")
    (fixture_root / html_path).write_text(html, encoding="utf-8")
    (fixture_root / "sitemap.xml").write_text(
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        '<url><loc>https://askjamie.bot/about/</loc></url></urlset>',
        encoding="utf-8",
    )
    (fixture_root / ".gitignore").write_text(
        "/assets/audit/responsive-qa/\n", encoding="utf-8"
    )

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    def run_report(static, path=None):
        command = [
            node_bin,
            "scripts/responsive-qa.mjs",
            f"--base=http://127.0.0.1:{server.server_address[1]}",
        ]
        if static:
            command.append("--static")
        env = {**os.environ, "NODE_PATH": node_modules}
        if path is not None:
            env["PATH"] = str(path)
        result = subprocess.run(
            command,
            cwd=fixture_root,
            text=True,
            capture_output=True,
            timeout=90,
            env=env,
        )
        report = json.loads(
            (fixture_root / "assets/audit/responsive-qa/results.json").read_text(
                encoding="utf-8"
            )
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return report

    try:
        browser_unavailable = run_report(static=False)
        static_unavailable = run_report(static=True)
        for report in (browser_unavailable, static_unavailable):
            assert report["source_revision"] is None
            assert report["source_tree_status"] == "unavailable"
            assert report["source_revision_error"]
            assert report["source_changed_paths_status"] == "unavailable"
            assert report["source_changed_paths_classification_status"] == "unavailable"
            assert "source_changed_paths_during_run" not in report

        no_git_path = tmp_path / "no-git-bin"
        no_git_path.mkdir()
        python_bin = shutil.which("python3")
        if not python_bin:
            pytest.skip("Python 3 is required to parse the sitemap fixture")
        (no_git_path / "python3").symlink_to(python_bin)
        for static in (False, True):
            report = run_report(static, path=no_git_path)
            assert report["source_revision"] is None
            assert report["source_tree_status"] == "unavailable"
            assert report["source_revision_error"] == "git executable unavailable"
            assert report["source_changed_paths_status"] == "unavailable"
            assert report["source_changed_paths_classification_status"] == "unavailable"
            assert "source_changed_paths_during_run" not in report

        failing_git_path = tmp_path / "failing-git-bin"
        failing_git_path.mkdir()
        failing_git = failing_git_path / "git"
        failing_git.write_text("#!/bin/sh\nexit 23\n", encoding="utf-8")
        failing_git.chmod(0o755)
        failing_git_env_path = os.pathsep.join(
            (str(failing_git_path), os.environ.get("PATH", ""))
        )
        for static in (False, True):
            report = run_report(static, path=failing_git_env_path)
            assert report["source_revision"] is None
            assert report["source_tree_status"] == "unavailable"
            assert report["source_revision_error"] == "git command failed (exit code 23)"
            assert report["source_tree_status_at_completion"] == "unavailable"
            assert report["source_revision_error_at_completion"] == (
                "git command failed (exit code 23)"
            )

        for invalid_name, revision_output in (
            ("empty", ""),
            ("malformed", "not-a-git-revision"),
        ):
            invalid_git_path = tmp_path / f"{invalid_name}-revision-git-bin"
            invalid_git_path.mkdir()
            invalid_git = invalid_git_path / "git"
            invalid_git.write_text(
                "#!/bin/sh\n"
                'if [ "$1" = "rev-parse" ]; then\n'
                f"  printf '%s' '{revision_output}'\n"
                "  exit 0\n"
                "fi\n"
                f"exec '{git_bin}' \"$@\"\n",
                encoding="utf-8",
            )
            invalid_git.chmod(0o755)
            invalid_git_env_path = os.pathsep.join(
                (str(invalid_git_path), os.environ.get("PATH", ""))
            )
            for static in (False, True):
                report = run_report(static, path=invalid_git_env_path)
                assert report["source_revision"] is None
                assert report["source_tree_status"] == "unavailable"
                assert report["source_revision_error"] == (
                    "git returned an empty or malformed revision"
                )
                assert report["source_revision_at_completion"] is None
                assert report["source_tree_status_at_completion"] == "unavailable"
                assert report["source_revision_error_at_completion"] == (
                    "git returned an empty or malformed revision"
                )
                assert report["source_changed_paths_status"] == "unavailable"
                assert report["mode"] == ("static-lint" if static else "playwright")

        timed_out_git_path = tmp_path / "timed-out-git-bin"
        timed_out_git_path.mkdir()
        timed_out_git = timed_out_git_path / "git"
        timed_out_git.write_text(
            "#!/usr/bin/env python3\nimport time\ntime.sleep(10)\n",
            encoding="utf-8",
        )
        timed_out_git.chmod(0o755)
        timed_out_git_env_path = os.pathsep.join(
            (str(timed_out_git_path), os.environ.get("PATH", ""))
        )
        for static in (False, True):
            report = run_report(static, path=timed_out_git_env_path)
            assert report["source_revision"] is None
            assert report["source_tree_status"] == "unavailable"
            assert report["source_revision_error"] == "git command timed out after 3000ms"
            assert report["source_tree_status_at_completion"] == "unavailable"
            assert report["source_revision_error_at_completion"] == (
                "git command timed out after 3000ms"
            )
            if static:
                assert report["mode"] == "static-lint"
            else:
                assert report["mode"] == "playwright"
                assert report["status"] == "complete"

        subprocess.run([git_bin, "init", "-q"], cwd=fixture_root, check=True)
        subprocess.run(
            [git_bin, "config", "user.name", "Responsive QA Test"],
            cwd=fixture_root,
            check=True,
        )
        subprocess.run(
            [git_bin, "config", "user.email", "responsive-qa-test@example.invalid"],
            cwd=fixture_root,
            check=True,
        )
        subprocess.run([git_bin, "add", "."], cwd=fixture_root, check=True)
        subprocess.run(
            [git_bin, "commit", "-q", "-m", "fixture baseline"],
            cwd=fixture_root,
            check=True,
        )
        revision = subprocess.check_output(
            [git_bin, "rev-parse", "HEAD"], cwd=fixture_root, text=True
        ).strip()

        late_timeout_path = tmp_path / "late-timeout-git-bin"
        late_timeout_path.mkdir()
        late_timeout_counter = tmp_path / "late-timeout-git-rev-parse-count"
        late_timeout_git = late_timeout_path / "git"
        late_timeout_git.write_text(
            "#!/bin/sh\n"
            f"counter={shlex.quote(str(late_timeout_counter))}\n"
            f"real_git={shlex.quote(git_bin)}\n"
            'if [ "$1" = "rev-parse" ]; then\n'
            '  count=$(cat "$counter" 2>/dev/null || printf 0)\n'
            '  count=$((count + 1))\n'
            '  printf "%s\\n" "$count" > "$counter"\n'
            '  if [ "$count" -eq 2 ]; then exec sleep 30; fi\n'
            "fi\n"
            'exec "$real_git" "$@"\n',
            encoding="utf-8",
        )
        late_timeout_git.chmod(0o755)
        late_timeout_git_env_path = os.pathsep.join(
            (str(late_timeout_path), os.environ.get("PATH", ""))
        )
        for static in (False, True):
            late_timeout_counter.write_text("0\n", encoding="utf-8")
            report = run_report(static, path=late_timeout_git_env_path)
            assert report["source_revision"] == revision
            assert report["source_tree_status"] == "clean"
            assert "source_revision_error" not in report
            assert report["source_revision_at_completion"] is None
            assert report["source_tree_status_at_completion"] == "unavailable"
            assert report["source_revision_error_at_completion"] == (
                "git command timed out after 3000ms"
            )
            assert "source_tree_error_at_completion" not in report
            assert report["source_tree_changed_during_run"] is None
            assert report["source_changed_paths_status"] == "unavailable"
            assert "source_changed_paths_during_run" not in report
            assert late_timeout_counter.read_text(encoding="utf-8").strip() == "2"
            assert report["mode"] == ("static-lint" if static else "playwright")
            if not static:
                assert report["status"] == "complete"

        for static in (False, True):
            report = run_report(static)
            assert report["source_revision"] == revision
            assert report["source_tree_status"] == "clean"
            assert "source_revision_error" not in report

        stalled_commands = {
            "status": '[ "$1" = "status" ]',
            "tracked-diff": '[ "$1" = "diff" ] && [ "$2" = "--binary" ]',
            "changed-paths": '[ "$1" = "diff" ] && [ "$2" = "--no-renames" ]',
            "untracked-files": '[ "$1" = "ls-files" ]',
        }
        for command_name, command_match in stalled_commands.items():
            stalled_git_path = tmp_path / f"stalled-{command_name}-git-bin"
            stalled_git_path.mkdir()
            stalled_git = stalled_git_path / "git"
            stalled_marker = tmp_path / f"stalled-{command_name}-marker"
            stalled_git.write_text(
                "#!/bin/sh\n"
                f"if {command_match} && [ ! -e {shlex.quote(str(stalled_marker))} ]; then\n"
                f"  : > {shlex.quote(str(stalled_marker))}\n"
                "  exec sleep 30\n"
                "fi\n"
                f"exec {shlex.quote(git_bin)} \"$@\"\n",
                encoding="utf-8",
            )
            stalled_git.chmod(0o755)
            stalled_git_env_path = os.pathsep.join(
                (str(stalled_git_path), os.environ.get("PATH", ""))
            )
            for static in (False, True):
                stalled_marker.unlink(missing_ok=True)
                report = run_report(static, path=stalled_git_env_path)
                assert stalled_marker.is_file(), (
                    f"the {command_name} provenance command was not stalled"
                )
                assert report["source_revision"] == revision
                assert report["source_tree_status"] == "unavailable"
                assert report["source_tree_error"] == (
                    "git command timed out after 3000ms"
                )
                assert report["source_tree_status_at_completion"] == "clean"
                assert "source_tree_error_at_completion" not in report
                assert report["source_tree_changed_during_run"] is None
                assert report["source_changed_paths_status"] == "unavailable"
                assert report["mode"] == ("static-lint" if static else "playwright")
                if not static:
                    assert report["status"] == "complete"

        for command_to_fail in ("status", "diff"):
            failing_worktree_path = tmp_path / f"failing-{command_to_fail}-git-bin"
            failing_worktree_path.mkdir()
            failing_worktree_git = failing_worktree_path / "git"
            failing_worktree_git.write_text(
                "#!/bin/sh\n"
                f'if [ "$1" = "{command_to_fail}" ]; then exit 23; fi\n'
                f'exec "{git_bin}" "$@"\n',
                encoding="utf-8",
            )
            failing_worktree_git.chmod(0o755)
            failing_worktree_env_path = os.pathsep.join(
                (str(failing_worktree_path), os.environ.get("PATH", ""))
            )
            for static in (False, True):
                report = run_report(static, path=failing_worktree_env_path)
                assert report["source_revision"] == revision
                assert report["source_tree_status"] == "unavailable"
                assert report["source_tree_error"] == (
                    "git command failed (exit code 23)"
                )
                assert "source_revision_error" not in report
                assert report["source_revision_at_completion"] == revision
                assert report["source_tree_status_at_completion"] == "unavailable"
                assert report["source_tree_error_at_completion"] == (
                    "git command failed (exit code 23)"
                )
                assert report["source_changed_paths_status"] == "unavailable"
                assert report["mode"] == ("static-lint" if static else "playwright")

        (fixture_root / html_path).write_text(html + "\n<!-- modified -->\n", encoding="utf-8")
        for static in (False, True):
            report = run_report(static)
            assert report["source_revision"] == revision
            assert report["source_tree_status"] == "modified"
            assert "source_revision_error" not in report
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)


def test_responsive_qa_flags_source_edits_during_static_run(tmp_path):
    node_bin = os.environ.get("ASKJAMIE_NODE") or shutil.which("node")
    git_bin = shutil.which("git")
    if not node_bin or not git_bin:
        pytest.skip("Node.js and Git are required")

    fixture_root = tmp_path / "runner"
    (fixture_root / "scripts").mkdir(parents=True)
    (fixture_root / "first").mkdir()
    (fixture_root / "second").mkdir()
    source = (ROOT / "scripts/responsive-qa.mjs").read_text(encoding="utf-8")
    marker = "    const html   = readFileSync(fsPath, 'utf-8');"
    assert source.count(marker) == 1
    mutation_hook = marker + """
    if (process.env.RESPONSIVE_QA_MUTATE_SOURCE === '1' && path === '/first/') {
      const sourcePath = resolve(ROOT, 'first/index.html');
      writeFileSync(sourcePath, readFileSync(sourcePath, 'utf-8') + '\\n<!-- changed during QA -->\\n');
    }"""
    mutation_hook += """
    if (process.env.RESPONSIVE_QA_COMMIT_SOURCE === '1' && path === '/second/') {
      writeFileSync(resolve(ROOT, 'committed-added.txt'), 'committed addition fixture content\\n');
      writeFileSync(resolve(ROOT, 'committed added – café.txt'), 'Unicode addition fixture content\\n');
      unlinkSync(resolve(ROOT, 'second/index.html'));
      execFileSync(
        'git',
        ['add', 'first/index.html', 'committed-added.txt', 'committed added – café.txt'],
        { cwd: ROOT }
      );
      execFileSync(
        'git',
        ['rm', '-q', '--', 'second/index.html', 'committed removed – résumé.txt'],
        { cwd: ROOT }
      );
      execFileSync('git', ['commit', '-q', '-m', 'source changed during QA'], { cwd: ROOT });
    }"""
    mutation_hook += """
    if (process.env.RESPONSIVE_QA_MUTATE_UNTRACKED === '1' && path === '/first/') {
      const sourcePath = resolve(ROOT, 'notes.txt');
      writeFileSync(sourcePath, 'untracked file added during QA\\n');
    }"""
    mutation_hook += """
    if (process.env.RESPONSIVE_QA_MUTATE_REMOVED === '1' && path === '/second/') {
      unlinkSync(resolve(ROOT, 'second/index.html'));
    }"""
    mutation_hook += """
    if (process.env.RESPONSIVE_QA_ADD_TRACKED === '1' && path === '/first/') {
      writeFileSync(resolve(ROOT, 'added-tracked.txt'), 'added tracked fixture content\\n');
      execFileSync('git', ['add', 'added-tracked.txt'], { cwd: ROOT });
    }"""
    mutation_hook += """
    if (process.env.RESPONSIVE_QA_RENAME_SOURCE === '1' && path === '/second/') {
      execFileSync(
        'git',
        ['mv', 'rename\\nsource.txt', 'renamed target.txt'],
        { cwd: ROOT }
      );
    }"""
    (fixture_root / "scripts/responsive-qa.mjs").write_text(
        source.replace(marker, mutation_hook), encoding="utf-8"
    )
    _copy_responsive_qa_dependencies(fixture_root / "scripts/responsive-qa.mjs")
    html = (
        '<!doctype html><html><head><meta name="viewport" '
        'content="width=device-width"></head><body><a class="skip-link" href="#main">Skip</a>'
        '<nav class="primary-nav"><a href="/about/">About</a></nav>'
        '<main id="main"><h1>Fixture</h1></main><footer><a href="/search/">Search</a></footer>'
        '<script src="/assets/js/app.js"></script></body></html>'
    )
    (fixture_root / "first/index.html").write_text(html, encoding="utf-8")
    (fixture_root / "second/index.html").write_text(html, encoding="utf-8")
    rename_source_name = "rename\nsource.txt"
    rename_source_content = "private rename fixture contents\n"
    (fixture_root / rename_source_name).write_text(
        rename_source_content, encoding="utf-8"
    )
    committed_removed_name = "committed removed – résumé.txt"
    (fixture_root / committed_removed_name).write_text(
        "Unicode removal fixture content\n", encoding="utf-8"
    )
    (fixture_root / "sitemap.xml").write_text(
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        '<url><loc>https://askjamie.bot/first/</loc></url>'
        '<url><loc>https://askjamie.bot/second/</loc></url></urlset>',
        encoding="utf-8",
    )
    _ensure_sitemap_fixture_pages(fixture_root, allow_fixture_sitemap_roots=True)
    (fixture_root / ".gitignore").write_text(
        "/assets/audit/responsive-qa/\n", encoding="utf-8"
    )
    subprocess.run([git_bin, "init", "-q"], cwd=fixture_root, check=True)
    subprocess.run(
        [git_bin, "config", "user.name", "Responsive QA Test"],
        cwd=fixture_root,
        check=True,
    )
    subprocess.run(
        [git_bin, "config", "user.email", "responsive-qa-test@example.invalid"],
        cwd=fixture_root,
        check=True,
    )
    subprocess.run([git_bin, "add", "."], cwd=fixture_root, check=True)
    subprocess.run(
        [git_bin, "commit", "-q", "-m", "fixture baseline"],
        cwd=fixture_root,
        check=True,
    )
    starting_revision = subprocess.check_output(
        [git_bin, "rev-parse", "HEAD"], cwd=fixture_root, text=True
    ).strip()

    def run_static(
        mutate_source=False,
        mutate_untracked=False,
        mutate_removed=False,
        commit_source=False,
        add_tracked=False,
        rename_source=False,
    ):
        result = subprocess.run(
            [node_bin, "scripts/responsive-qa.mjs", "--static"],
            cwd=fixture_root,
            text=True,
            capture_output=True,
            timeout=30,
            env={
                **os.environ,
                "RESPONSIVE_QA_MUTATE_SOURCE": "1" if mutate_source else "0",
                "RESPONSIVE_QA_MUTATE_UNTRACKED": "1" if mutate_untracked else "0",
                "RESPONSIVE_QA_MUTATE_REMOVED": "1" if mutate_removed else "0",
                "RESPONSIVE_QA_COMMIT_SOURCE": "1" if commit_source else "0",
                "RESPONSIVE_QA_ADD_TRACKED": "1" if add_tracked else "0",
                "RESPONSIVE_QA_RENAME_SOURCE": "1" if rename_source else "0",
            },
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(
            (fixture_root / "assets/audit/responsive-qa/results.json").read_text(
                encoding="utf-8"
            )
        )

    unchanged_report = run_static()
    assert unchanged_report["source_tree_status"] == "clean"
    assert unchanged_report["source_tree_status_at_completion"] == "clean"
    assert unchanged_report["source_tree_changed_during_run"] is False
    assert unchanged_report["source_changed_paths_status"] == "recorded"
    assert unchanged_report["source_changed_paths_classification_status"] == "recorded"
    assert unchanged_report["source_changed_paths_during_run"] == []

    first_page = fixture_root / "first/index.html"
    first_page.write_text(html + "\n<!-- already modified before QA -->\n", encoding="utf-8")
    report = run_static(
        mutate_source=True,
        mutate_untracked=True,
        mutate_removed=True,
        add_tracked=True,
    )
    assert report["source_revision"] == report["source_revision_at_completion"]
    assert report["source_tree_status"] == "modified"
    assert report["source_tree_status_at_completion"] == "modified"
    assert report["source_tree_changed_during_run"] is True
    assert report["source_changed_paths_status"] == "recorded"
    assert report["source_changed_paths_classification_status"] == "recorded"
    assert report["source_changed_paths_during_run"] == [
        {"path": "added-tracked.txt", "classification": "added"},
        {"path": "first/index.html", "classification": "edited"},
        {"path": "notes.txt", "classification": "added"},
        {"path": "second/index.html", "classification": "removed"},
    ]
    assert "changed during QA" not in json.dumps(report)
    assert "added tracked fixture content" not in json.dumps(report)

    subprocess.run([git_bin, "reset", "--hard", "-q", "HEAD"], cwd=fixture_root, check=True)
    rename_report = run_static(rename_source=True)
    assert rename_report["source_changed_paths_status"] == "recorded"
    assert rename_report["source_changed_paths_classification_status"] == "recorded"
    assert rename_report["source_changed_paths_during_run"] == [
        {"path": rename_source_name, "classification": "removed"},
        {"path": "renamed target.txt", "classification": "added"},
    ]
    serialized_rename_report = (
        fixture_root / "assets/audit/responsive-qa/results.json"
    ).read_text(encoding="utf-8")
    assert json.loads(serialized_rename_report) == rename_report
    assert "rename\\nsource.txt" in serialized_rename_report
    assert rename_source_content not in serialized_rename_report
    assert "renamed target.txt" in serialized_rename_report

    subprocess.run([git_bin, "reset", "--hard", "-q", "HEAD"], cwd=fixture_root, check=True)
    (fixture_root / "notes.txt").unlink()
    committed_report = run_static(mutate_source=True, commit_source=True)
    assert committed_report["source_revision"] == starting_revision
    assert committed_report["source_revision_at_completion"] != starting_revision
    assert committed_report["source_tree_status"] == "clean"
    assert committed_report["source_tree_status_at_completion"] == "clean"
    assert committed_report["source_tree_changed_during_run"] is True
    assert committed_report["source_changed_paths_status"] == "recorded"
    assert committed_report["source_changed_paths_classification_status"] == "recorded"
    assert committed_report["source_changed_paths_during_run"] == [
        {"path": "committed added – café.txt", "classification": "added"},
        {"path": committed_removed_name, "classification": "removed"},
        {"path": "committed-added.txt", "classification": "added"},
        {"path": "first/index.html", "classification": "edited"},
        {"path": "second/index.html", "classification": "removed"},
    ]
    assert (
        committed_report["source_revision_at_completion"]
        != committed_report["source_revision"]
    )
    assert subprocess.check_output(
        [git_bin, "status", "--porcelain"], cwd=fixture_root, text=True
    ) == ""


@pytest.mark.parametrize("setup_stop", ["context_rejection", "signal"])
def test_responsive_qa_writes_partial_report_when_context_setup_stops(tmp_path, setup_stop):
    node_bin = os.environ.get("ASKJAMIE_NODE") or shutil.which("node")
    node_modules = os.environ.get("NODE_PATH")
    repo_modules = ROOT / "node_modules"
    if not node_modules and (repo_modules / "playwright").exists():
        node_modules = str(repo_modules)
    if not node_bin or not node_modules or not (Path(node_modules) / "playwright").exists():
        pytest.skip("Playwright runtime is unavailable")

    fixture_root = tmp_path / "runner"
    runner = fixture_root / "scripts/responsive-qa.mjs"
    runner.parent.mkdir(parents=True)
    source = (ROOT / "scripts/responsive-qa.mjs").read_text(encoding="utf-8")
    marker = "    workers.push(...await Promise.all(QA_VIEWPORTS.map(async vp => {"
    assert source.count(marker) == 1
    setup_mode = json.dumps(setup_stop)
    setup_hook = f"""
  const originalNewContext = browser.newContext.bind(browser);
  let setupCalls = 0;
  browser.newContext = options => {{
    const call = setupCalls++;
    if ({setup_mode} === 'context_rejection' && call === 0) {{
      return Promise.reject(new Error('fixture context setup rejected'));
    }}
    const pending = originalNewContext(options);
    if ({setup_mode} === 'signal') {{
      if (call === 0) {{
        writeFileSync(resolve(ROOT, 'during-run.txt'), 'changed during QA\\n');
        process.kill(process.pid, 'SIGTERM');
      }}
      return pending.then(ctx => new Promise(resolve => setTimeout(() => resolve(ctx), 500)));
    }}
    return pending;
  }};
"""
    runner.write_text(source.replace(marker, setup_hook + marker), encoding="utf-8")
    _copy_responsive_qa_dependencies(runner)
    (fixture_root / "sitemap.xml").write_text(
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        '<url><loc>https://askjamie.bot/setup/</loc></url></urlset>',
        encoding="utf-8",
    )
    _ensure_sitemap_fixture_pages(fixture_root, allow_fixture_sitemap_roots=True)
    (fixture_root / ".gitignore").write_text(
        "/assets/audit/responsive-qa/\n", encoding="utf-8"
    )
    (fixture_root / "during-run.txt").write_text("baseline\n", encoding="utf-8")
    git_bin = shutil.which("git")
    assert git_bin, "Git is required to verify report provenance"
    subprocess.run([git_bin, "init", "-q"], cwd=fixture_root, check=True)
    subprocess.run(
        [git_bin, "config", "user.name", "Responsive QA Test"],
        cwd=fixture_root,
        check=True,
    )
    subprocess.run(
        [git_bin, "config", "user.email", "responsive-qa-test@example.invalid"],
        cwd=fixture_root,
        check=True,
    )
    subprocess.run(
        [
            git_bin,
            "add",
            ".gitignore",
            "during-run.txt",
            "scripts/responsive-qa.mjs",
            "scripts/universe-not-found.mjs",
            "scripts/page-map-sweep-performance.mjs",
            "scripts/responsive-qa-warning-trend.mjs",
            "scripts/responsive-qa-failure-summary.mjs",
            "sitemap.xml",
            "setup",
        ],
                   cwd=fixture_root, check=True)
    subprocess.run([git_bin, "commit", "-q", "-m", "setup fixture"],
                   cwd=fixture_root, check=True)
    revision = subprocess.check_output(
        [git_bin, "rev-parse", "HEAD"], cwd=fixture_root, text=True
    ).strip()

    report_path = fixture_root / "assets/audit/responsive-qa/results.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_text(
        json.dumps({"status": "complete", "results": [{"stale": True}]}),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            node_bin,
            "scripts/responsive-qa.mjs",
            "--base=http://127.0.0.1:9",
        ],
        cwd=fixture_root,
        text=True,
        capture_output=True,
        timeout=30,
        env={**os.environ, "NODE_PATH": node_modules},
    )

    if "Required browser QA could not start: Chromium could not be launched" in result.stderr:
        pytest.skip("Chromium could not launch; context setup was not reached")

    assert result.returncode == (143 if setup_stop == "signal" else 1), (
        result.stdout + result.stderr
    )
    assert report_path.is_file(), "context setup stop did not replace the stale report"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    expected_reason = (
        "fixture context setup rejected"
        if setup_stop == "context_rejection"
        else "Received SIGTERM"
    )
    assert report["status"] == "early_stop"
    assert report["failure_reason"] == expected_reason
    assert report["source_revision"] == revision
    assert report["source_tree_status"] == "clean"
    assert report["source_tree_status_at_completion"] == (
        "modified" if setup_stop == "signal" else "clean"
    )
    assert report["source_tree_changed_during_run"] is (setup_stop == "signal")
    assert report["playwright_version"]
    assert report["chromium_version"]
    assert report["pages_checked"] == 0
    assert report["total_checks"] == 0
    assert report["passing_checks"] == 0
    assert report["failing_checks"] == 0
    assert report["results"] == []


@pytest.mark.parametrize(
    "stop_phase",
    ["route", "mid_route", "mid_route_exception", "cleanup", "exception", "forced_kill"],
)
def test_responsive_qa_retains_clearly_partial_report_after_early_stop(tmp_path, stop_phase):
    node_bin = os.environ.get("ASKJAMIE_NODE") or shutil.which("node")
    node_modules = os.environ.get("NODE_PATH")
    repo_modules = ROOT / "node_modules"
    if not node_modules and (repo_modules / "playwright").exists():
        node_modules = str(repo_modules)
    if not node_bin or not node_modules or not (Path(node_modules) / "playwright").exists():
        pytest.skip("Playwright runtime is unavailable")

    slow_route_started = threading.Event()
    release_slow_route = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, _format, *_args):
            return

        def do_GET(self):
            if self.path.startswith("/slow/"):
                slow_route_started.set()
                release_slow_route.wait(timeout=60)
            payload = (
                b'<!doctype html><html><head><meta name="viewport" '
                b'content="width=device-width"></head><body><h1>Fixture</h1></body></html>'
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    fixture_root = tmp_path / "runner"
    (fixture_root / "scripts").mkdir(parents=True)
    _copy_responsive_qa_fixture(ROOT / "scripts/responsive-qa.mjs", fixture_root / "scripts/responsive-qa.mjs")
    if stop_phase in ("mid_route", "mid_route_exception", "forced_kill"):
        runner = fixture_root / "scripts/responsive-qa.mjs"
        source = runner.read_text(encoding="utf-8")
        marker = "      return row;"
        assert source.count(marker) == 1
        failure = (
            "\n        throw new Error('fixture viewport failed');"
            if stop_phase == "mid_route_exception"
            else ""
        )
        runner.write_text(source.replace(
            marker,
            """      if (path === '/fast/' && vp.name === 'mobile-320') {
        await new Promise(resolve => setTimeout(resolve, 5000));""" + failure + """
      }
      return row;"""
        ), encoding="utf-8")
    elif stop_phase == "cleanup":
        runner = fixture_root / "scripts/responsive-qa.mjs"
        source = runner.read_text(encoding="utf-8")
        # Both the normal cleanup and signal handler await the same close.
        # The normal continuation must not replace an early-stop receipt.
        marker = "  const allResults = [];"
        assert source.count(marker) == 1
        source = source.replace(marker, """
  const realClose = browser.close.bind(browser);
  let closing;
  browser.close = () => {
    if (!closing) {
      closing = new Promise(resolve => setTimeout(resolve, 100)).then(realClose);
      process.kill(process.pid, 'SIGTERM');
    }
    return closing;
  };
""" + marker)
        runner.write_text(source, encoding="utf-8")
    elif stop_phase == "exception":
        runner = fixture_root / "scripts/responsive-qa.mjs"
        source = runner.read_text(encoding="utf-8")
        marker = "    for (const { ctx } of workers) await ctx.close();"
        assert source.count(marker) == 1
        runner.write_text(source.replace(
            marker, '    throw new Error("fixture cleanup failed");\n' + marker
        ), encoding="utf-8")
    (fixture_root / "sitemap.xml").write_text(
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        '<url><loc>https://askjamie.bot/fast/</loc></url>'
        + ('<url><loc>https://askjamie.bot/slow/</loc></url>' if stop_phase == "route" else '')
        + '</urlset>',
        encoding="utf-8",
    )
    _ensure_sitemap_fixture_pages(fixture_root, allow_fixture_sitemap_roots=True)
    report_path = fixture_root / "assets/audit/responsive-qa/results.json"
    process = subprocess.Popen(
        [
            node_bin,
            "scripts/responsive-qa.mjs",
            f"--base=http://127.0.0.1:{server.server_address[1]}",
        ],
        cwd=fixture_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "NODE_PATH": node_modules},
    )
    try:
        if stop_phase == "route":
            assert slow_route_started.wait(timeout=90), "responsive QA never reached the slow route"
            process.terminate()
        elif stop_phase in ("mid_route", "mid_route_exception", "forced_kill"):
            deadline = time.monotonic() + 90
            partial_report_seen = False
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    break
                if report_path.is_file():
                    try:
                        in_progress = json.loads(report_path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        in_progress = {}
                    if (
                        in_progress.get("status") == "in_progress"
                        and in_progress.get("pages_checked") == 0
                        and in_progress.get("total_checks", 0) > 0
                    ):
                        partial_report_seen = True
                        if stop_phase == "mid_route":
                            process.terminate()
                        elif stop_phase == "forced_kill":
                            active_retry = subprocess.run(
                                [
                                    node_bin,
                                    "scripts/responsive-qa.mjs",
                                    f"--base=http://127.0.0.1:{server.server_address[1]}",
                                ],
                                cwd=fixture_root,
                                text=True,
                                capture_output=True,
                                timeout=30,
                                env={**os.environ, "NODE_PATH": node_modules},
                            )
                            assert active_retry.returncode == 1
                            assert (
                                f"Responsive QA is already running (PID {process.pid})"
                                in active_retry.stderr
                            )
                            still_active = json.loads(
                                report_path.read_text(encoding="utf-8")
                            )
                            assert still_active["status"] == "in_progress"
                            assert still_active["runner_pid"] == process.pid
                            process.kill()
                        break
                time.sleep(0.01)
            assert partial_report_seen, "responsive QA did not save viewport results before route completion"
        _stdout, stderr = process.communicate(
            timeout=10 if stop_phase in ("route", "mid_route", "forced_kill") else 90
        )
        if stop_phase == "forced_kill":
            assert process.returncode == -9
            report = json.loads(report_path.read_text(encoding="utf-8"))
            assert report["status"] == "in_progress"
            assert report["runner_pid"] == process.pid
            assert report["failure_reason"] is None
            assert report["pages_checked"] == 0
            assert report["total_checks"] == len(report["results"])
            assert report["total_checks"] > 0
            assert report["passing_checks"] == sum(
                1 for row in report["results"] if row["pass"]
            )
            assert report["failing_checks"] == sum(
                1 for row in report["results"] if not row["pass"]
            )

            preload = tmp_path / "fail-browser-launch.cjs"
            preload.write_text(
                """
const Module = require('module');
const originalRequire = Module.prototype.require;
Module.prototype.require = function (id) {
  if (id === 'playwright') {
    return { chromium: { launch: async () => { throw new Error('fixture launch failure'); } } };
  }
  return originalRequire.apply(this, arguments);
};
""".strip()
                + "\n",
                encoding="utf-8",
            )
            recovery = subprocess.run(
                [
                    node_bin,
                    "scripts/responsive-qa.mjs",
                    f"--base=http://127.0.0.1:{server.server_address[1]}",
                ],
                cwd=fixture_root,
                text=True,
                capture_output=True,
                timeout=30,
                env={
                    **os.environ,
                    "NODE_PATH": node_modules,
                    "NODE_OPTIONS": f"--require {preload}",
                },
            )
            assert recovery.returncode == 1
            assert "Required browser QA could not start: Chromium could not be launched" in recovery.stderr
            abandoned = json.loads(report_path.read_text(encoding="utf-8"))
            assert abandoned["status"] == "abandoned"
            assert abandoned["failure_reason"] is None
            assert abandoned["abandoned_at"]
            assert abandoned["runner_pid"] == process.pid
            assert abandoned["pages_checked"] == report["pages_checked"]
            assert abandoned["total_checks"] == report["total_checks"]
            assert abandoned["passing_checks"] == report["passing_checks"]
            assert abandoned["failing_checks"] == report["failing_checks"]
            assert abandoned["results"] == report["results"]
            assert "Failure reason remains unknown." in recovery.stderr
            archived = list(
                (fixture_root / "assets/audit/responsive-qa/interrupted-runs").glob(
                    "*--abandoned.json"
                )
            )
            assert len(archived) == 1
            archived_report = json.loads(archived[0].read_text(encoding="utf-8"))
            assert archived_report["status"] == "abandoned"
            assert archived_report["generated"] == abandoned["generated"]
            assert archived_report["total_checks"] == abandoned["total_checks"]
            return

        expected_returncode = (
            1 if stop_phase in ("exception", "mid_route_exception") else 143
        )
        assert process.returncode == expected_returncode
        assert report_path.is_file(), "no report was retained after the early stop"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["status"] == "early_stop"
        expected_reason = {
            "exception": "fixture cleanup failed",
            "mid_route_exception": "fixture viewport failed",
        }.get(stop_phase, "Received SIGTERM")
        assert report["failure_reason"] == expected_reason
        if stop_phase in ("exception", "mid_route_exception"):
            assert expected_reason in stderr
        assert report["playwright_version"]
        assert report["chromium_version"]
        assert report["pages_checked"] == (
            0 if stop_phase in ("mid_route", "mid_route_exception") else 1
        )
        assert report["pages_expected"] == (2 if stop_phase == "route" else 1)
        rows = report["results"]
        assert report["total_checks"] == len(rows)
        if stop_phase in ("mid_route", "mid_route_exception"):
            assert 0 < len(rows) < RESPONSIVE_QA_VIEWPORT_COUNT
        else:
            assert len(rows) == RESPONSIVE_QA_VIEWPORT_COUNT
        assert report["passing_checks"] == sum(1 for row in rows if row["pass"])
        assert report["failing_checks"] == sum(1 for row in rows if not row["pass"])
    finally:
        release_slow_route.set()
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)


def test_responsive_qa_keeps_csp_suppression_narrow_and_reports_resource_failures():
    source = (ROOT / "scripts/responsive-qa.mjs").read_text(encoding="utf-8")

    assert "effectiveConsoleErrors" not in source
    assert "isMermaidInlineStyleWarning" in source
    assert "REQUEST FAILED: [" in source
    assert "HTTP ${r.status}: [" in source
    assert "CONSOLE: " in source
    assert "checkCriticalHeroStyles" in source
    assert "CRITICAL HERO STYLESHEET RESPONSE MISSING" in source
    assert "CRITICAL HERO STYLE MISMATCH" in source
    assert "critical_hero_styles: criticalHeroStyles.evidence" in source


def test_brandguard_forced_colors_failures_include_route_viewport_and_readability_evidence():
    source = (ROOT / "scripts/responsive-qa.mjs").read_text(encoding="utf-8")
    formatter = source[
        source.index("function formatBrandGuardForcedColorsReadabilityResult"):
        source.index("async function checkBrandGuardForcedColorsReadability")
    ]
    selectors = [
        ".askjamie-main .askjamie-breadcrumb",
        ".askjamie-main .askjamie-hero-copy h1",
        ".askjamie-main .askjamie-hero-copy .hero-subtitle",
        ".askjamie-main .askjamie-hero-copy .hero-tagline",
    ]
    evidence = {
        "forced_colors_active": False,
        "viewport_width": 320,
        "document_scroll_width": 340,
        "document_overflow": True,
        "elements": {
            selector: {
                "text_present": True,
                "visible": True,
                "clipped": False,
                "outside_viewport": False,
                "contrast": {
                    "foreground_color": "rgb(0, 0, 0)",
                    "effective_foreground_color": "rgb(0, 0, 0)",
                    "background_color": "rgba(0, 0, 0, 0)",
                    "effective_background_color": "rgb(255, 255, 255)",
                    "contrast_ratio": 21,
                    "minimum_contrast_ratio": 4.5,
                },
            }
            for selector in selectors
        },
        "overlaps": [{
            "first": selectors[0],
            "second": selectors[1],
            "overlap_width_px": 8,
            "overlap_height_px": 3,
        }],
    }
    evidence["elements"][selectors[1]].update({
        "visible": False,
        "clipped": True,
        "contrast": {
            "foreground_color": "rgb(120, 120, 120)",
            "effective_foreground_color": "rgb(120, 120, 120)",
            "background_color": "Canvas",
            "effective_background_color": "rgb(255, 255, 255)",
            "contrast_ratio": 4.42,
            "minimum_contrast_ratio": 4.5,
        },
    })
    node_test = f"""
const vm = require('node:vm');
const context = vm.createContext({{ BRANDGUARD_GEOMETRY_SELECTORS: {json.dumps(selectors)} }});
vm.runInContext({json.dumps(formatter)}, context);
const format = vm.runInContext('formatBrandGuardForcedColorsReadabilityResult', context);
console.log(JSON.stringify(format(
  {json.dumps(evidence)},
  '/lens-system/okhp3-brandguard/lvmh/',
  320
)));
"""
    result = subprocess.run(
        ["node", "-e", node_test], cwd=ROOT, text=True, capture_output=True, check=True
    )
    output = json.loads(result.stdout)
    errors = output["errors"]
    assert any("FORCED-COLORS MODE UNAVAILABLE" in error for error in errors)
    assert any("route=/lens-system/okhp3-brandguard/lvmh/" in error for error in errors)
    assert any("viewport=320px" in error for error in errors)
    assert any(selectors[1] in error and "not visible" in error for error in errors)
    assert any(selectors[1] in error and "is clipped" in error for error in errors)
    assert any("document scroll width 340px" in error for error in errors)
    assert any("text elements overlap" in error for error in errors)
    assert any(
        selectors[1] in error and "contrast=4.42:1" in error and
        "foreground=rgb(120, 120, 120)" in error and
        "background=rgb(255, 255, 255)" in error
        for error in errors
    )
    assert output["evidence"]["forced_colors_active"] is False
    assert "brandguard_forced_colors_readability" in source
    assert (
        "Every sitemap-listed BrandGuard case study is checked in isolated "
        "browser-emulated forced-colors mode at 320px and 1280px"
    ) in source


def test_brandguard_forced_colors_contrast_composites_transparent_layers_and_alpha():
    source = (ROOT / "scripts/responsive-qa.mjs").read_text(encoding="utf-8")
    helpers = source[
        source.index("function parseBrandGuardForcedColorsColor"):
        source.index("function formatBrandGuardForcedColorsReadabilityResult")
    ]
    node_test = f"""
const vm = require('node:vm');
const {{ chromium }} = require('playwright');
const context = vm.createContext({{}});
vm.runInContext({json.dumps(helpers)}, context);
const measure = vm.runInContext('calculateBrandGuardForcedColorsContrast', context);
const transparent = measure({{
  foregroundColor: 'rgb(0, 0, 0)',
  backgroundLayers: ['rgba(0, 0, 0, 0)', 'rgba(255, 255, 255, 0)'],
  canvasColor: 'rgb(255, 255, 255)',
  effectiveOpacity: 1,
}});
const translucent = measure({{
  foregroundColor: 'rgb(0, 0, 0)',
  backgroundLayers: ['rgba(0, 0, 0, 0)', 'rgba(0, 0, 0, 0.5)'],
  canvasColor: 'rgb(255, 255, 255)',
  effectiveOpacity: 1,
}});
(async () => {{
  const browser = await chromium.launch({{ headless: true }});
  try {{
    const page = await browser.newPage({{ colorScheme: 'light' }});
    await page.emulateMedia({{ forcedColors: 'active' }});
    await page.setContent(
      '<main id="surface" style="background-color: Canvas">' +
      '<p id="copy" style="color: CanvasText; background-color: transparent">Readable copy</p>' +
      '</main>'
    );
    const resolved = await page.evaluate(() => {{
      const copy = document.querySelector('#copy');
      const layers = [];
      for (let ancestor = copy; ancestor; ancestor = ancestor.parentElement) {{
        layers.push(getComputedStyle(ancestor).backgroundColor);
        if (ancestor === document.documentElement) break;
      }}
      const probe = document.createElement('span');
      probe.style.color = 'CanvasText';
      probe.style.backgroundColor = 'Canvas';
      document.documentElement.append(probe);
      const canvasColor = getComputedStyle(probe).backgroundColor;
      probe.remove();
      return {{
        forcedColorsActive: matchMedia('(forced-colors: active)').matches,
        foregroundColor: getComputedStyle(copy).color,
        backgroundLayers: layers,
        canvasColor,
      }};
    }});
    const systemColorContrast = measure({{
      foregroundColor: resolved.foregroundColor,
      backgroundLayers: resolved.backgroundLayers,
      canvasColor: resolved.canvasColor,
      effectiveOpacity: 1,
    }});
    console.log(JSON.stringify({{transparent, translucent, resolved, systemColorContrast}}));
  }} finally {{
    await browser.close();
  }}
}})().catch(error => {{
  console.error(error);
  process.exitCode = 1;
}});
"""
    result = subprocess.run(
        ["node", "-e", node_test], cwd=ROOT, text=True, capture_output=True, check=True
    )
    output = json.loads(result.stdout)
    transparent = output["transparent"]
    assert transparent["contrast_ratio"] == 21
    assert transparent["effective_background_color"] == "rgb(255, 255, 255)"
    assert transparent["canvas_color"] == "rgb(255, 255, 255)"
    translucent = output["translucent"]
    assert translucent["effective_background_color"] == "rgb(128, 128, 128)"
    assert 5.2 < translucent["contrast_ratio"] < 5.3
    assert output["resolved"]["forcedColorsActive"] is True
    assert output["resolved"]["foregroundColor"] != "CanvasText"
    assert output["resolved"]["canvasColor"] != "Canvas"
    assert output["systemColorContrast"]["contrast_ratio"] >= 4.5


def test_brandguard_deferred_theme_text_fragments_detect_reflow_inside_fixed_box():
    node_bin = os.environ.get("ASKJAMIE_NODE") or shutil.which("node")
    node_modules = os.environ.get("NODE_PATH")
    repo_modules = ROOT / "node_modules"
    if not node_modules and (repo_modules / "playwright").exists():
        node_modules = str(repo_modules)
    if not node_bin or not node_modules or not (Path(node_modules) / "playwright").exists():
        pytest.skip("Playwright runtime is unavailable")

    node_test = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('scripts/responsive-qa.mjs', 'utf8');
const captureStart = source.indexOf('function captureBrandGuardTextLineFragments(');
const captureEnd = source.indexOf('\nconst BRANDGUARD_NARROW_CONTENT_SELECTORS', captureStart);
const compareStart = source.indexOf('function compareBrandGuardTextLineReflow(');
const compareEnd = source.indexOf('\nfunction captureBrandGuardTextLineFragments', compareStart);
if ([captureStart, captureEnd, compareStart, compareEnd].some(index => index < 0)) {
  throw new Error('BrandGuard text-line helpers could not be isolated');
}
const selectors = [
  '.askjamie-main .askjamie-breadcrumb',
  '.askjamie-main .askjamie-hero-copy h1',
  '.askjamie-main .askjamie-hero-copy .hero-subtitle',
  '.askjamie-main .askjamie-hero-copy .hero-tagline'
];
const context = vm.createContext({ BRANDGUARD_TEXT_LINE_SELECTORS: selectors });
vm.runInContext(
  source.slice(compareStart, compareEnd) + '\n' +
  source.slice(captureStart, captureEnd),
  context
);
const capture = vm.runInContext('captureBrandGuardTextLineFragments', context);
const compare = vm.runInContext('compareBrandGuardTextLineReflow', context);
const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 320, height: 780 } });
    await page.setContent(`<!doctype html><html><head><style>
      .askjamie-main { margin: 0; font: 16px/20px Arial, sans-serif; }
      .askjamie-breadcrumb { width: 300px; height: 30px; }
      .askjamie-hero-copy h1 {
        box-sizing: border-box; width: 160px; height: 80px; margin: 0;
        font: 16px/18px Arial, sans-serif;
      }
      .askjamie-hero-copy .hero-subtitle,
      .askjamie-hero-copy .hero-tagline { width: 300px; height: 30px; margin: 0; }
    </style>
    <style id="deferred-theme" media="not all">
      .askjamie-hero-copy h1 { letter-spacing: 5px; }
    </style></head><body class="askjamie-main">
      <nav class="askjamie-breadcrumb">BrandGuard breadcrumb</nav>
      <main class="askjamie-hero-copy">
        <h1>A reserved heading that wraps across several visible lines</h1>
        <p class="hero-subtitle">Stable subtitle copy</p>
        <p class="hero-tagline">Stable tagline copy</p>
      </main>
    </body></html>`);
    const before = await page.evaluate(capture, selectors);
    const beforeBox = await page.locator(selectors[1]).boundingBox();
    await page.evaluate(() => {
      document.querySelector('#deferred-theme').media = 'all';
      return new Promise(resolve => requestAnimationFrame(() =>
        requestAnimationFrame(resolve)
      ));
    });
    const after = await page.evaluate(capture, selectors);
    const afterBox = await page.locator(selectors[1]).boundingBox();
    const result = compare(before, after);
    console.log(JSON.stringify({ before, after, beforeBox, afterBox, result }));
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
"""
    result = subprocess.run(
        [node_bin, "-e", node_test],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=60,
        env={**os.environ, "NODE_PATH": node_modules},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    output = json.loads(result.stdout)

    assert output["beforeBox"]["width"] == output["afterBox"]["width"] == 160
    assert output["beforeBox"]["height"] == output["afterBox"]["height"] == 80
    assert output["before"][".askjamie-main .askjamie-hero-copy h1"]["lines"] != (
        output["after"][".askjamie-main .askjamie-hero-copy h1"]["lines"]
    )
    assert len(output["result"]["evidence"]["reflows"]) == 1
    assert output["result"]["evidence"]["reflows"][0]["selector"] == (
        ".askjamie-main .askjamie-hero-copy h1"
    )


def test_brandguard_tablet_font_geometry_has_separate_evidence_and_tolerance():
    source = (ROOT / "scripts/responsive-qa.mjs").read_text(encoding="utf-8")
    theme_check = source[
        source.index("async function checkBrandGuardHeroGeometry"):
        source.index("\nasync function checkBrandGuardWebFontGeometry")
    ]

    font_viewports = re.search(
        r"const BRANDGUARD_FONT_GEOMETRY_VIEWPORTS = new Set\(\[(.*?)\]\);",
        source,
        re.DOTALL,
    )
    theme_viewports = re.search(
        r"const BRANDGUARD_THEME_GEOMETRY_VIEWPORTS = new Set\(\[(.*?)\]\);",
        source,
        re.DOTALL,
    )
    assert theme_viewports
    assert {
        "mobile-400-edge",
        "mobile-401-edge",
    } <= set(re.findall(r"'([^']+)'", theme_viewports.group(1)))
    assert "BRANDGUARD_THEME_GEOMETRY_VIEWPORTS.has(vp.name)" in source
    geometry_selectors = re.search(
        r"const BRANDGUARD_GEOMETRY_SELECTORS = \[(.*?)\];",
        source,
        re.DOTALL,
    )
    assert font_viewports
    assert {
        "tablet-768",
        "tablet-769-edge",
        "tablet-899",
        "tablet-900-edge",
    } <= set(
        re.findall(r"'([^']+)'", font_viewports.group(1))
    )
    assert geometry_selectors
    assert re.findall(r"'([^']+)'", geometry_selectors.group(1)) == [
        ".askjamie-main .askjamie-breadcrumb",
        ".askjamie-main .askjamie-hero-copy h1",
        ".askjamie-main .askjamie-hero-copy .hero-subtitle",
        ".askjamie-main .askjamie-hero-copy .hero-tagline",
    ]

    font_check = source[
        source.index("async function checkBrandGuardWebFontGeometry"):
        source.index("\nasync function checkBrandGuardNarrowContent")
    ]
    assert "BRANDGUARD_FONT_GEOMETRY_TOLERANCE_PX = 1" in source
    assert "BRANDGUARD_GEOMETRY_TOLERANCE_PX = 1" in source
    assert "Math.abs(delta[property]) > BRANDGUARD_FONT_GEOMETRY_TOLERANCE_PX" in font_check
    assert "threshold_px: BRANDGUARD_FONT_GEOMETRY_TOLERANCE_PX" in font_check
    assert "fallback_font_geometry: fallbackGeometry" in font_check
    assert "loaded_font_geometry: loadedGeometry" in font_check
    assert "font_geometry_deltas: compareBrandGuardFontGeometry(" in font_check
    assert "font_geometry_tolerance_px: BRANDGUARD_FONT_GEOMETRY_TOLERANCE_PX" in font_check
    assert "function compareBrandGuardFontGeometry(fallbackGeometry, loadedGeometry)" in source
    assert "hero_font_geometry: heroFontGeometryEvidence" in source
    assert "brandguard_header_navigation_font_geometry:" in source
    assert "key !== 'header_navigation'" in source
    assert "hero_theme_geometry: heroThemeGeometry.evidence" in source
    assert "text_line_reflow: textLineReflow.evidence" in source
    assert "function compareBrandGuardTextLineReflow(beforeLines, afterLines)" in source
    assert "BRANDGUARD HERO TEXT-LINE REFLOW" in source
    assert "BRANDGUARD_TEXT_LINE_SELECTORS = [...BRANDGUARD_GEOMETRY_SELECTORS]" in source
    assert "mobile-400-edge (400px), mobile-401-edge (401px)" in source
    assert "link[rel~=\"stylesheet\"][href]" in theme_check
    assert "new URL(link.href).pathname === '/assets/css/theme.css'" in theme_check
    assert "themeDeferred: themeLink?.hasAttribute('data-deferred-styles') ?? null" in theme_check
    assert "themeLink.hasAttribute('data-deferred-styles')" in theme_check
    assert "before.themeDeferred === false" in theme_check
    assert "comparison: 'not-applicable-active-stylesheet'" in theme_check
    assert (
        "BrandGuard breadcrumb, heading, subtitle, and tagline box geometry and visible "
        "text-line reflow are checked on every "
        "sitemap-listed BrandGuard route"
    ) in source
    assert (
        "mobile-350-edge, mobile-355-boundary, mobile-356-gap, mobile-360, "
        "mobile-364-gap, mobile-365-boundary, mobile-370-edge"
    ) in source
    assert (
        "BrandGuard hero geometry after web fonts load is checked on every "
        "sitemap-listed BrandGuard route"
    ) in source
    assert "tablet-769-edge, tablet-899, and tablet-900-edge" in source


def test_brandguard_tablet_font_report_preserves_both_states_for_sitemap_routes(tmp_path):
    node_bin = os.environ.get("ASKJAMIE_NODE") or shutil.which("node")
    node_modules = os.environ.get("NODE_PATH")
    repo_modules = ROOT / "node_modules"
    if not node_modules and (repo_modules / "playwright").exists():
        node_modules = str(repo_modules)
    if not node_bin or not node_modules or not (Path(node_modules) / "playwright").exists():
        pytest.skip("Playwright runtime is unavailable")

    font_candidates = sorted(
        (repo_modules / "playwright-core/lib/vite").glob("**/*.ttf")
    )
    if not font_candidates:
        pytest.skip("Playwright's bundled fixture font is unavailable")
    font_data = base64.b64encode(font_candidates[0].read_bytes()).decode("ascii")

    route_paths = (
        "/lens-system/okhp3-brandguard/",
        "/lens-system/okhp3-brandguard/fixture-case-study/",
    )
    sitemap = (
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + "".join(
            f"<url><loc>https://askjamie.bot{path}</loc></url>"
            for path in route_paths
        )
        + "</urlset>"
    )

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, _format, *_args):
            return

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/assets/css/theme.css":
                payload = b".askjamie-main { display: block; }"
                content_type = "text/css"
            else:
                payload = (
                    '<!doctype html><html><head><meta name="viewport" '
                    'content="width=device-width"><title>BrandGuard font fixture</title>'
                    '<link rel="stylesheet" href="/assets/css/theme.css">'
                    '<link rel="stylesheet" '
                    'href="https://fonts.googleapis.com/css2?family=Fixture">'
                    '<style>'
                    '.askjamie-main { margin: 0; }'
                    '.askjamie-breadcrumb { font: 400 16px Kalam; width: 700px; height: 24px; }'
                    '.askjamie-hero-copy { width: 700px; }'
                    '.askjamie-hero-copy h1 { font: 400 24px "Baloo 2"; height: 36px; }'
                    '.askjamie-hero-copy .hero-subtitle { font: 400 18px Kalam; height: 28px; }'
                    '.askjamie-hero-copy .hero-tagline { font: 400 16px "Open Sans"; height: 24px; }'
                    '</style></head><body class="askjamie-main">'
                    '<nav class="askjamie-breadcrumb">Fixture breadcrumb</nav>'
                    '<main class="askjamie-hero-copy" data-font-geometry-reserved="true">'
                    '<h1>Fixture heading</h1>'
                    '<p class="hero-subtitle">Fixture subtitle</p>'
                    '<p class="hero-tagline">Fixture tagline</p>'
                    '</main>'
                    + (
                        '<script>if (window.innerWidth === 769) '
                        'document.querySelector(".askjamie-hero-copy h1").remove();</script>'
                        if path == route_paths[1]
                        else ''
                    )
                    + '</body></html>'
                ).encode("utf-8")
                content_type = "text/html"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    fixture_root = tmp_path / "runner"
    scripts = fixture_root / "scripts"
    scripts.mkdir(parents=True)
    runner_source = (ROOT / "scripts/responsive-qa.mjs").read_text(encoding="utf-8")
    viewports_match = re.search(
        r"const VIEWPORTS = \[.*?\n\];",
        runner_source,
        re.DOTALL,
    )
    assert viewports_match
    runner_source = (
        runner_source[:viewports_match.start()]
        + """const VIEWPORTS = [
  { name: 'tablet-769-edge', width: 769, height: 1024 },
  { name: 'tablet-900-edge', width: 900, height: 1024 },
];"""
        + runner_source[viewports_match.end():]
    )
    stylesheet_continue = """if (requestUrl.hostname === 'fonts.googleapis.com' &&
            requestUrl.pathname === '/css2' &&
            requestType === 'stylesheet') {
          return route.continue();
        }"""
    font_continue = """if (requestUrl.hostname === 'fonts.gstatic.com' && requestType === 'font') {
          return brandGuardFontGate.then(() => route.continue());
        }"""
    assert runner_source.count(stylesheet_continue) == 1
    assert runner_source.count(font_continue) == 1
    fixture_stylesheet = (
        "@font-face{font-family:'Kalam';src:url('https://fonts.gstatic.com/fixture.ttf') format('truetype')}"
        "@font-face{font-family:'Baloo 2';src:url('https://fonts.gstatic.com/fixture.ttf') format('truetype')}"
        "@font-face{font-family:'Open Sans';src:url('https://fonts.gstatic.com/fixture.ttf') format('truetype')}"
    )
    runner_source = runner_source.replace(
        stylesheet_continue,
        """if (requestUrl.hostname === 'fonts.googleapis.com' &&
            requestUrl.pathname === '/css2' &&
            requestType === 'stylesheet') {
          return route.fulfill({
            status: 200,
            contentType: 'text/css',
            body: %s,
          });
        }""" % json.dumps(fixture_stylesheet),
        1,
    )
    runner_source = runner_source.replace(
        font_continue,
        """if (requestUrl.hostname === 'fonts.gstatic.com' && requestType === 'font') {
          return brandGuardFontGate.then(() => route.fulfill({
            status: 200,
            contentType: 'font/ttf',
            headers: { 'access-control-allow-origin': '*' },
            body: Buffer.from(%s, 'base64'),
          }));
        }""" % json.dumps(font_data),
        1,
    )
    (scripts / "responsive-qa.mjs").write_text(runner_source, encoding="utf-8")
    _copy_responsive_qa_dependencies(scripts / "responsive-qa.mjs")
    (fixture_root / "sitemap.xml").write_text(sitemap, encoding="utf-8")
    _ensure_sitemap_fixture_pages(fixture_root)
    for path in route_paths:
        page = fixture_root / path.lstrip("/") / "index.html"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text("<!doctype html><html><body>Fixture</body></html>", encoding="utf-8")

    try:
        result = subprocess.run(
            [
                node_bin,
                "scripts/responsive-qa.mjs",
                f"--base=http://127.0.0.1:{server.server_address[1]}",
            ],
            cwd=fixture_root,
            text=True,
            capture_output=True,
            timeout=180,
            env={**os.environ, "NODE_PATH": node_modules},
        )
        report = json.loads(
            (fixture_root / "assets/audit/responsive-qa/results.json").read_text(
                encoding="utf-8"
            )
        )
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)

    assert result.returncode == 1, result.stdout + result.stderr
    edge_rows = [
        row for row in report["results"]
        if row["viewport"] in {"tablet-769-edge", "tablet-900-edge"}
    ]
    assert len(edge_rows) == len(route_paths) * 2
    assert {urlsplit(row["url"]).path for row in edge_rows} == set(route_paths)
    selectors = (
        ".askjamie-main .askjamie-breadcrumb",
        ".askjamie-main .askjamie-hero-copy h1",
        ".askjamie-main .askjamie-hero-copy .hero-subtitle",
        ".askjamie-main .askjamie-hero-copy .hero-tagline",
    )
    for row in edge_rows:
        evidence = row["hero_font_geometry"]
        assert evidence["font_geometry_tolerance_px"] == 1
        assert evidence["fallback_font_geometry"] == evidence["before"]
        assert evidence["loaded_font_geometry"] == evidence["after"]
        assert evidence["fallback_font_geometry"]
        assert evidence["loaded_font_geometry"]
        missing_heading_case = (
            row["viewport"] == "tablet-769-edge"
            and urlsplit(row["url"]).path == route_paths[1]
        )
        heading_selector = ".askjamie-main .askjamie-hero-copy h1"
        if missing_heading_case:
            assert any(
                f"selector={heading_selector}" in error
                and f"route={route_paths[1]}" in error
                for error in row["errors"]
            ), row["errors"]
            assert evidence["fallback_font_geometry"][heading_selector] is None
            assert evidence["loaded_font_geometry"][heading_selector] is None
            assert all(
                evidence["fallback_font_geometry"][selector]
                and evidence["loaded_font_geometry"][selector]
                for selector in selectors
                if selector != heading_selector
            )
            assert all(
                font["loaded"] or font.get("reason") == "element missing"
                for font in evidence["font_loading"]["fonts"]
            )
        else:
            assert all(evidence["fallback_font_geometry"][selector] for selector in selectors)
            assert all(evidence["loaded_font_geometry"][selector] for selector in selectors)
            assert all(font["loaded"] for font in evidence["font_loading"]["fonts"])
        deltas = evidence["font_geometry_deltas"]
        assert [item["selector"] for item in deltas] == list(selectors)
        assert all(
            item["delta"] is None or set(item["delta"]) == {"x", "y", "width", "height"}
            for item in deltas
        )


def test_brandguard_font_geometry_deltas_include_zero_small_and_over_tolerance_changes():
    source = (ROOT / "scripts/responsive-qa.mjs").read_text(encoding="utf-8")
    helper = source[
        source.index("function compareBrandGuardFontGeometry("):
        source.index("\nfunction compareBrandGuardTextLineReflow")
    ]
    selectors = [
        ".askjamie-main .askjamie-breadcrumb",
        ".askjamie-main .askjamie-hero-copy h1",
        ".askjamie-main .askjamie-hero-copy .hero-subtitle",
        ".askjamie-main .askjamie-hero-copy .hero-tagline",
    ]
    before = {
        selector: {"x": 10, "y": 20, "width": 300, "height": 40}
        for selector in selectors
    }
    after = {
        selectors[0]: {"x": 10, "y": 20, "width": 300, "height": 40},
        selectors[1]: {"x": 10.5, "y": 20, "width": 300, "height": 40},
        selectors[2]: {"x": 8.75, "y": 20, "width": 300, "height": 40},
        selectors[3]: {"x": 10, "y": 18.99, "width": 300, "height": 40},
    }
    node_test = f"""
const vm = require('node:vm');
const context = vm.createContext({{
  BRANDGUARD_GEOMETRY_SELECTORS: {json.dumps(selectors)},
  BRANDGUARD_FONT_GEOMETRY_TOLERANCE_PX: 1,
}});
vm.runInContext({json.dumps(helper)}, context);
const compare = vm.runInContext('compareBrandGuardFontGeometry', context);
console.log(JSON.stringify(compare({json.dumps(before)}, {json.dumps(after)})));
"""
    result = subprocess.run(
        ["node", "-e", node_test], cwd=ROOT, text=True, capture_output=True, check=True
    )
    deltas = json.loads(result.stdout)

    assert [item["selector"] for item in deltas] == selectors
    assert deltas[0]["delta"] == {"x": 0, "y": 0, "width": 0, "height": 0}
    assert deltas[0]["changed_properties"] == []
    assert deltas[1]["delta"]["x"] == 0.5
    assert deltas[1]["changed_properties"] == []
    assert deltas[2]["delta"]["x"] == -1.25
    assert deltas[2]["changed_properties"] == ["x"]
    assert deltas[3]["delta"]["y"] == -1.01
    assert deltas[3]["changed_properties"] == ["y"]


def test_responsive_qa_reports_missing_tablet_font_baseline_without_comparison(tmp_path):
    node_bin = os.environ.get("ASKJAMIE_NODE") or shutil.which("node")
    node_modules = os.environ.get("NODE_PATH")
    repo_modules = ROOT / "node_modules"
    if not node_modules and (repo_modules / "playwright").exists():
        node_modules = str(repo_modules)
    bundled_modules = Path(
        "/Users/okh/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules"
    )
    if not node_modules and (bundled_modules / "playwright").exists():
        node_modules = str(bundled_modules)
    if not node_bin or not node_modules or not (Path(node_modules) / "playwright").exists():
        pytest.skip("Playwright runtime is unavailable")

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, _format, *_args):
            return

        def do_GET(self):
            if self.path == "/assets/css/theme.css":
                payload = b".askjamie-main { display: block; }"
                content_type = "text/css"
            else:
                payload = (
                    '<!doctype html><html><head><meta name="viewport" '
                    'content="width=device-width"><title>Font baseline fixture</title>'
                    '<link rel="stylesheet" href="/assets/css/theme.css"></head>'
                    '<body class="askjamie-main"><nav class="askjamie-breadcrumb">'
                    'Home</nav><main class="askjamie-hero-copy"><h1>Fixture heading</h1>'
                    '<p class="hero-subtitle">Fixture subtitle</p>'
                    '<p class="hero-tagline">Fixture tagline</p></main></body></html>'
                ).encode("utf-8")
                content_type = "text/html"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    fixture_root = tmp_path / "runner"
    scripts = fixture_root / "scripts"
    scripts.mkdir(parents=True)
    runner_source = (ROOT / "scripts/responsive-qa.mjs").read_text(encoding="utf-8")
    timeout_setting = "const BRANDGUARD_FONT_GEOMETRY_TIMEOUT_MS = 15000;"
    assert timeout_setting in runner_source
    runner_source = runner_source.replace(
        timeout_setting,
        "const BRANDGUARD_FONT_GEOMETRY_TIMEOUT_MS = 100;",
        1,
    )
    (scripts / "responsive-qa.mjs").write_text(runner_source, encoding="utf-8")
    _copy_responsive_qa_dependencies(scripts / "responsive-qa.mjs")
    (fixture_root / "sitemap.xml").write_text(
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        '<url><loc>https://askjamie.bot/lens-system/okhp3-brandguard/</loc></url>'
        "</urlset>",
        encoding="utf-8",
    )
    _ensure_sitemap_fixture_pages(fixture_root)
    try:
        result = subprocess.run(
            [
                node_bin,
                "scripts/responsive-qa.mjs",
                f"--base=http://127.0.0.1:{server.server_address[1]}",
            ],
            cwd=fixture_root,
            text=True,
            capture_output=True,
            timeout=90,
            env={**os.environ, "NODE_PATH": node_modules},
        )
        report = json.loads(
            (fixture_root / "assets/audit/responsive-qa/results.json").read_text(
                encoding="utf-8"
            )
        )
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)

    assert result.returncode == 1, result.stdout + result.stderr
    tablet_rows = [
        row for row in report["results"]
        if row["viewport"] in {"tablet-768", "tablet-769-edge", "tablet-899"}
    ]
    assert len(tablet_rows) == 3
    for row in tablet_rows:
        unavailable = [
            error for error in row["errors"]
            if "BRANDGUARD TABLET WEB FONT BASELINE UNAVAILABLE" in error
        ]
        assert len(unavailable) == 1, row
        evidence = row["hero_font_geometry"]
        assert evidence["baseline_status"] == "unavailable"
        assert evidence["baseline_scope"] == "tablet"
        assert evidence["comparison"] == "not-performed-reservation-unavailable"
        assert evidence["viewport_width_px"] == row["width"]
        assert evidence["before"] is None
        assert evidence["after"] is None
        assert evidence["shifts"] == []
        if row["viewport"] == "tablet-769-edge":
            assert evidence["fallback_font_geometry"] is None
            assert evidence["loaded_font_geometry"] is None
            assert evidence["font_geometry_tolerance_px"] == 1
            assert [item["selector"] for item in evidence["font_geometry_deltas"]] == [
                ".askjamie-main .askjamie-breadcrumb",
                ".askjamie-main .askjamie-hero-copy h1",
                ".askjamie-main .askjamie-hero-copy .hero-subtitle",
                ".askjamie-main .askjamie-hero-copy .hero-tagline",
            ]
            assert all(item["delta"] is None for item in evidence["font_geometry_deltas"])


def test_responsive_qa_waits_for_delayed_tablet_font_reservation(tmp_path):
    node_bin = os.environ.get("ASKJAMIE_NODE") or shutil.which("node")
    node_modules = os.environ.get("NODE_PATH")
    repo_modules = ROOT / "node_modules"
    if not node_modules and (repo_modules / "playwright").exists():
        node_modules = str(repo_modules)
    bundled_modules = Path(
        "/Users/okh/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules"
    )
    if not node_modules and (bundled_modules / "playwright").exists():
        node_modules = str(bundled_modules)
    if not node_bin or not node_modules or not (Path(node_modules) / "playwright").exists():
        pytest.skip("Playwright runtime is unavailable")

    font_candidates = sorted(
        (Path(node_modules) / "playwright-core/lib/vite").glob("**/*.ttf")
    )
    if not font_candidates:
        pytest.skip("Playwright's bundled fixture font is unavailable")
    font_data = base64.b64encode(font_candidates[0].read_bytes()).decode("ascii")

    events = []
    events_lock = threading.Lock()
    delayed_reservation_wait_started = threading.Event()
    delayed_font_request_started = threading.Event()
    reservation_coordination_timed_out = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, _format, *_args):
            return

        def do_GET(self):
            request_url = urlsplit(self.path)
            if request_url.path == "/fixture-event":
                query = parse_qs(request_url.query)
                event = query["event"][0]
                width = int(query["width"][0])
                if event == "reservation-wait-started" and width == 769:
                    delayed_reservation_wait_started.set()
                if event == "font-request-started" and width == 769:
                    delayed_font_request_started.set()
                if event == "reservation-ready" and width == 899:
                    if not (
                        delayed_reservation_wait_started.wait(timeout=5) and
                        delayed_font_request_started.wait(timeout=5)
                    ):
                        reservation_coordination_timed_out.set()
                with events_lock:
                    events.append({"event": event, "width": width})
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if request_url.path == "/assets/css/theme.css":
                payload = b".askjamie-main { display: block; }"
                content_type = "text/css"
            else:
                payload = (
                    '<!doctype html><html><head><meta name="viewport" '
                    'content="width=device-width"><title>Delayed reservation fixture</title>'
                    '<link rel="stylesheet" href="/assets/css/theme.css">'
                    '<link rel="stylesheet" '
                    'href="https://fonts.googleapis.com/css2?family=Fixture">'
                    '<link rel="preload" href="https://fonts.gstatic.com/fixture.ttf" '
                    'as="font" type="font/ttf" crossorigin>'
                    '<style>'
                    '.askjamie-main { margin: 0; }'
                    '.askjamie-breadcrumb { font: 400 16px Kalam; width: 700px; height: 24px; }'
                    '.askjamie-hero-copy { width: 700px; }'
                    '.askjamie-hero-copy h1 { font: 400 24px "Baloo 2"; height: 36px; }'
                    '.askjamie-hero-copy .hero-subtitle { font: 400 18px Kalam; height: 28px; }'
                    '.askjamie-hero-copy .hero-tagline { font: 400 16px "Open Sans"; height: 24px; }'
                    '</style></head><body class="askjamie-main">'
                    '<nav class="askjamie-breadcrumb">Fixture breadcrumb</nav>'
                    '<main class="askjamie-hero-copy"><h1>Fixture heading</h1>'
                    '<p class="hero-subtitle">Fixture subtitle</p>'
                    '<p class="hero-tagline">Fixture tagline</p></main>'
                    '<script>window.fixtureRecordEvent = async event => {'
                    'const query = new URLSearchParams({'
                    'event, width: String(window.innerWidth)'
                    '});'
                    'await fetch(`/fixture-event?${query}`, { cache: "no-store" });'
                    '};'
                    'window.addEventListener("fixture:reservation-wait-started", () => {'
                    'const reserve = async () => {'
                    'await window.fixtureRecordEvent("reservation-ready");'
                    'document.querySelector(".askjamie-hero-copy")'
                    '.setAttribute("data-font-geometry-reserved", "true");'
                    '};'
                    'if (window.innerWidth === 769) setTimeout(reserve, 1800);'
                    'else void reserve();'
                    '}, { once: true });</script></body></html>'
                ).encode("utf-8")
                content_type = "text/html"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    fixture_root = tmp_path / "runner"
    scripts = fixture_root / "scripts"
    scripts.mkdir(parents=True)
    runner_source = (ROOT / "scripts/responsive-qa.mjs").read_text(encoding="utf-8")
    viewports_match = re.search(
        r"const VIEWPORTS = \[.*?\n\];",
        runner_source,
        re.DOTALL,
    )
    assert viewports_match
    runner_source = (
        runner_source[:viewports_match.start()]
        + """const VIEWPORTS = [
  { name: 'tablet-769-edge', width: 769, height: 1024 },
  { name: 'tablet-899', width: 899, height: 1024 },
];"""
        + runner_source[viewports_match.end():]
    )
    timeout_setting = "const BRANDGUARD_FONT_GEOMETRY_TIMEOUT_MS = 15000;"
    assert timeout_setting in runner_source
    runner_source = runner_source.replace(
        timeout_setting,
        "const BRANDGUARD_FONT_GEOMETRY_TIMEOUT_MS = 3000;",
        1,
    )
    reservation_wait = """  if (reservationRequired) {
    try {"""
    assert runner_source.count(reservation_wait) == 1
    runner_source = runner_source.replace(
        reservation_wait,
        """  if (reservationRequired) {
    await page.evaluate(async () => {
      await window.fixtureRecordEvent('reservation-wait-started');
      window.dispatchEvent(new Event('fixture:reservation-wait-started'));
    });
    try {""",
        1,
    )
    baseline_capture = """  const before = await capture();
  fontRequestsBeforeBaseline = [...fontRequests];"""
    assert runner_source.count(baseline_capture) == 1
    runner_source = runner_source.replace(
        baseline_capture,
        """  const before = await capture();
  await page.evaluate(() => window.fixtureRecordEvent('fallback-captured'));
  fontRequestsBeforeBaseline = [...fontRequests];""",
        1,
    )
    baseline_release = """  releaseFontRequests();
  fontRequestGateReleasedAfterBaseline = true;"""
    assert runner_source.count(baseline_release) == 1
    runner_source = runner_source.replace(
        baseline_release,
        """  releaseFontRequests();
  await page.evaluate(() => window.fixtureRecordEvent('font-gate-released'));
  fontRequestGateReleasedAfterBaseline = true;""",
        1,
    )
    stylesheet_continue = """if (requestUrl.hostname === 'fonts.googleapis.com' &&
            requestUrl.pathname === '/css2' &&
            requestType === 'stylesheet') {
          return route.continue();
        }"""
    font_continue = """if (requestUrl.hostname === 'fonts.gstatic.com' && requestType === 'font') {
          return brandGuardFontGate.then(() => route.continue());
        }"""
    assert runner_source.count(stylesheet_continue) == 1
    assert runner_source.count(font_continue) == 1
    fixture_stylesheet = (
        "@font-face{font-family:'Kalam';src:url('https://fonts.gstatic.com/fixture.ttf') format('truetype')}"
        "@font-face{font-family:'Baloo 2';src:url('https://fonts.gstatic.com/fixture.ttf') format('truetype')}"
        "@font-face{font-family:'Open Sans';src:url('https://fonts.gstatic.com/fixture.ttf') format('truetype')}"
    )
    runner_source = runner_source.replace(
        stylesheet_continue,
        """if (requestUrl.hostname === 'fonts.googleapis.com' &&
            requestUrl.pathname === '/css2' &&
            requestType === 'stylesheet') {
          return route.fulfill({
            status: 200,
            contentType: 'text/css',
            body: %s,
          });
        }""" % json.dumps(fixture_stylesheet),
        1,
    )
    runner_source = runner_source.replace(
        font_continue,
        """if (requestUrl.hostname === 'fonts.gstatic.com' && requestType === 'font') {
          return page.waitForFunction(() =>
            typeof window.fixtureRecordEvent === 'function'
          ).then(() => page.evaluate(() =>
            window.fixtureRecordEvent('font-request-started')
          )).then(() => brandGuardFontGate).then(() => route.fulfill({
            status: 200,
            contentType: 'font/ttf',
            headers: { 'access-control-allow-origin': '*' },
            body: Buffer.from(%s, 'base64'),
          }));
        }""" % json.dumps(font_data),
        1,
    )
    (scripts / "responsive-qa.mjs").write_text(runner_source, encoding="utf-8")
    _copy_responsive_qa_dependencies(scripts / "responsive-qa.mjs")
    (fixture_root / "sitemap.xml").write_text(
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        '<url><loc>https://askjamie.bot/lens-system/okhp3-brandguard/</loc></url>'
        "</urlset>",
        encoding="utf-8",
    )
    _ensure_sitemap_fixture_pages(fixture_root)

    try:
        result = subprocess.run(
            [
                node_bin,
                "scripts/responsive-qa.mjs",
                f"--base=http://127.0.0.1:{server.server_address[1]}",
            ],
            cwd=fixture_root,
            text=True,
            capture_output=True,
            timeout=90,
            env={**os.environ, "NODE_PATH": node_modules},
        )
        report = json.loads(
            (fixture_root / "assets/audit/responsive-qa/results.json").read_text(
                encoding="utf-8"
            )
        )
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)

    assert result.returncode == 0, result.stdout + result.stderr
    assert report["status"] == "complete"
    assert not reservation_coordination_timed_out.is_set()
    tablet_rows = [
        row for row in report["results"]
        if row["viewport"] in {"tablet-769-edge", "tablet-899"}
    ]
    assert len(tablet_rows) == 2
    rows_by_width = {row["width"]: row for row in tablet_rows}
    assert set(rows_by_width) == {769, 899}
    for row in tablet_rows:
        evidence = row["hero_font_geometry"]
        assert evidence["timeout_ms"] == 3000
        assert evidence["reservation_present_at_baseline"] is True
        assert evidence["comparison"] == "performed"
        assert evidence["before"]
        assert evidence["after"]
        gate = evidence["font_request_gate"]
        assert gate["held_requests_before_baseline"]
        assert gate["font_responses_before_baseline"] == []
        assert gate["released_after_baseline"] is True

    delayed = rows_by_width[769]["hero_font_geometry"]
    ready = rows_by_width[899]["hero_font_geometry"]
    assert delayed["before"] == delayed["fallback_font_geometry"]
    assert delayed["after"] == delayed["loaded_font_geometry"]
    assert 1500 <= delayed["reservation_wait_ms"] < delayed["timeout_ms"]
    assert ready["reservation_wait_ms"] < 1000

    def event_index(event, width):
        return next(
            index for index, item in enumerate(events)
            if item == {"event": event, "width": width}
        )

    delayed_wait_started = event_index("reservation-wait-started", 769)
    delayed_font_request = event_index("font-request-started", 769)
    ready_font_request = event_index("font-request-started", 899)
    ready_captured = event_index("fallback-captured", 899)
    ready_released = event_index("font-gate-released", 899)
    delayed_reserved = event_index("reservation-ready", 769)
    delayed_captured = event_index("fallback-captured", 769)
    delayed_released = event_index("font-gate-released", 769)
    assert delayed_wait_started < ready_released < delayed_reserved
    assert delayed_font_request < ready_released
    assert ready_font_request < ready_released
    assert ready_captured < ready_released
    assert delayed_reserved < delayed_captured < delayed_released


@pytest.mark.parametrize("failure_mode", ["http", "network", "stylesheet-http"])
def test_responsive_qa_does_not_treat_tablet_fallback_as_loaded_font_stability(
    tmp_path, failure_mode
):
    node_bin = os.environ.get("ASKJAMIE_NODE") or shutil.which("node")
    node_modules = os.environ.get("NODE_PATH")
    repo_modules = ROOT / "node_modules"
    if not node_modules and (repo_modules / "playwright").exists():
        node_modules = str(repo_modules)
    bundled_modules = Path(
        "/Users/okh/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules"
    )
    if not node_modules and (bundled_modules / "playwright").exists():
        node_modules = str(bundled_modules)
    if not node_bin or not node_modules or not (Path(node_modules) / "playwright").exists():
        pytest.skip("Playwright runtime is unavailable")

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, _format, *_args):
            return

        def do_GET(self):
            if self.path.split("?", 1)[0] == "/assets/css/theme.css":
                payload = b".askjamie-main { display: block; }"
                content_type = "text/css"
            else:
                payload = (
                    '<!doctype html><html><head><meta name="viewport" '
                    'content="width=device-width"><title>Failed tablet font fixture</title>'
                    '<link rel="stylesheet" href="/assets/css/theme.css">'
                    '<link rel="stylesheet" '
                    'href="https://fonts.googleapis.com/css2?family=Fixture">'
                    '<style>'
                    '.askjamie-main { margin: 0; }'
                    '.askjamie-breadcrumb { font: 400 16px Kalam; width: 700px; height: 24px; }'
                    '.askjamie-hero-copy { width: 700px; }'
                    '.askjamie-hero-copy h1 { font: 400 24px "Baloo 2"; height: 36px; }'
                    '.askjamie-hero-copy .hero-subtitle { font: 400 18px Kalam; height: 28px; }'
                    '.askjamie-hero-copy .hero-tagline { font: 400 16px "Open Sans"; height: 24px; }'
                    '</style></head><body class="askjamie-main">'
                    '<nav class="askjamie-breadcrumb">Fixture breadcrumb</nav>'
                    '<main class="askjamie-hero-copy" data-font-geometry-reserved="true">'
                    '<h1>Fixture heading</h1>'
                    '<p class="hero-subtitle">Fixture subtitle</p>'
                    '<p class="hero-tagline">Fixture tagline</p>'
                    '</main></body></html>'
                ).encode("utf-8")
                content_type = "text/html"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    fixture_root = tmp_path / "runner"
    scripts = fixture_root / "scripts"
    scripts.mkdir(parents=True)
    runner_source = (ROOT / "scripts/responsive-qa.mjs").read_text(encoding="utf-8")
    font_timeout_setting = "const BRANDGUARD_FONT_GEOMETRY_TIMEOUT_MS = 15000;"
    assert font_timeout_setting in runner_source
    runner_source = runner_source.replace(
        font_timeout_setting,
        "const BRANDGUARD_FONT_GEOMETRY_TIMEOUT_MS = 500;",
        1,
    )
    viewports_match = re.search(
        r"const VIEWPORTS = \[.*?\n\];",
        runner_source,
        re.DOTALL,
    )
    assert viewports_match
    runner_source = (
        runner_source[:viewports_match.start()]
        + """const VIEWPORTS = [
  { name: 'tablet-769-edge', width: 769, height: 1024 },
];"""
        + runner_source[viewports_match.end():]
    )
    stylesheet_continue = """if (requestUrl.hostname === 'fonts.googleapis.com' &&
            requestUrl.pathname === '/css2' &&
            requestType === 'stylesheet') {
          return route.continue();
        }"""
    font_continue = """if (requestUrl.hostname === 'fonts.gstatic.com' && requestType === 'font') {
          return brandGuardFontGate.then(() => route.continue());
        }"""
    assert runner_source.count(stylesheet_continue) == 1
    assert runner_source.count(font_continue) == 1
    fixture_stylesheet = (
        "@font-face{font-family:'Kalam';src:url('https://fonts.gstatic.com/fixture.ttf') format('truetype')}"
        "@font-face{font-family:'Baloo 2';src:url('https://fonts.gstatic.com/fixture.ttf') format('truetype')}"
        "@font-face{font-family:'Open Sans';src:url('https://fonts.gstatic.com/fixture.ttf') format('truetype')}"
    )
    if failure_mode == "stylesheet-http":
        stylesheet_route = """if (requestUrl.hostname === 'fonts.googleapis.com' &&
            requestUrl.pathname === '/css2' &&
            requestType === 'stylesheet') {
          return route.fulfill({
            status: 503,
            contentType: 'text/plain',
            body: 'fixture stylesheet unavailable',
          });
        }"""
    else:
        stylesheet_route = """if (requestUrl.hostname === 'fonts.googleapis.com' &&
            requestUrl.pathname === '/css2' &&
            requestType === 'stylesheet') {
          return route.fulfill({
            status: 200,
            contentType: 'text/css',
            body: %s,
          });
        }""" % json.dumps(fixture_stylesheet)
    runner_source = runner_source.replace(stylesheet_continue, stylesheet_route, 1)
    if failure_mode == "network":
        font_failure_route = """if (requestUrl.hostname === 'fonts.gstatic.com' && requestType === 'font') {
          return brandGuardFontGate.then(() => route.abort('failed'));
        }"""
    elif failure_mode == "http":
        font_failure_route = """if (requestUrl.hostname === 'fonts.gstatic.com' && requestType === 'font') {
          return brandGuardFontGate.then(() => route.fulfill({
            status: 503,
            contentType: 'text/plain',
            body: 'fixture font unavailable',
          }));
        }"""
        runner_source = runner_source.replace(font_continue, font_failure_route, 1)
    (scripts / "responsive-qa.mjs").write_text(runner_source, encoding="utf-8")
    _copy_responsive_qa_dependencies(scripts / "responsive-qa.mjs")
    (fixture_root / "sitemap.xml").write_text(
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        '<url><loc>https://askjamie.bot/lens-system/okhp3-brandguard/</loc></url>'
        "</urlset>",
        encoding="utf-8",
    )
    _ensure_sitemap_fixture_pages(fixture_root)

    try:
        result = subprocess.run(
            [
                node_bin,
                "scripts/responsive-qa.mjs",
                f"--base=http://127.0.0.1:{server.server_address[1]}",
            ],
            cwd=fixture_root,
            text=True,
            capture_output=True,
            timeout=90,
            env={**os.environ, "NODE_PATH": node_modules},
        )
        report = json.loads(
            (fixture_root / "assets/audit/responsive-qa/results.json").read_text(
                encoding="utf-8"
            )
        )
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)

    assert result.returncode == 1, result.stdout + result.stderr
    tablet_rows = [
        row for row in report["results"]
        if row["viewport"] == "tablet-769-edge"
    ]
    assert len(tablet_rows) == 1
    row = tablet_rows[0]
    evidence = row["hero_font_geometry"]
    assert evidence["reservation_present_at_baseline"] is True
    assert evidence["fallback_font_geometry"]
    assert evidence["loaded_font_geometry"] is None
    assert evidence["font_evidence_status"] == "unavailable"
    assert evidence["comparison"] == "not-performed-fonts-unavailable"
    assert evidence["measurement_state"] == "fallback-only-fonts-unavailable"
    failures = evidence["font_resource_failures"]
    if failure_mode == "stylesheet-http":
        stylesheet_failure = next(
            failure for failure in failures
            if failure["host"] == "fonts.googleapis.com"
            and failure["path"] == "/css2"
            and failure["resource_type"] == "stylesheet"
        )
        assert stylesheet_failure["failure_kind"] == "http"
        assert stylesheet_failure["status"] == 503
    else:
        font_failure = next(
            failure for failure in failures
            if failure["host"] == "fonts.gstatic.com"
            and failure["path"] == "/fixture.ttf"
            and failure["resource_type"] == "font"
        )
        if failure_mode == "network":
            assert font_failure["failure_kind"] == "network"
            assert font_failure["error"]
            assert "status" not in font_failure
        else:
            assert font_failure["failure_kind"] == "http"
            assert font_failure["status"] == 503
    assert evidence["shifts"] == []
    assert any("BRANDGUARD WEB FONTS UNAVAILABLE" in error for error in row["errors"])
    assert not any("WEB FONT GEOMETRY SHIFT" in error for error in row["errors"])


def test_responsive_qa_marks_narrow_font_subreports_unavailable_without_skipping_readability():
    source = (ROOT / "scripts/responsive-qa.mjs").read_text(encoding="utf-8")
    start = source.index("async function checkBrandGuardWebFontGeometry")
    end = source.index("\nasync function checkBrandGuardNarrowContent", start)
    font_check = source[start:end]

    assert "const afterFontStateName = fontEvidenceAvailable" in font_check
    navigation_check = font_check[
        font_check.index("const headerNavigationAfter ="):
        font_check.index("const afterTextLayout =")
    ]
    assert "validateNarrowHeaderNavigation(" in navigation_check
    assert "afterFontStateName" in navigation_check
    assert "if (includeHeaderNavigation && fontEvidenceAvailable)" in navigation_check
    assert "const headerNavigationShifts = fontEvidenceAvailable ? [] : null;" in navigation_check
    assert "const headerNavigationLineChanges = fontEvidenceAvailable ? [] : null;" in navigation_check

    notice_check = font_check[
        font_check.index("const transitionNoticeGeometryShifts ="):
        font_check.index("const lineCountChanges =")
    ]
    assert "const transitionNoticeGeometryShifts = fontEvidenceAvailable ? [] : null;" in notice_check
    assert "} else if (fontEvidenceAvailable) {" in notice_check
    assert "['fallback-font state', beforeTransitionNotice]" in notice_check
    assert "[afterFontStateName, afterTransitionNotice]" in notice_check
    assert "ARRIVAL NOTICE TEXT NOT READABLE (${stage})" in notice_check

    action_check = font_check[
        font_check.index("const heroActionGeometryShifts ="):
        font_check.index("return {", font_check.index("const heroActionGeometryShifts ="))
    ]
    assert "const heroActionGeometryShifts = fontEvidenceAvailable ? [] : null;" in action_check
    assert "validateHeroActionState('fallback-font state', beforeHeroActions);" in action_check
    assert "validateHeroActionState(afterFontStateName, afterHeroActions);" in action_check
    assert action_check.index("validateHeroActionState(afterFontStateName") < action_check.index(
        "if (fontEvidenceAvailable)"
    )

    assert "'fallback-readability-checked-font-swap-unavailable'" in font_check
    assert "'not-performed-fonts-unavailable'" in font_check
    assert "'performed-font-resources-unverified'" not in font_check


def test_responsive_qa_measures_every_universe_group_without_parallel_renders():
    source = (ROOT / "scripts/responsive-qa.mjs").read_text(encoding="utf-8")
    start = source.index("async function checkUniversePageMapGroupsGeometry")
    # Keep this assertion scoped to the sequential geometry loop. Later font
    # rotation helpers read and restore independent scroll positions in parallel.
    end = source.index("\nasync function checkUniversePageMapFontPendingRotation", start)
    group_check = source[start:end]

    assert "for (let groupIndex = 0; groupIndex < groupCount; groupIndex += 1)" in group_check
    assert "await releaseResponsiveQaMermaidRender(page)" in group_check
    assert "await page.waitForFunction((index)" in group_check
    assert "compareUniversePageMapGeometry(page, groupCheck, after)" in group_check
    assert "other_groups_stayed_collapsed" in group_check
    assert "group_index: groupIndex + 1" in group_check
    assert "Promise.all" not in group_check
    assert "await checkUniversePageMapGroupsGeometry(page, waitForTwoFrames)" in source


def test_responsive_qa_visits_and_reports_every_generated_universe_destination():
    source = (ROOT / "scripts/responsive-qa.mjs").read_text(encoding="utf-8")
    start = source.index("const checkGeneratedPageMapFallback = async targetPage =>")
    end = source.index(
        "\n  const checkGeneratedPageMapWithoutJavaScript",
        start,
    )
    fallback_check = source[start:end]

    assert "for (let linkIndex = 0; linkIndex < state.links.length; linkIndex += 1)" in fallback_check
    assert "ordinaryPageLink.click({" in fallback_check
    assert "modifiers: ['ControlOrMeta']" in fallback_check
    assert "context.on('response', onResponse)" in fallback_check
    assert "context.on('requestfailed', onRequestFailed)" in fallback_check
    assert "request.isNavigationRequest()" in fallback_check
    assert "initialRequest.redirectedFrom()" in fallback_check
    assert "initialRequestUrl.href === expectedResponseUrl.href" in fallback_check
    assert "if (!isRedirect)" in fallback_check
    assert "navigation.request.failure()?.errorText" in fallback_check
    assert "cancelNavigationWait();" in fallback_check
    assert "await openedPage.waitForURL(" in fallback_check
    assert "url.pathname === destination.pathname" in fallback_check
    assert fallback_check.index("await openedPage.waitForURL(") < fallback_check.index(
        "openedPage.evaluate"
    )
    assert "openedPage.url()" in fallback_check
    assert "reachedUrl.pathname === destination.pathname" in fallback_check
    assert "response.status() < 400" in fallback_check
    assert "reached_title: null" in fallback_check
    assert "reached_heading: null" in fallback_check
    assert "failed_stage: 'destination_validation'" in fallback_check
    assert "currentStage = 'tab_open'" in fallback_check
    assert "currentStage = 'navigation_response'" in fallback_check
    assert "currentStage = 'expected_url'" in fallback_check
    assert "currentStage = 'document_read'" in fallback_check
    assert "result.http_status = response?.status() ?? null;" in fallback_check
    assert "result.failed_stage = currentStage;" in fallback_check
    assert "result.failed_stage = 'http_response';" in fallback_check
    assert "result.failed_stage = 'document_validation';" in fallback_check
    assert "result.reached_title = reachedDocument.title" in fallback_check
    assert "result.reached_heading = reachedDocument.heading" in fallback_check
    assert "result.reached_title = reachedDocument?.title ?? null" in fallback_check
    assert "result.reached_heading = reachedDocument?.heading ?? null" in fallback_check
    assert "'title unavailable'" in fallback_check
    assert "`reached document ${reachedPageIdentity}, `" in fallback_check
    assert "result.not_found_page = isNotFoundDocument(" in fallback_check
    assert "destination rendered a not-found page" in fallback_check
    assert "!result.not_found_page" in fallback_check
    assert "all_group_destinations_navigated" in fallback_check
    assert "(failed stage ${result.failed_stage}); " in fallback_check
    assert "destination_sweep_duration_ms: groupDestinationSweepDurationMs" in fallback_check
    assert "destination_count: destinationCount" in fallback_check
    assert "destinations: destinationEvidence" in fallback_check
    assert "${groupLabel}, " in fallback_check
    assert "`link ${linkIndex + 1} \"${label}\"" in fallback_check
    assert "expected_path: destination.pathname" in fallback_check
    assert "targetPage.goto(url" not in fallback_check


def test_page_map_destination_sweep_scaling_guard_uses_controlled_sample_counts():
    script = r"""
import { assessDestinationSweepScaling } from './scripts/page-map-sweep-performance.mjs';
const stable = Array.from({ length: 24 }, (_, index) => 75 + (index % 3) * 4);
const oneOutlier = Array.from({ length: 15 }, (_, index) =>
  index === 14 ? 1200 : 80 + (index % 2) * 5
);
const regressed = Array.from({ length: 30 }, (_, index) =>
  index < 20 ? 40 + (index % 3) * 2 : 160 + (index % 3) * 4
);
const belowMinimum = assessDestinationSweepScaling(Array(8).fill(200));
console.log(JSON.stringify({
  stable: assessDestinationSweepScaling(stable),
  oneOutlier: assessDestinationSweepScaling(oneOutlier),
  regressed: assessDestinationSweepScaling(regressed),
  belowMinimum,
}));
"""

    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=15,
    )

    assert result.returncode == 0, result.stderr
    evidence = json.loads(result.stdout)
    assert evidence["stable"]["checked"] is True
    assert evidence["stable"]["slowed"] is False
    assert evidence["oneOutlier"]["slowed"] is False
    assert evidence["regressed"]["slowed"] is True
    assert evidence["regressed"]["destination_count"] == 30
    assert evidence["regressed"]["early_median_ms"] < evidence["regressed"]["late_median_ms"]
    assert evidence["belowMinimum"]["status"] == "insufficient_destinations"
    assert evidence["belowMinimum"]["destination_count"] == 8

    source = (ROOT / "scripts/responsive-qa.mjs").read_text(encoding="utf-8")
    start = source.index("const checkGeneratedPageMapFallback = async targetPage =>")
    end = source.index(
        "\n  const checkGeneratedPageMapWithoutJavaScript",
        start,
    )
    fallback_check = source[start:end]
    assert (
        "destinationDurationsMs.push(performance.now() - destinationStartedAt)"
        in fallback_check
    )
    assert "assessDestinationSweepScaling(destinationDurationsMs)" in fallback_check
    assert "destinationSweepScaling.destination_count} destinations" in fallback_check
    assert "destination_sweep_scaling: destinationSweepScaling" in fallback_check


def test_page_map_sweep_history_detects_only_sustained_cross_run_slowdowns():
    script = r"""
import assert from 'node:assert/strict';
import {
  appendPageMapSweepHistory,
  assessPageMapSweepHistory,
  isValidPageMapSweepHistory,
  summarizePageMapSweepRun,
} from './scripts/page-map-sweep-performance.mjs';

const report = {
  mode: 'playwright',
  status: 'complete',
  coverage_profile: 'full-public-sitemap',
  results: [{
    url: 'https://example.test/universe/',
    viewport: 'mobile-390',
    universe_mermaid_failure: {
      generated_page_map: {
        groups: [{
          group_index: 2,
          state: { title: 'Fixture group' },
          destination_count: 3,
          destinations: [
            { passed: true, expected_path: '/a/', destination_duration_ms: 90 },
            { passed: true, expected_path: '/b/', destination_duration_ms: 110 },
            { passed: true, expected_path: '/c/', destination_duration_ms: 100 },
          ],
        }],
      },
    },
  }],
};
const summary = summarizePageMapSweepRun(report);
assert.deepEqual(summary, [{
  route: '/universe/',
  viewport: 'mobile-390',
  group_index: 2,
  group_label: 'Fixture group',
  destination_count: 3,
  destination_signature: '["/a/","/b/","/c/"]',
  median_destination_ms: 100,
}]);
assert.deepEqual(summarizePageMapSweepRun({
  ...report, status: 'early_stop',
}), []);
assert.deepEqual(summarizePageMapSweepRun({
  ...report, coverage_profile: 'brandguard-edge-only',
}), []);

const summaryFor = value => ({
  route: '/universe/',
  viewport: 'mobile-390',
  group_index: 2,
  group_label: 'Fixture group',
  destination_count: 3,
  destination_signature: '["/a/","/b/","/c/"]',
  median_destination_ms: value,
});
const run = (index, value, chromium = '156.0.0.0') => ({
  generated: `2026-10-0${index}T00:00:00.000Z`,
  playwright_version: '1.64.1',
  chromium_version: chromium,
  summaries: [summaryFor(value)],
});

const oneNoisyRun = assessPageMapSweepHistory(
  [run(1, 80), run(2, 84), run(3, 76), run(4, 350)],
  run(5, 82),
);
assert.equal(oneNoisyRun.comparable_group_count, 1);
assert.deepEqual(oneNoisyRun.regressions, []);

const tooLittleHistory = assessPageMapSweepHistory(
  [run(1, 80), run(2, 82), run(3, 78)],
  run(4, 230),
);
assert.equal(tooLittleHistory.comparable_group_count, 0);
assert.deepEqual(tooLittleHistory.regressions, []);

const sustained = assessPageMapSweepHistory(
  [run(1, 80), run(2, 82), run(3, 700), run(4, 78), run(5, 80), run(6, 220)],
  run(7, 230),
);
assert.equal(sustained.comparable_group_count, 1);
assert.equal(sustained.regressions.length, 1);
const regression = sustained.regressions[0];
assert.equal(regression.route, '/universe/');
assert.equal(regression.group_index, 2);
assert.equal(regression.destination_count, 3);
assert.equal(regression.baseline_run_count, 5);
assert.equal(regression.confirmation_run_count, 2);
assert.equal(regression.baseline_median_destination_ms, 80);
assert.deepEqual(regression.comparison_window, {
  baseline_from: '2026-10-01T00:00:00.000Z',
  baseline_to: '2026-10-05T00:00:00.000Z',
  confirmation_from: '2026-10-06T00:00:00.000Z',
  confirmation_to: '2026-10-07T00:00:00.000Z',
});

const incompatibleRuntime = assessPageMapSweepHistory(
  [run(1, 80), run(2, 82), run(3, 78), run(4, 80, '157.0.0.0')],
  run(5, 230),
);
assert.deepEqual(incompatibleRuntime.regressions, []);

const changedDestinationSet = assessPageMapSweepHistory(
  [run(1, 80), run(2, 82), run(3, 78), run(4, 80), run(5, 220)],
  {
    ...run(6, 230),
    summaries: [{
      ...summaryFor(230),
      destination_signature: '["/different/","/new/","/routes/"]',
    }],
  },
);
assert.deepEqual(changedDestinationSet.regressions, []);

const history = { schema_version: 1, runs: [run(1, 80)] };
assert.equal(isValidPageMapSweepHistory(history), true);
assert.equal(isValidPageMapSweepHistory({ schema_version: 2, runs: [] }), false);
assert.equal(appendPageMapSweepHistory(
  { schema_version: 1, runs: Array.from({ length: 12 }, (_, index) => run(index + 1, 80)) },
  run(13, 80),
).runs.length, 12);
"""

    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=15,
    )

    assert result.returncode == 0, result.stderr


def test_universe_destination_failures_keep_map_group_and_link_identity():
    source = (ROOT / "scripts/responsive-qa.mjs").read_text(encoding="utf-8")
    start = source.index("const checkGeneratedPageMapFallback = async targetPage =>")
    end = source.index(
        "\n  const checkGeneratedPageMapWithoutJavaScript",
        start,
    )
    fallback_check = source[start:end]

    assert "PAGE LINK: ${groupLabel}, " in fallback_check
    assert "`link ${linkIndex + 1} \"${label}\"" in fallback_check
    assert "JSON.stringify(link.href)" in fallback_check


def test_universe_large_page_map_closes_destinations_after_navigation_failures(tmp_path):
    node_bin = os.environ.get("ASKJAMIE_NODE") or shutil.which("node")
    node_modules = os.environ.get("NODE_PATH")
    repo_modules = ROOT / "node_modules"
    if not node_modules and (repo_modules / "playwright").exists():
        node_modules = str(repo_modules)
    bundled_modules = Path(
        "/Users/okh/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules"
    )
    if not node_modules and (bundled_modules / "playwright").exists():
        node_modules = str(bundled_modules)
    if not node_bin or not node_modules or not (Path(node_modules) / "playwright").exists():
        pytest.skip("Playwright runtime is unavailable")

    source_html = (ROOT / "universe/index.html").read_text(encoding="utf-8")
    start_marker = "<!-- AUTOGEN:UNIVERSE-MAP -->"
    end_marker = "<!-- /AUTOGEN:UNIVERSE-MAP -->"
    start = source_html.index(start_marker)
    end = source_html.index(end_marker, start) + len(end_marker)
    destination_specs = [
        ("Connection drop", "/connection-drop/"),
        *[
            (f"Generated destination {index}", f"/generated-destination-{index:02d}/")
            for index in range(1, 23)
        ],
        ("Missing fixture route", "/missing-fixture-route/"),
    ]
    diagram_nodes = "\n".join(
        f'          n{index:016x}["{label}"]'
        for index, (label, _path) in enumerate(destination_specs, start=1)
    )
    destination_links = "\n".join(
        f'      <li><a data-universe-node="n{index:016x}" href="{path}">{label}</a>. Published page.</li>'
        for index, (label, path) in enumerate(destination_specs, start=1)
    )
    generated_map = f"""<!-- AUTOGEN:UNIVERSE-MAP -->
<section class="content-section container universe-map-generated" aria-labelledby="indexed-map-title">
  <h2 id="indexed-map-title">Explore fixture pages</h2>
  <p>Open a group to explore its diagram and page links.</p>
  <details class="card universe-map-group" data-map-node-count="{len(destination_specs)}">
    <summary>Fixture destinations (group 1)</summary>
    <figure class="askjamie-mermaid-shell">
      <div class="mermaid-scroll-wrap" aria-hidden="true" tabindex="-1">
        <div class="mermaid" inert data-diagram-label="Fixture destinations">flowchart TB
{diagram_nodes}
        </div>
      </div>
      <p class="mermaid-render-status sr-only" data-mermaid-failure-status role="status" aria-live="polite"></p>
      <figcaption>Pages beneath Fixture destinations. The links below provide the same navigation.</figcaption>
    </figure>
    <ul class="link-list">
{destination_links}
    </ul>
  </details>
</section>
<!-- /AUTOGEN:UNIVERSE-MAP -->"""
    source_html = source_html[:start] + generated_map + source_html[end:]
    requested_paths = []
    request_lock = threading.Lock()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, _format, *_args):
            return

        def _respond(self, payload, content_type, status=200):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            path = urlsplit(self.path).path
            with request_lock:
                requested_paths.append(path)
            if path == "/connection-drop/":
                self.close_connection = True
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self.connection.close()
                return
            if path == "/universe/":
                self._respond(source_html.encode("utf-8"), "text/html; charset=utf-8")
                return
            if path == "/missing-fixture-route/":
                self._respond(
                    (
                        '<!doctype html><html><head><title>Example Site | 404 page not found</title>'
                        '<meta name="viewport" content="width=device-width"></head>'
                        '<body><main><h1>Example Site — Page not found</h1></main></body></html>'
                    ).encode("utf-8"),
                    "text/html; charset=utf-8",
                    status=200,
                )
                return
            if path.startswith("/generated-destination-"):
                self._respond(
                    (
                        '<!doctype html><html><head><title>Fixture destination</title>'
                        '<meta name="viewport" content="width=device-width"></head>'
                        '<body><main><h1>Fixture destination</h1></main></body></html>'
                    ).encode("utf-8"),
                    "text/html; charset=utf-8",
                )
                return

            relative_path = path.lstrip("/")
            if path.endswith("/"):
                relative_path += "index.html"
            file_path = (ROOT / relative_path).resolve()
            if not file_path.is_relative_to(ROOT.resolve()) or not file_path.is_file():
                self.send_error(404, "unknown fixture asset")
                return
            content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
            self._respond(file_path.read_bytes(), content_type)

    class FixtureServer(http.server.ThreadingHTTPServer):
        # Mermaid imports many local chunks together. Preserve intentional
        # connection-drop behavior without overflowing the asset listen queue.
        request_queue_size = 512

    server = FixtureServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    fixture_root = tmp_path / "runner"
    (fixture_root / "scripts").mkdir(parents=True)
    runner_source = (ROOT / "scripts/responsive-qa.mjs").read_text(encoding="utf-8")
    runner_source, viewport_replacements = re.subn(
        r"const VIEWPORTS = \[.*?\n\];",
        "const VIEWPORTS = [\\n  { name: 'mobile-390', width: 390, height: 844 },\\n];",
        runner_source,
        count=1,
        flags=re.DOTALL,
    )
    assert viewport_replacements == 1
    (fixture_root / "scripts/responsive-qa.mjs").write_text(
        runner_source,
        encoding="utf-8",
    )
    _copy_responsive_qa_dependencies(fixture_root / "scripts/responsive-qa.mjs")
    (fixture_root / "sitemap.xml").write_text(
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        '<url><loc>https://askjamie.bot/universe/</loc></url></urlset>',
        encoding="utf-8",
    )
    (fixture_root / "universe").mkdir()
    (fixture_root / "universe/index.html").write_text(
        source_html,
        encoding="utf-8",
    )

    try:
        result = subprocess.run(
            [
                node_bin,
                "scripts/responsive-qa.mjs",
                f"--base=http://127.0.0.1:{server.server_address[1]}",
            ],
            cwd=fixture_root,
            text=True,
            capture_output=True,
            timeout=180,
            env={**os.environ, "NODE_PATH": node_modules},
        )
        report_path = fixture_root / "assets/audit/responsive-qa/results.json"
        assert report_path.is_file(), result.stdout + result.stderr
        report = json.loads(
            report_path.read_text(encoding="utf-8")
        )
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)

    assert result.returncode == 1, result.stdout + result.stderr
    row = next(
        row for row in report["results"]
        if row["url"].endswith("/universe/") and row["viewport"] == "mobile-390"
    )
    failure_check = row["universe_mermaid_failure"]
    generated_page_map = failure_check["generated_page_map"]
    assert generated_page_map["all_group_destinations_navigated"] is False
    assert generated_page_map["destination_sweep"]["destination_count"] == 24
    group = generated_page_map["groups"][0]
    assert group["all_destinations_work"] is False
    destinations = group["destinations"]
    assert len(destinations) == 24

    failed_destination = destinations[0]
    assert failed_destination["text"] == "Connection drop"
    assert failed_destination["expected_path"] == "/connection-drop/"
    assert failed_destination["reached_path"] is not None
    assert failed_destination["error"].startswith("navigation failed: page.goto:")
    assert re.search(
        r"net::ERR_(?:CONNECTION_RESET|EMPTY_RESPONSE)\b",
        failed_destination["error"],
    )
    assert all(destination["passed"] for destination in destinations[1:-1])
    assert destinations[-1]["text"] == "Missing fixture route"
    assert destinations[-1]["http_status"] == 200
    assert destinations[-1]["not_found_page"] is True
    assert destinations[-1]["reached_title"] == "Example Site | 404 page not found"
    assert destinations[-1]["reached_heading"] == "Example Site — Page not found"
    assert {"/generated-destination-01/", "/generated-destination-22/", "/missing-fixture-route/"}.issubset(
        set(requested_paths)
    )
    resource_cleanup = generated_page_map["resource_cleanup"]
    assert resource_cleanup["all_destinations_closed"] is True
    assert resource_cleanup["max_live_page_count"] == 1
    assert len(resource_cleanup["groups"]) == 1
    group_cleanup = resource_cleanup["groups"][0]
    assert group_cleanup["destination_count"] == 24
    assert group_cleanup["baseline_live_page_count"] == 1
    assert group_cleanup["all_destinations_closed"] is True
    assert len(group_cleanup["samples"]) == 24
    assert all(
        sample["destination_page_closed"]
        and sample["live_page_count"] == sample["expected_live_page_count"] == 1
        for sample in group_cleanup["samples"]
    )
    assert group_cleanup["samples"][0]["destination_index"] == 1
    assert group_cleanup["samples"][-1]["destination_index"] == 24
    reported_errors = "\n".join(row["errors"])
    not_found_summary = next(
        error for error in row["errors"]
        if 'link 24 "Missing fixture route" ("/missing-fixture-route/")' in error
    )
    assert "group 1" in not_found_summary
    assert 'title "Example Site | 404 page not found"' in not_found_summary
    assert 'heading "Example Site — Page not found"' in not_found_summary
    assert "Fixture destination" not in reported_errors
    assert any(
        'link 1 "Connection drop"' in error
        and 'expected path "/connection-drop/"' in error
        and "reached path " in error
        for error in row["errors"]
    )
    fallback_check = (
        (ROOT / "scripts/responsive-qa.mjs").read_text(encoding="utf-8")
        .split("const checkGeneratedPageMapFallback = async targetPage =>", 1)[1]
        .split("\n  const checkGeneratedPageMapWithoutJavaScript", 1)[0]
    )
    assert "await destinationPage?.close().catch(() => {});" in fallback_check
    assert "UNIVERSE PAGE-MAP RESOURCE COUNT GREW: ${groupLabel}, after destination " in fallback_check
    assert "destination_count: state.links.length" in fallback_check


def test_lighthouse_routes_preserves_controlled_mobile_isolation_contract():
    source = (ROOT / "scripts/lighthouse-routes.mjs").read_text(encoding="utf-8")

    assert 'if (controlled && preset !== "mobile") {' in source
    assert "The controlled third-party isolation mode is only supported with --preset=mobile." in source
    assert 'const outputSuffix = `${preset === "mobile" ? "-mobile" : ""}${controlled ? "-controlled" : ""}`;' in source

    blocked_patterns_start = source.index("export const CONTROLLED_BLOCKED_URL_PATTERNS = Object.freeze([")
    blocked_patterns_end = source.index("]);", blocked_patterns_start) + 2
    blocked_patterns = re.findall(
        r'"([^"]+)"',
        source[blocked_patterns_start:blocked_patterns_end],
    )
    assert blocked_patterns == [
        "https://fonts.googleapis.com/*",
        "https://fonts.gstatic.com/*",
        "https://www.googletagmanager.com/*",
        "https://www.google-analytics.com/*",
        "https://*.google-analytics.com/*",
    ]
    assert "CONTROLLED_BLOCKED_URL_PATTERNS.map((pattern) => `--blocked-url-patterns=${pattern}`)" in source

    assert 'thirdPartyFonts: "blocked"' in source
    assert 'analytics: "blocked"' in source
    assert "Controlled lab measurement only. Not field data." in source
    assert 'lcpElement: lcpElement?.selector || null' in source
    assert 'audits["largest-contentful-paint-element"]?.details?.items?.[0]?.items?.[0]?.node' in source
    assert "unavailableMetrics" in source


def test_lighthouse_routes_preserves_normal_output_contract():
    source = (ROOT / "scripts/lighthouse-routes.mjs").read_text(encoding="utf-8")

    assert '...(preset === "desktop" ? ["--preset=desktop"] : ["--form-factor=mobile"])' in source
    assert 'thirdPartyFonts: "in flight"' in source
    assert 'analytics: "in flight"' in source
    assert "No third-party isolation applied." in source

    for field in (
        "schemaVersion",
        "capturedAt",
        "tool",
        "environment",
        "property",
        "targetReview",
        "baseline",
        "controls",
        "pages",
        "performance",
        "accessibility",
        "bestPractices",
        "seo",
        "lcpMs",
        "cls",
        "tbtMs",
        "fcpMs",
        "lcpElement",
        "deltaPerformance",
        "deltaLcpMs",
        "unavailableMetrics",
    ):
        assert f"{field}:" in source
    assert "path," in source


def test_lighthouse_hosted_source_revision_signal_is_separate_from_runner_and_approval():
    runner = """
import assert from "node:assert/strict";
import {
  compareHostedSourceProvenance,
  createSummary,
  DEPLOYED_SOURCE_REVISION_HEADER,
  getHostedSourceRevision,
} from "./scripts/lighthouse-routes.mjs";

const runnerRevision = "a".repeat(40);
const deployedRevision = "b".repeat(40);
const runnerRevisionSha256 = "c".repeat(64);
const deployedRevisionSha256 = "d".repeat(64);
const responseWith = (value, url = "https://hosted.example/") => ({
  ok: true,
  status: 200,
  url,
  headers: { get: (name) => name === DEPLOYED_SOURCE_REVISION_HEADER ? value : null },
  body: { cancel: async () => {} },
});
const fetchWith = (response) => async (url, options) => {
  assert.equal(url, "https://hosted.example/");
  assert.equal(options.redirect, "follow");
  assert.equal(options.headers["cache-control"], "no-cache");
  return response;
};

const matching = await getHostedSourceRevision(
  "https://hosted.example/",
  runnerRevision,
  { fetchImpl: fetchWith(responseWith(runnerRevision.toUpperCase())) }
);
const differing = await getHostedSourceRevision(
  "https://hosted.example/",
  runnerRevision,
  { fetchImpl: fetchWith(responseWith(deployedRevision)) }
);
const matchingSha256 = await getHostedSourceRevision(
  "https://hosted.example/",
  runnerRevisionSha256,
  { fetchImpl: fetchWith(responseWith(runnerRevisionSha256.toUpperCase())) }
);
const differingSha256 = await getHostedSourceRevision(
  "https://hosted.example/",
  runnerRevisionSha256,
  { fetchImpl: fetchWith(responseWith(deployedRevisionSha256)) }
);
const invalidHeaders = await Promise.all(
  [
    "a".repeat(12),
    "a".repeat(39),
    "a".repeat(41),
    "a".repeat(63),
    "a".repeat(65),
    "g".repeat(40),
    "g".repeat(64),
    "a".repeat(39) + "!",
  ].map((value) =>
    getHostedSourceRevision(
      "https://hosted.example/",
      runnerRevision,
      { fetchImpl: fetchWith(responseWith(value)) }
    )
  )
);
const invalidRunnerRevision = await getHostedSourceRevision(
  "https://hosted.example/",
  "a".repeat(39),
  { fetchImpl: fetchWith(responseWith(deployedRevision)) }
);
const absent = await getHostedSourceRevision(
  "https://hosted.example/",
  runnerRevision,
  { fetchImpl: fetchWith(responseWith(null)) }
);
const sameOriginRedirect = await getHostedSourceRevision(
  "https://hosted.example/",
  runnerRevision,
  { fetchImpl: fetchWith(responseWith(deployedRevision, "https://hosted.example/deployed/")) }
);
const crossOriginRedirect = await getHostedSourceRevision(
  "https://hosted.example/",
  runnerRevision,
  { fetchImpl: fetchWith(responseWith(deployedRevision, "https://third-party.example/deployed/")) }
);
const missingResponseUrl = await getHostedSourceRevision(
  "https://hosted.example/",
  runnerRevision,
  { fetchImpl: fetchWith(responseWith(deployedRevision, "")) }
);
const malformedResponseUrl = await getHostedSourceRevision(
  "https://hosted.example/",
  runnerRevision,
  { fetchImpl: fetchWith(responseWith(deployedRevision, "not a valid URL")) }
);
const local = await getHostedSourceRevision(
  "http://127.0.0.1:5000/",
  runnerRevision,
  { fetchImpl: async () => { throw new Error("local targets must not be fetched"); } }
);

assert.deepEqual(
  [matching.status, matching.revision, matching.runnerCheckoutComparison],
  ["verified", runnerRevision, "matches"]
);
assert.deepEqual(
  [differing.status, differing.revision, differing.runnerCheckoutComparison],
  ["verified", deployedRevision, "differs"]
);
assert.deepEqual(
  [matchingSha256.status, matchingSha256.revision, matchingSha256.runnerCheckoutComparison],
  ["verified", runnerRevisionSha256, "matches"]
);
assert.deepEqual(
  [differingSha256.status, differingSha256.revision, differingSha256.runnerCheckoutComparison],
  ["verified", deployedRevisionSha256, "differs"]
);
for (const invalidHeader of invalidHeaders) {
  assert.equal(invalidHeader.status, "unavailable");
  assert.equal(invalidHeader.revision, null);
  assert.equal(invalidHeader.runnerCheckoutComparison, "unavailable");
}
assert.deepEqual(
  [invalidRunnerRevision.status, invalidRunnerRevision.revision, invalidRunnerRevision.runnerCheckoutComparison],
  ["verified", deployedRevision, "unavailable"]
);
assert.equal(absent.status, "unavailable");
assert.equal(absent.revision, null);
assert.equal(absent.runnerCheckoutComparison, "unavailable");
assert.deepEqual(
  [sameOriginRedirect.status, sameOriginRedirect.revision, sameOriginRedirect.runnerCheckoutComparison],
  ["verified", deployedRevision, "differs"]
);
for (const unavailableRedirect of [
  missingResponseUrl,
  malformedResponseUrl,
  crossOriginRedirect,
]) {
  assert.equal(unavailableRedirect.status, "unavailable");
  assert.equal(unavailableRedirect.revision, null);
  assert.equal(unavailableRedirect.runnerCheckoutComparison, "unavailable");
}
assert.equal(
  missingResponseUrl.reason,
  "Hosted target revision response did not include a final response URL; the requested origin could not be confirmed."
);
assert.equal(
  malformedResponseUrl.reason,
  "Hosted target revision response ended at an invalid final URL; the requested origin could not be confirmed."
);
assert.equal(
  crossOriginRedirect.reason,
  "Hosted target revision response ended at a different origin; the requested origin could not be confirmed."
);
for (const unverifiedResponse of [
  missingResponseUrl,
  malformedResponseUrl,
  crossOriginRedirect,
]) {
  assert.equal(JSON.stringify(unverifiedResponse).includes(deployedRevision), false);
}
assert.equal(local.status, "not-applicable");
assert.deepEqual(
  compareHostedSourceProvenance(matching, { ...matching }),
  { status: "stable", changedDuringRun: false }
);
assert.deepEqual(
  compareHostedSourceProvenance(matching, differing),
  { status: "changed", changedDuringRun: true }
);
assert.deepEqual(
  compareHostedSourceProvenance(matchingSha256, { ...matchingSha256 }),
  { status: "stable", changedDuringRun: false }
);
assert.deepEqual(
  compareHostedSourceProvenance(matchingSha256, differingSha256),
  { status: "changed", changedDuringRun: true }
);
assert.deepEqual(
  compareHostedSourceProvenance(matching, absent),
  { status: "unavailable", changedDuringRun: null }
);

const approvedReference = {
  status: "owner-approved",
  approvedAt: "2026-09-07",
  approvedBy: "Release owner",
  approvalRecord: "docs/desktop-reference-approval.md",
  integrityFingerprint: `sha256:${"a".repeat(64)}`,
};
const createApprovedSummary = (deployedSourceProvenanceRun) => createSummary({
  date: "2099-01-02",
  preset: "desktop",
  controlled: false,
  baseUrl: "https://hosted.example/",
  measurementStack: { lighthouseVersion: "13.5.0", chromiumVersion: "153.0.8010.12" },
  baselineMeasurementStack: { lighthouseVersion: "13.5.0", chromiumVersion: "153.0.8010.12" },
  referenceApproval: approvedReference,
  referenceIntegrityFingerprint: approvedReference.integrityFingerprint,
  deployedSourceProvenanceRun,
});
const summary = createApprovedSummary({
  start: matching,
  end: matching,
  ...compareHostedSourceProvenance(matching, matching),
});
const approvedSummaryReference = summary.referenceApproval;
assert.equal(approvedSummaryReference.status, "owner-approved");
assert.equal(approvedSummaryReference.ownerApproved, true);
for (const provenance of [matching, differing, matchingSha256, differingSha256, ...invalidHeaders, invalidRunnerRevision]) {
  const summary = createApprovedSummary({
    start: provenance,
    end: provenance,
    ...compareHostedSourceProvenance(provenance, provenance),
  });
  assert.deepEqual(summary.referenceApproval, approvedSummaryReference);
  assert.equal(summary.referenceApproval.ownerApproved, true);
}
assert.equal(summary.deployedSourceProvenanceRun.start.runnerCheckoutComparison, "matches");
console.log("hosted-source-revision-tests-passed");
"""
    result = subprocess.run(
        ["node", "--input-type=module", "-e", runner],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "hosted-source-revision-tests-passed" in result.stdout

    documentation = (
        ROOT / "docs/performance/lighthouse-hosted-source-revision.md"
    ).read_text(encoding="utf-8")
    assert "X-AskJamie-Source-Revision" in documentation
    assert "not a cryptographic proof" in documentation


def test_lighthouse_desktop_reference_is_independent_and_not_mislabelled_approved(tmp_path):
    reference_path = ROOT / "assets/docs/performance-baseline-desktop-2026-09-07.json"
    desktop_baseline = json.loads(reference_path.read_text(encoding="utf-8"))
    assert desktop_baseline["referenceType"] == "desktop"
    assert desktop_baseline["schemaVersion"] == 2
    assert desktop_baseline["targetOrigin"] == "http://127.0.0.1:5000"
    assert desktop_baseline["runConditions"]["formFactor"] == "desktop"
    assert desktop_baseline["measurementStack"]["lighthouseVersion"] == "12.8.2"
    assert desktop_baseline["pages"]["brandguard"]["performance"] == 99
    assert desktop_baseline["sourceRevision"] is None
    assert desktop_baseline["sourceWorkingTreeClean"] is None
    assert desktop_baseline["sourceRevisionStatus"] == "not-recorded"
    assert "did not preserve" in desktop_baseline["sourceRevisionNote"]
    assert desktop_baseline["approval"]["status"] == "not-recorded"
    assert desktop_baseline["approval"]["approvedAt"] is None
    assert desktop_baseline["approval"]["approvedBy"] is None
    assert desktop_baseline["approval"]["approvalRecord"] is None
    assert desktop_baseline["approval"]["integrityFingerprint"] is None

    runner = tmp_path / "desktop-reference-fixture.mjs"
    runner.write_text(
        """
import { readFileSync } from "node:fs";
import {
  CHROME_STARTUP_FLAGS,
  HISTORICAL_REFERENCE_MEASUREMENT_STACK,
  HISTORICAL_REFERENCE_RUN_CONDITIONS,
  createRunConditionsReview,
  createSummary,
  getDesktopReferenceIntegrityFingerprint,
  getLighthouseReference,
  summarizePage,
  summarizeReferenceApproval,
  summarizeRunConditions
} from "./scripts/lighthouse-routes.mjs";

const desktopBaseline = JSON.parse(readFileSync(
  "assets/docs/performance-baseline-desktop-2026-09-07.json",
  "utf8"
));
const fixture = JSON.parse(readFileSync("tests/fixtures/lighthouse-summary-report.json", "utf8"));
const desktopReference = getLighthouseReference("desktop", desktopBaseline);
const mobileReference = getLighthouseReference("mobile", desktopBaseline);
const approvedBaseline = {
  ...desktopBaseline,
  approval: {
    status: "owner-approved",
    approvedAt: "2026-10-02",
    approvedBy: "site owner",
    approvalRecord: "docs/desktop-reference-approval.md",
    integrityFingerprint: getDesktopReferenceIntegrityFingerprint(desktopBaseline)
  }
};
const approvedReference = getLighthouseReference("desktop", approvedBaseline);
const unchangedApproval = summarizeReferenceApproval(
  approvedReference.approval,
  approvedReference.integrityFingerprint
);
const orderedSettingsBaseline = {
  ...approvedBaseline,
  runConditions: {
    ...approvedBaseline.runConditions,
    storage: {
      ...approvedBaseline.runConditions.storage,
      clearStorageTypes: [
        ...approvedBaseline.runConditions.storage.clearStorageTypes
      ].reverse()
    }
  }
};
const orderedSettingsReference = getLighthouseReference(
  "desktop",
  orderedSettingsBaseline
);
const orderedSettingsApproval = summarizeReferenceApproval(
  orderedSettingsReference.approval,
  orderedSettingsReference.integrityFingerprint
);
const staleVariants = {
  measurement: {
    ...approvedBaseline,
    pages: {
      ...approvedBaseline.pages,
      brandguard: { ...approvedBaseline.pages.brandguard, performance: 98 }
    }
  },
  conditions: {
    ...approvedBaseline,
    runConditions: {
      ...approvedBaseline.runConditions,
      screenEmulation: {
        ...approvedBaseline.runConditions.screenEmulation,
        width: approvedBaseline.runConditions.screenEmulation.width + 1
      }
    }
  },
  measurementStack: {
    ...approvedBaseline,
    measurementStack: {
      ...approvedBaseline.measurementStack,
      lighthouseVersion: "12.8.3"
    }
  },
  targetOrigin: {
    ...approvedBaseline,
    targetOrigin: "https://different-reference.invalid"
  }
};
const staleApprovals = Object.fromEntries(
  Object.entries(staleVariants).map(([name, baseline]) => {
    const reference = getLighthouseReference("desktop", baseline);
    return [name, summarizeReferenceApproval(reference.approval, reference.integrityFingerprint)];
  })
);
const desktopReports = Object.fromEntries(
  ["homepage", "brandguard", "universe", "search"].map(name => [
    `${name}.json`,
    { ...desktopBaseline.runConditions, chromeFlags: [...CHROME_STARTUP_FLAGS] }
  ])
);
const desktopBaselineWithFlags = {
  ...desktopBaseline,
  runConditions: {
    ...desktopBaseline.runConditions,
    chromeFlags: [...CHROME_STARTUP_FLAGS]
  }
};
const desktopReferenceWithFlags = getLighthouseReference("desktop", desktopBaselineWithFlags);
const desktopMatchingFlagReview = createRunConditionsReview(desktopReports, {
  baselineRunConditions: desktopReferenceWithFlags.runConditions
});
const desktopChangedFlagReview = createRunConditionsReview({
  ...desktopReports,
  "universe.json": {
    ...desktopReports["universe.json"],
    chromeFlags: ["--headless", "--no-sandbox", "--disable-gpu"]
  }
}, {
  baselineRunConditions: desktopReferenceWithFlags.runConditions
});
const desktopMissingFlagReview = createRunConditionsReview({
  ...desktopReports,
  "brandguard-sample-02.json": {
    ...desktopReports["brandguard.json"],
    chromeFlags: "unavailable"
  }
}, {
  baselineRunConditions: desktopReferenceWithFlags.runConditions
});
const desktopSummary = createSummary({
  date: "2099-01-02",
  preset: "desktop",
  controlled: false,
  baseUrl: "https://fixture.invalid",
  measurementStack: desktopBaseline.measurementStack,
  baselineMeasurementStack: desktopReference.measurementStack,
  runConditionsReports: desktopReports,
  baselineRunConditions: desktopReference.runConditions,
  baselineTargetOrigin: desktopReference.targetOrigin,
  baselinePath: desktopReference.path,
  measurementStackReferenceNote: desktopReference.measurementStackReferenceNote,
  referenceApproval: desktopReference.approval,
  referenceIntegrityFingerprint: desktopReference.integrityFingerprint
});
desktopSummary.pages.brandguard = summarizePage({
  report: fixture,
  path: "/lens-system/okhp3-brandguard/",
  baselinePage: desktopBaseline.pages.brandguard
});
const legacyApproval = {
  status: "owner-approved",
  approvedAt: "2026-09-01",
  approvedBy: "site owner",
  approvalRecord: "docs/desktop-reference-approval.md"
};
const legacyReference = getLighthouseReference("desktop", {
  ...desktopBaseline,
  approval: legacyApproval
});
const legacySummary = createSummary({
  date: "2099-01-02",
  preset: "desktop",
  controlled: false,
  baseUrl: "https://fixture.invalid",
  measurementStack: desktopBaseline.measurementStack,
  baselineMeasurementStack: legacyReference.measurementStack,
  runConditionsReports: desktopReports,
  baselineRunConditions: legacyReference.runConditions,
  baselineTargetOrigin: legacyReference.targetOrigin,
  baselinePath: legacyReference.path,
  referenceApproval: legacyReference.approval,
  referenceIntegrityFingerprint: legacyReference.integrityFingerprint
});
const upgradedStack = createSummary({
  date: "2099-01-02",
  preset: "desktop",
  controlled: false,
  baseUrl: "https://fixture.invalid",
  measurementStack: { ...desktopBaseline.measurementStack, lighthouseVersion: "13.5.0" },
  baselineMeasurementStack: desktopReference.measurementStack,
  runConditionsReports: desktopReports,
  baselineRunConditions: desktopReference.runConditions,
  baselineTargetOrigin: desktopReference.targetOrigin,
  baselinePath: desktopReference.path,
  referenceApproval: desktopReference.approval,
  referenceIntegrityFingerprint: desktopReference.integrityFingerprint
});
const chromiumUpgrade = createSummary({
  date: "2099-01-02",
  preset: "desktop",
  controlled: false,
  baseUrl: "https://fixture.invalid",
  measurementStack: { ...desktopBaseline.measurementStack, chromiumVersion: "149.0.0.0" },
  baselineMeasurementStack: desktopReference.measurementStack,
  runConditionsReports: desktopReports,
  baselineRunConditions: desktopReference.runConditions,
  baselineTargetOrigin: desktopReference.targetOrigin,
  baselinePath: desktopReference.path,
  referenceApproval: desktopReference.approval,
  referenceIntegrityFingerprint: desktopReference.integrityFingerprint
});
const changedConditions = createSummary({
  date: "2099-01-02",
  preset: "desktop",
  controlled: false,
  baseUrl: "https://fixture.invalid",
  measurementStack: desktopBaseline.measurementStack,
  baselineMeasurementStack: desktopReference.measurementStack,
  runConditionsReports: { "homepage.json": summarizeRunConditions(fixture, CHROME_STARTUP_FLAGS) },
  baselineRunConditions: desktopReference.runConditions,
  baselineTargetOrigin: desktopReference.targetOrigin,
  baselinePath: desktopReference.path,
  referenceApproval: desktopReference.approval,
  referenceIntegrityFingerprint: desktopReference.integrityFingerprint
});
const mobileSummary = createSummary({
  date: "2099-01-02",
  preset: "mobile",
  controlled: false,
  baseUrl: "https://fixture.invalid",
  measurementStack: HISTORICAL_REFERENCE_MEASUREMENT_STACK,
  baselineMeasurementStack: mobileReference.measurementStack,
  runConditionsReports: { "homepage.json": summarizeRunConditions(fixture, CHROME_STARTUP_FLAGS) },
  baselineRunConditions: HISTORICAL_REFERENCE_RUN_CONDITIONS,
  baselineTargetOrigin: mobileReference.targetOrigin,
  baselinePath: mobileReference.path
});
const incompleteApprovalFields = ["approvedAt", "approvedBy", "approvalRecord"];
const incompleteApprovalCases = Object.fromEntries(
  incompleteApprovalFields.flatMap(field => {
    const absent = {
      status: "owner-approved",
      approvedAt: "2026-10-02",
      approvedBy: "site owner",
      approvalRecord: "docs/desktop-reference-approval.md",
      integrityFingerprint: desktopReference.integrityFingerprint
    };
    delete absent[field];
    const blank = {
      status: "owner-approved",
      approvedAt: "2026-10-02",
      approvedBy: "site owner",
      approvalRecord: "docs/desktop-reference-approval.md",
      integrityFingerprint: desktopReference.integrityFingerprint,
      [field]: "   "
    };
    return [
      [`missing-${field}`, summarizeReferenceApproval(absent, desktopReference.integrityFingerprint)],
      [`blank-${field}`, summarizeReferenceApproval(blank, desktopReference.integrityFingerprint)]
    ];
  })
);
const incompleteApprovalSummary = createSummary({
  date: "2099-01-02",
  preset: "desktop",
  controlled: false,
  baseUrl: "https://fixture.invalid",
  measurementStack: desktopBaseline.measurementStack,
  baselineMeasurementStack: desktopReference.measurementStack,
  referenceApproval: { status: "owner-approved" },
  referenceIntegrityFingerprint: desktopReference.integrityFingerprint
});
const recordedApproval = summarizeReferenceApproval({
  status: "owner-approved",
  approvedAt: "2026-10-02",
  approvedBy: "site owner",
  approvalRecord: "docs/desktop-reference-approval.md",
  integrityFingerprint: desktopReference.integrityFingerprint
}, desktopReference.integrityFingerprint);
const unavailableCurrentApproval = summarizeReferenceApproval({
  status: "owner-approved",
  approvedAt: "2026-10-02",
  approvedBy: "site owner",
  approvalRecord: "docs/desktop-reference-approval.md",
  integrityFingerprint: desktopReference.integrityFingerprint
});
const malformedApproval = summarizeReferenceApproval({
  status: "owner-approved",
  approvedAt: "2026-10-02",
  approvedBy: "site owner",
  approvalRecord: "docs/desktop-reference-approval.md",
  integrityFingerprint: "sha256:not-a-valid-fingerprint"
}, desktopReference.integrityFingerprint);
const blankFingerprintApproval = summarizeReferenceApproval({
  status: "owner-approved",
  approvedAt: "2026-10-02",
  approvedBy: "site owner",
  approvalRecord: "docs/desktop-reference-approval.md",
  integrityFingerprint: "   "
}, desktopReference.integrityFingerprint);
let mobileConditionsRejected = "";
try {
  getLighthouseReference("desktop", {
    ...desktopBaseline,
    runConditions: HISTORICAL_REFERENCE_RUN_CONDITIONS
  });
} catch (error) {
  mobileConditionsRejected = error.message;
}
process.stdout.write(JSON.stringify({
  desktopReference,
  desktopMatchingFlagReview,
  desktopChangedFlagReview,
  desktopMissingFlagReview,
  unchangedApproval,
  orderedSettingsReview: {
    originalValues: approvedBaseline.runConditions.storage.clearStorageTypes,
    changedValues: orderedSettingsBaseline.runConditions.storage.clearStorageTypes,
    originalFingerprint: approvedReference.integrityFingerprint,
    changedFingerprint: orderedSettingsReference.integrityFingerprint,
    approval: orderedSettingsApproval
  },
  staleApprovals,
  mobileReference,
  desktopSummary,
  legacySummary,
  upgradedStack,
  chromiumUpgrade,
  changedConditions,
  mobileSummary,
  incompleteApprovalCases,
  incompleteApprovalSummary,
  recordedApproval,
  unavailableCurrentApproval,
  malformedApproval,
  blankFingerprintApproval,
  mobileConditionsRejected
}));
""".strip()
        + "\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", runner.read_text(encoding="utf-8")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    emitted = json.loads(result.stdout)
    desktop_summary = emitted["desktopSummary"]
    fingerprint_result = subprocess.run(
        [
            "node",
            "scripts/lighthouse-routes.mjs",
            "--print-desktop-reference-fingerprint",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
        env={
            **os.environ,
            "CHROME_PATH": str(tmp_path / "chromium-must-not-launch"),
        },
    )
    command_fingerprint = fingerprint_result.stdout.strip()
    assert emitted["desktopReference"]["path"] == (
        "assets/docs/performance-baseline-desktop-2026-09-07.json"
    )
    assert emitted["desktopReference"]["targetOrigin"] == desktop_baseline["targetOrigin"]
    assert emitted["mobileReference"]["targetOrigin"] == "http://127.0.0.1:5000"
    assert emitted["desktopReference"]["runConditions"]["formFactor"] == "desktop"
    assert "chromeFlags" not in emitted["desktopReference"]["runConditions"]
    assert emitted["desktopMatchingFlagReview"]["status"] == "not-required"
    assert emitted["desktopMatchingFlagReview"]["changedReports"] == []
    assert emitted["desktopChangedFlagReview"]["status"] == "required"
    assert emitted["desktopChangedFlagReview"]["changedReports"] == [{
        "report": "universe.json",
        "changedComponents": ["chromeFlags"],
    }]
    assert emitted["desktopMissingFlagReview"]["status"] == "required"
    assert emitted["desktopMissingFlagReview"]["changedReports"] == [{
        "report": "brandguard-sample-02.json",
        "changedComponents": ["chromeFlags"],
    }]
    assert emitted["mobileReference"]["path"] == (
        "assets/docs/performance-baseline-mobile-2026-08-22.json"
    )
    assert emitted["mobileReference"]["runConditions"]["formFactor"] == "mobile"
    assert desktop_summary["baseline"] == emitted["desktopReference"]["path"]
    assert desktop_summary["measurementStackReview"]["status"] == "not-required"
    assert desktop_summary["runConditionsReview"]["status"] == "required"
    assert desktop_summary["runConditionsReview"]["changedComponents"] == ["chromeFlags"]
    assert desktop_summary["runConditionsReview"]["baseline"]["formFactor"] == "desktop"
    assert desktop_summary["referenceApproval"] == {
        "status": "not-recorded",
        "reason": "missing",
        "ownerApproved": False,
        "approvalIntegrityFingerprint": None,
        "currentIntegrityFingerprint": emitted["desktopReference"]["integrityFingerprint"],
        "action": "No owner approval is recorded for this reference; treat desktop trend deltas as exploratory.",
    }
    assert emitted["desktopReference"]["integrityFingerprint"].startswith("sha256:")
    assert command_fingerprint == emitted["desktopReference"]["integrityFingerprint"]
    assert command_fingerprint == (
        desktop_summary["referenceApproval"]["currentIntegrityFingerprint"]
    )
    assert fingerprint_result.stdout == f"{command_fingerprint}\n"
    assert emitted["unchangedApproval"]["status"] == "owner-approved"
    assert emitted["unchangedApproval"]["reason"] == "approved"
    assert emitted["unchangedApproval"]["ownerApproved"] is True
    assert emitted["unchangedApproval"]["approvalIntegrityFingerprint"] == (
        emitted["unchangedApproval"]["currentIntegrityFingerprint"]
    )
    ordered_settings_review = emitted["orderedSettingsReview"]
    assert ordered_settings_review["changedValues"] == list(
        reversed(ordered_settings_review["originalValues"])
    )
    assert sorted(ordered_settings_review["changedValues"]) == sorted(
        ordered_settings_review["originalValues"]
    )
    assert ordered_settings_review["changedFingerprint"] != (
        ordered_settings_review["originalFingerprint"]
    )
    assert ordered_settings_review["approval"]["status"] == "stale"
    assert ordered_settings_review["approval"]["reason"] == "changed"
    assert ordered_settings_review["approval"]["ownerApproved"] is False
    assert set(emitted["staleApprovals"]) == {
        "measurement",
        "conditions",
        "measurementStack",
        "targetOrigin",
    }
    for stale_approval in emitted["staleApprovals"].values():
        assert stale_approval["status"] == "stale"
        assert stale_approval["reason"] == "changed"
        assert stale_approval["ownerApproved"] is False
        assert "fresh owner decision" in stale_approval["action"]
    assert desktop_summary["pages"]["brandguard"]["deltaPerformance"] == -8
    assert desktop_summary["pages"]["brandguard"]["deltaLcpMs"] == 1616
    assert emitted["upgradedStack"]["measurementStackReview"]["status"] == "required"
    assert emitted["upgradedStack"]["measurementStackReview"]["changedComponents"] == [
        "lighthouseVersion"
    ]
    assert emitted["chromiumUpgrade"]["measurementStackReview"]["status"] == "required"
    assert emitted["chromiumUpgrade"]["measurementStackReview"]["changedComponents"] == [
        "chromiumVersion"
    ]
    assert emitted["changedConditions"]["runConditionsReview"]["status"] == "required"
    assert "formFactor" in emitted["changedConditions"]["runConditionsReview"]["changedComponents"]
    assert emitted["mobileSummary"]["baseline"] == emitted["mobileReference"]["path"]
    assert emitted["mobileSummary"]["measurementStackReview"]["status"] == "not-required"
    assert emitted["mobileSummary"]["runConditionsReview"]["status"] == "required"
    assert emitted["mobileSummary"]["runConditionsReview"]["changedComponents"] == ["chromeFlags"]
    assert "referenceApproval" not in emitted["mobileSummary"]
    for field in ("approvedAt", "approvedBy", "approvalRecord"):
        for condition in ("missing", "blank"):
            review = emitted["incompleteApprovalCases"][f"{condition}-{field}"]
            assert review["status"] == "stale"
            assert review["reason"] == "incomplete"
            assert review["ownerApproved"] is False
            assert review["missingFields"] == [field]
            assert "complete the approval date, owner, and evidence reference" in (
                review["action"]
            )
            assert review["action"]
            if condition == "blank":
                assert review[field] == "   "
            else:
                assert review[field] is None
            expected_details = {
                "approvedAt": "2026-10-02",
                "approvedBy": "site owner",
                "approvalRecord": "docs/desktop-reference-approval.md",
            }
            for detail_field, supplied_value in expected_details.items():
                if detail_field != field:
                    assert review[detail_field] == supplied_value
            assert review["approvalIntegrityFingerprint"] == (
                emitted["desktopReference"]["integrityFingerprint"]
            )
    incomplete_summary_approval = emitted["incompleteApprovalSummary"][
        "referenceApproval"
    ]
    assert incomplete_summary_approval["reason"] == "incomplete"
    assert incomplete_summary_approval["missingFields"] == [
        "approvedAt",
        "approvedBy",
        "approvalRecord",
    ]
    assert incomplete_summary_approval["approvedAt"] is None
    assert incomplete_summary_approval["approvedBy"] is None
    assert incomplete_summary_approval["approvalRecord"] is None
    assert "complete the approval date, owner, and evidence reference" in (
        incomplete_summary_approval["action"]
    )
    assert emitted["recordedApproval"]["status"] == "owner-approved"
    assert emitted["recordedApproval"]["reason"] == "approved"
    assert emitted["recordedApproval"]["ownerApproved"] is True
    assert emitted["unavailableCurrentApproval"]["status"] == "stale"
    assert emitted["unavailableCurrentApproval"]["reason"] == "unavailable-current"
    assert "current desktop reference integrity fingerprint is unavailable" in (
        emitted["unavailableCurrentApproval"]["action"]
    )
    for invalid_approval in (emitted["malformedApproval"],):
        assert invalid_approval["status"] == "stale"
        assert invalid_approval["reason"] == "malformed"
        assert invalid_approval["ownerApproved"] is False
        assert "fresh owner decision" in invalid_approval["action"]
    assert emitted["blankFingerprintApproval"]["status"] == "stale"
    assert emitted["blankFingerprintApproval"]["reason"] == "missing"
    assert emitted["blankFingerprintApproval"]["ownerApproved"] is False
    assert "fresh owner decision" in emitted["blankFingerprintApproval"]["action"]
    assert "malformed integrity fingerprint" in emitted["malformedApproval"]["action"]
    assert "desktop reference changed" not in emitted["malformedApproval"]["action"]
    assert "no integrity fingerprint" in emitted["blankFingerprintApproval"]["action"]
    assert emitted["blankFingerprintApproval"]["reason"] == "missing"
    legacy_approval = emitted["legacySummary"]["referenceApproval"]
    assert legacy_approval["status"] == "stale"
    assert legacy_approval["ownerApproved"] is False
    assert legacy_approval["approvalIntegrityFingerprint"] is None
    assert "no integrity fingerprint" in legacy_approval["action"]
    assert "fresh owner decision" in legacy_approval["action"]
    assert "must record desktop run conditions" in emitted["mobileConditionsRejected"]


def test_lighthouse_source_revision_reports_known_unavailable_and_dirty_tree(tmp_path):
    node_bin = shutil.which("node")
    git_bin = shutil.which("git")
    if not node_bin or not git_bin:
        pytest.skip("Node.js and Git are required")

    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run([git_bin, "init", "-q", str(repository)], check=True)
    (repository / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    author_env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Release test",
        "GIT_AUTHOR_EMAIL": "release-test@example.invalid",
        "GIT_COMMITTER_NAME": "Release test",
        "GIT_COMMITTER_EMAIL": "release-test@example.invalid",
    }
    subprocess.run(
        [git_bin, "-C", str(repository), "add", "tracked.txt"],
        check=True,
        env=author_env,
    )
    subprocess.run(
        [git_bin, "-C", str(repository), "commit", "-q", "-m", "fixture baseline"],
        check=True,
        env=author_env,
    )
    expected_revision = subprocess.check_output(
        [git_bin, "-C", str(repository), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    script_module = (ROOT / "scripts/lighthouse-routes.mjs").as_uri()
    runner = tmp_path / "source-revision.mjs"
    runner.write_text(
        f"""
import {{ createSummary, getSourceRevision }} from {json.dumps(script_module)};
import {{ writeFileSync }} from "node:fs";
const clean = getSourceRevision({json.dumps(str(repository))});
writeFileSync({json.dumps(str(repository / "tracked.txt"))}, "changed\\n");
const dirty = getSourceRevision({json.dumps(str(repository))});
const unavailable = getSourceRevision({json.dumps(str(tmp_path / "not-a-repository"))});
const summarize = (source) => createSummary({{
  date: "2099-01-02",
  preset: "desktop",
  controlled: false,
  baseUrl: "http://127.0.0.1:5000",
  measurementStack: {{ lighthouseVersion: "12.8.2", chromiumVersion: "148.0.7778.96" }},
  baselineMeasurementStack: null,
  sourceRevision: source.revision,
  sourceWorkingTreeClean: source.workingTreeClean,
  sourceRevisionStatus: source.status,
  sourceRevisionError: source.error
}});
process.stdout.write(JSON.stringify({{
  clean, dirty, unavailable,
  summaries: {{
    clean: summarize(clean),
    dirty: summarize(dirty),
    unavailable: summarize(unavailable)
  }}
}}));
""".strip()
        + "\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [node_bin, str(runner)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    provenance = json.loads(result.stdout)
    assert provenance["clean"]["revision"] == expected_revision
    assert provenance["clean"]["workingTreeClean"] is True
    assert provenance["clean"]["status"] == "recorded"
    assert provenance["clean"]["error"] is None
    assert len(provenance["clean"]["workingTreeFingerprint"]) == 64
    assert provenance["dirty"]["revision"] == expected_revision
    assert provenance["dirty"]["workingTreeClean"] is False
    assert provenance["dirty"]["workingTreeFingerprint"] != provenance["clean"]["workingTreeFingerprint"]
    assert provenance["dirty"]["status"] == "recorded"
    assert provenance["dirty"]["error"] is None
    assert provenance["unavailable"]["revision"] is None
    assert provenance["unavailable"]["workingTreeClean"] is None
    assert provenance["unavailable"]["workingTreeFingerprint"] is None
    assert provenance["unavailable"]["status"] == "unavailable"
    assert provenance["unavailable"]["error"]
    assert provenance["summaries"]["clean"]["sourceRevision"] == expected_revision
    assert provenance["summaries"]["clean"]["sourceWorkingTreeClean"] is True
    assert provenance["summaries"]["clean"]["sourceRevisionStatus"] == "recorded"
    assert provenance["summaries"]["dirty"]["sourceRevision"] == expected_revision
    assert provenance["summaries"]["dirty"]["sourceWorkingTreeClean"] is False
    assert provenance["summaries"]["dirty"]["sourceRevisionStatus"] == "recorded"
    assert provenance["summaries"]["unavailable"]["sourceRevision"] is None
    assert provenance["summaries"]["unavailable"]["sourceWorkingTreeClean"] is None
    assert provenance["summaries"]["unavailable"]["sourceRevisionStatus"] == "unavailable"
    assert provenance["summaries"]["unavailable"]["sourceRevisionError"]


def test_lighthouse_sha256_checkout_revision_is_recorded_and_compared(tmp_path):
    node_bin = shutil.which("node")
    git_bin = shutil.which("git")
    if not node_bin or not git_bin:
        pytest.skip("Node.js and Git are required")

    repository = tmp_path / "sha256-repository"
    repository.mkdir()
    initialized = subprocess.run(
        [git_bin, "init", "-q", "--object-format=sha256", str(repository)],
        capture_output=True,
        text=True,
    )
    if initialized.returncode != 0:
        reason = (initialized.stderr or initialized.stdout).strip().splitlines()
        pytest.skip(
            "Installed Git cannot initialize SHA-256 repositories"
            + (f": {reason[-1]}" if reason else "")
        )

    author_env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Release test",
        "GIT_AUTHOR_EMAIL": "release-test@example.invalid",
        "GIT_COMMITTER_NAME": "Release test",
        "GIT_COMMITTER_EMAIL": "release-test@example.invalid",
    }
    (repository / "tracked.txt").write_text("first commit\n", encoding="utf-8")
    subprocess.run(
        [git_bin, "-C", str(repository), "add", "tracked.txt"],
        check=True,
        env=author_env,
    )
    subprocess.run(
        [git_bin, "-C", str(repository), "commit", "-q", "-m", "first commit"],
        check=True,
        env=author_env,
    )
    different_revision = subprocess.check_output(
        [git_bin, "-C", str(repository), "rev-parse", "HEAD"],
        text=True,
    ).strip()

    (repository / "tracked.txt").write_text("second commit\n", encoding="utf-8")
    subprocess.run(
        [git_bin, "-C", str(repository), "add", "tracked.txt"],
        check=True,
        env=author_env,
    )
    subprocess.run(
        [git_bin, "-C", str(repository), "commit", "-q", "-m", "second commit"],
        check=True,
        env=author_env,
    )
    expected_revision = subprocess.check_output(
        [git_bin, "-C", str(repository), "rev-parse", "HEAD"],
        text=True,
    ).strip()

    script_module = (ROOT / "scripts/lighthouse-routes.mjs").as_uri()
    runner = tmp_path / "sha256-source-revision.mjs"
    runner.write_text(
        f"""
import {{ getHostedSourceRevision, getSourceRevision }} from {json.dumps(script_module)};
const source = getSourceRevision({json.dumps(str(repository))});
const compareHostedRevision = (revision) => getHostedSourceRevision(
  "https://hosted.example/",
  source.revision,
  {{
    fetchImpl: async () => ({{
      ok: true,
      status: 200,
      url: "https://hosted.example/",
      headers: {{
        get: (name) => name === "x-askjamie-source-revision" ? revision : null,
      }},
      body: {{ cancel: async () => {{}} }},
    }}),
  }},
);
const matching = await compareHostedRevision(source.revision);
const different = await compareHostedRevision({json.dumps(different_revision)});
process.stdout.write(JSON.stringify({{ source, matching, different }}));
""".strip()
        + "\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [node_bin, str(runner)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    provenance = json.loads(result.stdout)

    assert re.fullmatch(r"[0-9a-f]{64}", expected_revision)
    assert re.fullmatch(r"[0-9a-f]{64}", different_revision)
    assert expected_revision != different_revision
    assert provenance["source"]["revision"] == expected_revision
    assert provenance["source"]["status"] == "recorded"
    assert provenance["matching"]["status"] == "verified"
    assert provenance["matching"]["revision"] == expected_revision
    assert provenance["matching"]["runnerCheckoutComparison"] == "matches"
    assert provenance["different"]["status"] == "verified"
    assert provenance["different"]["revision"] == different_revision
    assert provenance["different"]["runnerCheckoutComparison"] == "differs"


@pytest.mark.parametrize(
    "revision_fetch_mode",
    ["cross-origin", "stalled"],
    ids=["cross-origin", "stalled"],
)
def test_lighthouse_route_runner_preserves_checkout_provenance_when_hosted_revision_is_unavailable(
    tmp_path,
    revision_fetch_mode,
):
    if os.name == "nt":
        pytest.skip("POSIX fake executables require shebang support unavailable on Windows")
    node_bin = shutil.which("node")
    git_bin = shutil.which("git")
    if not node_bin or not git_bin:
        pytest.skip("Node.js and Git are required")

    root = tmp_path
    (root / "scripts").mkdir()
    shutil.copy2(ROOT / "scripts/lighthouse-routes.mjs", root / "scripts/lighthouse-routes.mjs")
    (root / "assets/audit").mkdir(parents=True)
    (root / "assets/docs").mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        ROOT / "assets/docs/performance-baseline-mobile-2026-08-22.json",
        root / "assets/docs/performance-baseline-mobile-2026-08-22.json",
    )
    (root / "tests/fixtures").mkdir(parents=True)
    shutil.copy2(
        ROOT / "tests/fixtures/lighthouse-summary-incomplete-report.json",
        root / "tests/fixtures/lighthouse-summary-incomplete-report.json",
    )
    checkout_file = root / "tracked-checkout-file.txt"
    checkout_file.write_text("committed baseline\n", encoding="utf-8")
    staged_file = root / "staged-source.txt"
    staged_file.write_text("staged baseline\n", encoding="utf-8")
    unstaged_file = root / "unstaged-source.txt"
    unstaged_file.write_text("unstaged baseline\n", encoding="utf-8")
    unusual_untracked_filename = "untracked source café\tline\nbreak.txt"
    untracked_file = root / unusual_untracked_filename

    lighthouse_dir = root / "node_modules/.bin"
    lighthouse_dir.mkdir(parents=True)
    invocation_log = (
        root
        / "assets/audit/lighthouse-2099-02-06-mobile/fake-lighthouse-invocations.txt"
    )
    fake_lighthouse = lighthouse_dir / "lighthouse"
    fake_lighthouse.write_text(
        """#!/usr/bin/env node
const fs = require("node:fs");
const path = require("node:path");
const childProcess = require("node:child_process");
const args = process.argv.slice(2);
fs.appendFileSync(process.env.FAKE_LIGHTHOUSE_LOG, `${args[0]}\\n`);
const reportPath = args.find((arg) => arg.startsWith("--output-path="))
  .slice("--output-path=".length);
const report = JSON.parse(fs.readFileSync(
  path.join(process.cwd(), "tests/fixtures/lighthouse-summary-incomplete-report.json"),
  "utf8"
));
report.lighthouseVersion = "12.8.2";
report.environment = { hostUserAgent: "HeadlessChrome/148.0.0.0" };
report.configSettings = {};
fs.writeFileSync(reportPath, JSON.stringify(report));
if (path.basename(reportPath) === "brandguard.json") {
  childProcess.execFileSync(
    "git",
    ["checkout", "-q", process.env.FAKE_ALTERNATE_BRANCH],
    { cwd: process.cwd() }
  );
  childProcess.execFileSync(
    "git",
    ["checkout", "-q", process.env.FAKE_ORIGINAL_BRANCH],
    { cwd: process.cwd() }
  );
  const original = fs.readFileSync(process.env.FAKE_CHECKOUT_FILE, "utf8");
  fs.writeFileSync(process.env.FAKE_CHECKOUT_FILE, "edited temporarily during measurement\\n");
  fs.writeFileSync(process.env.FAKE_CHECKOUT_FILE, original);
} else if (path.basename(reportPath) === "brandguard-sample-02.json") {
  childProcess.execFileSync("git", ["add", "staged-source.txt"], { cwd: process.cwd() });
} else if (path.basename(reportPath) === "brandguard-sample-03.json") {
  fs.writeFileSync(process.env.FAKE_UNSTAGED_FILE, "unstaged during measurement\\n");
  fs.writeFileSync(process.env.FAKE_UNTRACKED_FILE, "untracked during measurement\\n");
}
""",
        encoding="utf-8",
    )
    fake_lighthouse.chmod(0o755)

    fake_chrome = root / "fake-chrome"
    fake_chrome.write_text(
        "#!/bin/sh\necho 'Chromium 148.0.7778.96'\n",
        encoding="utf-8",
    )
    fake_chrome.chmod(0o755)
    fetch_preload = root / "revision-fetch.mjs"
    fetch_event_log = root.parent / f"{root.name}-revision-fetch-events.jsonl"
    fetch_preload.write_text(
        """
import fs from "node:fs";

if (process.env.FAKE_REVISION_FETCH_MODE === "stalled") {
  globalThis.fetch = async (_url, options) => {
    const startedAt = performance.now();
    const record = (event, elapsedMs = null) => fs.appendFileSync(
      process.env.FAKE_REVISION_FETCH_LOG,
      `${JSON.stringify({ event, elapsedMs })}\\n`,
    );
    record("started");
    return new Promise((_resolve, reject) => {
      const signal = options?.signal;
      if (!signal) {
        reject(new Error("Hosted revision fetch did not receive an abort signal."));
        return;
      }
      // A real stalled network request keeps the event loop alive while
      // fetch waits for the abort signal.
      const keepAlive = setTimeout(() => {}, 10000);
      const onAbort = () => {
        clearTimeout(keepAlive);
        record("aborted", performance.now() - startedAt);
        reject(signal.reason || new Error("Hosted revision fetch was aborted."));
      };
      if (signal.aborted) onAbort();
      else signal.addEventListener("abort", onAbort, { once: true });
    });
  };
} else {
  globalThis.fetch = async () => ({
    ok: true,
    status: 200,
    url: "https://third-party.invalid/final/",
    headers: { get: () => "f".repeat(40) },
    body: { cancel: async () => {} },
  });
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    author_env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Release test",
        "GIT_AUTHOR_EMAIL": "release-test@example.invalid",
        "GIT_COMMITTER_NAME": "Release test",
        "GIT_COMMITTER_EMAIL": "release-test@example.invalid",
    }
    subprocess.run([git_bin, "init", "-q", str(root)], check=True)
    subprocess.run([git_bin, "-C", str(root), "add", "-A"], check=True, env=author_env)
    subprocess.run(
        [git_bin, "-C", str(root), "commit", "-q", "-m", "fixture baseline"],
        check=True,
        env=author_env,
    )
    subprocess.run(
        [git_bin, "-C", str(root), "branch", "lighthouse-original"],
        check=True,
    )
    subprocess.run(
        [git_bin, "-C", str(root), "commit", "--allow-empty", "-q", "-m", "alternate revision"],
        check=True,
        env=author_env,
    )
    subprocess.run(
        [git_bin, "-C", str(root), "branch", "lighthouse-alternate"],
        check=True,
    )
    subprocess.run(
        [git_bin, "-C", str(root), "checkout", "-q", "lighthouse-original"],
        check=True,
    )
    starting_revision = subprocess.check_output(
        [git_bin, "-C", str(root), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    assert subprocess.check_output(
        [git_bin, "-C", str(root), "symbolic-ref", "--short", "HEAD"],
        text=True,
    ).strip() == "lighthouse-original"
    checkout_file.write_text("dirty before measurement\n", encoding="utf-8")
    staged_file.write_text("staged during measurement\n", encoding="utf-8")

    environment = {
        **os.environ,
        "CHROME_PATH": str(fake_chrome),
        "NODE_OPTIONS": (
            f'{os.environ.get("NODE_OPTIONS", "").strip()} --import={fetch_preload.as_uri()}'
        ).strip(),
        "FAKE_LIGHTHOUSE_LOG": str(invocation_log),
        "FAKE_REVISION_FETCH_MODE": revision_fetch_mode,
        "FAKE_REVISION_FETCH_LOG": str(fetch_event_log),
        "FAKE_CHECKOUT_FILE": str(checkout_file),
        "FAKE_STAGED_FILE": str(staged_file),
        "FAKE_UNSTAGED_FILE": str(unstaged_file),
        "FAKE_UNTRACKED_FILE": str(untracked_file),
        "FAKE_ORIGINAL_BRANCH": "lighthouse-original",
        "FAKE_ALTERNATE_BRANCH": "lighthouse-alternate",
    }
    run_started = time.monotonic()
    run = subprocess.run(
        [
            node_bin,
            "scripts/lighthouse-routes.mjs",
            "--preset=mobile",
            "--brandguard-samples=3",
            "--date=2099-02-06",
            "--base-url=https://fixture.invalid",
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
    )
    run_elapsed = time.monotonic() - run_started
    assert run.returncode == 0, run.stderr
    assert len(invocation_log.read_text(encoding="utf-8").splitlines()) == 6

    summary = json.loads(
        (root / "assets/audit/lighthouse-2099-02-06-mobile/summary.json")
        .read_text(encoding="utf-8")
    )
    deployed_provenance = summary["deployedSourceProvenanceRun"]
    assert deployed_provenance["status"] == "unavailable"
    assert deployed_provenance["start"]["status"] == "unavailable"
    assert deployed_provenance["end"]["status"] == "unavailable"
    assert deployed_provenance["start"]["revision"] is None
    assert deployed_provenance["end"]["revision"] is None
    assert deployed_provenance["start"]["runnerCheckoutComparison"] == "unavailable"
    if revision_fetch_mode == "stalled":
        fetch_events = [
            json.loads(line)
            for line in fetch_event_log.read_text(encoding="utf-8").splitlines()
        ]
        started_events = [event for event in fetch_events if event["event"] == "started"]
        aborted_events = [event for event in fetch_events if event["event"] == "aborted"]
        assert len(started_events) == len(aborted_events) == 2
        assert all(2500 <= event["elapsedMs"] < 4000 for event in aborted_events)
        assert run_elapsed < 12
        assert "timeout" in deployed_provenance["start"]["reason"].lower()
        assert "timeout" in deployed_provenance["end"]["reason"].lower()
    provenance = summary["sourceProvenanceRun"]
    assert summary["sourceRevision"] == provenance["start"]["revision"]
    assert summary["sourceRevision"] is not None
    assert provenance["start"]["status"] == "recorded"
    assert provenance["end"]["status"] == "recorded"
    assert provenance["status"] == "changed"
    assert provenance["changedComponents"] == ["workingTree"]
    assert provenance["start"]["revision"] == provenance["end"]["revision"]
    assert provenance["start"]["revision"] == starting_revision
    assert subprocess.check_output(
        [git_bin, "-C", str(root), "symbolic-ref", "--short", "HEAD"],
        text=True,
    ).strip() == "lighthouse-original"
    assert provenance["start"]["workingTreeClean"] is False
    assert provenance["end"]["workingTreeClean"] is False
    assert (
        provenance["start"]["workingTreeFingerprint"]
        != provenance["end"]["workingTreeFingerprint"]
    )
    source_change_monitoring = summary["sourceChangeMonitoring"]
    assert source_change_monitoring["status"] == "recorded"
    assert source_change_monitoring["changedPaths"] == [
        "staged-source.txt",
        "tracked-checkout-file.txt",
        "unstaged-source.txt",
        unusual_untracked_filename,
    ]
    assert not any(
        path.startswith("assets/audit/lighthouse-2099-02-06-mobile")
        for path in source_change_monitoring["changedPaths"]
    )
    affected_passes = source_change_monitoring["affectedPasses"]
    assert [item["index"] for item in affected_passes] == [1]
    assert affected_passes[0]["eventCount"] > 0
    assert affected_passes[0]["revisionChanged"] is True
    assert "tracked-checkout-file.txt" in affected_passes[0]["changedPaths"]
    affected_samples = source_change_monitoring["affectedBrandGuardSamples"]
    assert {item["index"] for item in affected_samples} == {2, 3}
    assert affected_samples[0]["changedPaths"] == ["staged-source.txt"]
    assert set(affected_samples[1]["changedPaths"]) == {
        "unstaged-source.txt",
        unusual_untracked_filename,
    }
    assert "SITE SOURCE CHANGE OBSERVED DURING MEASUREMENT: pass(es) 1" in run.stderr
    assert "Site source revision at start:" in run.stdout
    assert "Site source revision at end:" in run.stdout


def test_lighthouse_route_runner_records_hosted_revision_change_without_changing_approval(tmp_path):
    if os.name == "nt":
        pytest.skip("POSIX fake executables require shebang support unavailable on Windows")
    node_bin = shutil.which("node")
    git_bin = shutil.which("git")
    if not node_bin or not git_bin:
        pytest.skip("Node.js and Git are required")

    root = tmp_path
    (root / "scripts").mkdir()
    shutil.copy2(ROOT / "scripts/lighthouse-routes.mjs", root / "scripts/lighthouse-routes.mjs")
    (root / "tests/fixtures").mkdir(parents=True)
    shutil.copy2(
        ROOT / "tests/fixtures/lighthouse-summary-report.json",
        root / "tests/fixtures/lighthouse-summary-report.json",
    )
    (root / "assets/docs").mkdir(parents=True)
    reference_path = root / "assets/docs/performance-baseline-desktop-2026-09-07.json"
    shutil.copy2(
        ROOT / "assets/docs/performance-baseline-desktop-2026-09-07.json",
        reference_path,
    )
    reference = json.loads(reference_path.read_text(encoding="utf-8"))
    reference["targetOrigin"] = "https://hosted.example"
    reference["approval"] = {
        "status": "owner-approved",
        "approvedAt": "2026-09-07",
        "approvedBy": "Release owner",
        "approvalRecord": "docs/desktop-reference-approval.md",
        "integrityFingerprint": None,
    }
    reference_path.write_text(json.dumps(reference), encoding="utf-8")
    fingerprint_result = subprocess.run(
        [
            node_bin,
            "--input-type=module",
            "-e",
            (
                'import { getDesktopReferenceIntegrityFingerprint } '
                'from "./scripts/lighthouse-routes.mjs"; '
                'import fs from "node:fs"; '
                'const reference = JSON.parse(fs.readFileSync(process.argv[1], "utf8")); '
                'process.stdout.write(getDesktopReferenceIntegrityFingerprint(reference));'
            ),
            str(reference_path),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    reference["approval"]["integrityFingerprint"] = fingerprint_result.stdout
    reference_path.write_text(json.dumps(reference), encoding="utf-8")
    original_approval = reference["approval"].copy()

    (root / "assets/audit").mkdir(parents=True)
    lighthouse_dir = root / "node_modules/.bin"
    lighthouse_dir.mkdir(parents=True)
    fake_lighthouse = lighthouse_dir / "lighthouse"
    fake_lighthouse.write_text(
        """#!/usr/bin/env node
const fs = require("node:fs");
const path = require("node:path");
const args = process.argv.slice(2);
const reportPath = args.find((arg) => arg.startsWith("--output-path="))
  .slice("--output-path=".length);
const report = JSON.parse(fs.readFileSync(
  path.join(process.cwd(), "tests/fixtures/lighthouse-summary-report.json"),
  "utf8"
));
report.lighthouseVersion = "12.8.2";
report.environment = { hostUserAgent: "Mozilla/5.0 HeadlessChrome/148.0.0.0" };
report.userAgent = "Mozilla/5.0 HeadlessChrome/148.0.0.0";
report.configSettings = {};
fs.writeFileSync(reportPath, JSON.stringify(report));
""",
        encoding="utf-8",
    )
    fake_lighthouse.chmod(0o755)
    fake_chrome = root / "fake-chrome"
    fake_chrome.write_text(
        "#!/bin/sh\nprintf '%s\\n' 'Chromium 148.0.7778.96'\n",
        encoding="utf-8",
    )
    fake_chrome.chmod(0o755)
    fetch_preload = root / "revision-fetch.mjs"
    fetch_preload.write_text(
        """
let responseCount = 0;
const revisions = ["a".repeat(40), "b".repeat(40)];
globalThis.fetch = async () => {
  const revision = revisions[responseCount++];
  return {
    ok: true,
    status: 200,
    url: "https://hosted.example/",
    headers: {
      get: (name) => name === "x-askjamie-source-revision" ? revision : null,
    },
    body: { cancel: async () => {} },
  };
};
""".strip()
        + "\n",
        encoding="utf-8",
    )

    author_env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Release test",
        "GIT_AUTHOR_EMAIL": "release-test@example.invalid",
        "GIT_COMMITTER_NAME": "Release test",
        "GIT_COMMITTER_EMAIL": "release-test@example.invalid",
    }
    subprocess.run([git_bin, "init", "-q", str(root)], check=True)
    subprocess.run([git_bin, "-C", str(root), "add", "-A"], check=True, env=author_env)
    subprocess.run(
        [git_bin, "-C", str(root), "commit", "-q", "-m", "fixture baseline"],
        check=True,
        env=author_env,
    )
    result = subprocess.run(
        [
            node_bin,
            "scripts/lighthouse-routes.mjs",
            "--preset=desktop",
            "--base-url=https://hosted.example",
            "--date=2099-02-07",
        ],
        cwd=root,
        env={
            **os.environ,
            "CHROME_PATH": str(fake_chrome),
            "NODE_OPTIONS": (
                f'{os.environ.get("NODE_OPTIONS", "").strip()} --import={fetch_preload.as_uri()}'
            ).strip(),
        },
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr

    output_dir = root / "assets/audit/lighthouse-2099-02-07"
    summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
    run_status = json.loads((output_dir / "run-status.json").read_text(encoding="utf-8"))
    deployed_provenance = summary["deployedSourceProvenanceRun"]
    assert deployed_provenance["status"] == "changed"
    assert deployed_provenance["changedDuringRun"] is True
    assert deployed_provenance["start"]["status"] == "verified"
    assert deployed_provenance["end"]["status"] == "verified"
    assert deployed_provenance["start"]["revision"] == "a" * 40
    assert deployed_provenance["end"]["revision"] == "b" * 40
    assert "HOSTED DEPLOYED SOURCE REVISION CHANGED DURING RUN:" in result.stderr
    assert set(summary["pages"]) == {"homepage", "brandguard", "universe", "search"}
    assert run_status["status"] == "completed"
    assert summary["referenceApproval"]["status"] == "owner-approved"
    assert summary["referenceApproval"]["ownerApproved"] is True
    assert summary["referenceApproval"]["approvalIntegrityFingerprint"] == (
        original_approval["integrityFingerprint"]
    )
    assert json.loads(reference_path.read_text(encoding="utf-8"))["approval"] == original_approval


def test_lighthouse_monitor_names_files_staged_between_repeated_samples(tmp_path):
    if os.name == "nt":
        pytest.skip("POSIX fake executables require shebang support unavailable on Windows")
    node_bin = shutil.which("node")
    git_bin = shutil.which("git")
    if not node_bin or not git_bin:
        pytest.skip("Node.js and Git are required")

    root = tmp_path / "checkout"
    root.mkdir()
    staged_file = root / "pre-dirty-source.txt"
    staged_file.write_text("committed baseline\n", encoding="utf-8")
    author_env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Release test",
        "GIT_AUTHOR_EMAIL": "release-test@example.invalid",
        "GIT_COMMITTER_NAME": "Release test",
        "GIT_COMMITTER_EMAIL": "release-test@example.invalid",
    }
    subprocess.run([git_bin, "init", "-q", str(root)], check=True)
    subprocess.run([git_bin, "-C", str(root), "add", "-A"], check=True, env=author_env)
    subprocess.run(
        [git_bin, "-C", str(root), "commit", "-q", "-m", "fixture baseline"],
        check=True,
        env=author_env,
    )
    staged_file.write_text("dirty before repeated samples\n", encoding="utf-8")

    module_url = (ROOT / "scripts/lighthouse-routes.mjs").as_uri()
    run = subprocess.run(
        [
            node_bin,
            "--input-type=module",
            "-e",
            f"""
import {{ execFileSync }} from "node:child_process";
import {{ createCheckoutChangeMonitor }} from {json.dumps(module_url)};

const monitor = createCheckoutChangeMonitor(process.cwd());
monitor.beginMeasurement("sample", 1);
monitor.endMeasurement();
execFileSync("git", ["add", "pre-dirty-source.txt"], {{ cwd: process.cwd() }});
monitor.beginMeasurement("sample", 2);
monitor.endMeasurement();
await monitor.flush();
monitor.close();
process.stdout.write(JSON.stringify(monitor.summary()));
""",
        ],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stderr
    summary = json.loads(run.stdout)
    assert summary["changedPaths"] == ["pre-dirty-source.txt"]
    assert summary["affectedBrandGuardSamples"] == []


def test_lighthouse_generated_reports_do_not_look_like_checkout_changes(tmp_path):
    if os.name == "nt":
        pytest.skip("POSIX fake executables require shebang support unavailable on Windows")
    node_bin = shutil.which("node")
    git_bin = shutil.which("git")
    if not node_bin or not git_bin:
        pytest.skip("Node.js and Git are required")

    root = tmp_path / "checkout"
    root.mkdir()
    (root / "scripts").mkdir()
    shutil.copy2(ROOT / "scripts/lighthouse-routes.mjs", root / "scripts/lighthouse-routes.mjs")
    (root / "assets/audit").mkdir(parents=True)
    (root / "assets/docs").mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        ROOT / "assets/docs/performance-baseline-mobile-2026-08-22.json",
        root / "assets/docs/performance-baseline-mobile-2026-08-22.json",
    )
    (root / "tests/fixtures").mkdir(parents=True)
    shutil.copy2(
        ROOT / "tests/fixtures/lighthouse-summary-incomplete-report.json",
        root / "tests/fixtures/lighthouse-summary-incomplete-report.json",
    )

    lighthouse_dir = root / "node_modules/.bin"
    lighthouse_dir.mkdir(parents=True)
    invocation_log = tmp_path / "fake-lighthouse-invocations.txt"
    fake_lighthouse = lighthouse_dir / "lighthouse"
    fake_lighthouse.write_text(
        """#!/usr/bin/env node
const fs = require("node:fs");
const path = require("node:path");
const args = process.argv.slice(2);
fs.appendFileSync(process.env.FAKE_LIGHTHOUSE_LOG, `${args[0]}\\n`);
const reportPath = args.find((arg) => arg.startsWith("--output-path="))
  .slice("--output-path=".length);
const report = JSON.parse(fs.readFileSync(
  path.join(process.cwd(), "tests/fixtures/lighthouse-summary-incomplete-report.json"),
  "utf8"
));
report.lighthouseVersion = "12.8.2";
report.environment = { hostUserAgent: "HeadlessChrome/148.0.0.0" };
report.configSettings = {};
fs.writeFileSync(reportPath, JSON.stringify(report));
""",
        encoding="utf-8",
    )
    fake_lighthouse.chmod(0o755)

    fake_chrome = root / "fake-chrome"
    fake_chrome.write_text(
        "#!/bin/sh\necho 'Chromium 148.0.7778.96'\n",
        encoding="utf-8",
    )
    fake_chrome.chmod(0o755)
    fetch_preload = root / "cross-origin-fetch.mjs"
    fetch_preload.write_text(
        """
globalThis.fetch = async () => ({
  ok: true,
  status: 200,
  url: "https://third-party.invalid/final/",
  headers: { get: () => "f".repeat(40) },
  body: { cancel: async () => {} },
});
""".strip()
        + "\n",
        encoding="utf-8",
    )

    author_env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Release test",
        "GIT_AUTHOR_EMAIL": "release-test@example.invalid",
        "GIT_COMMITTER_NAME": "Release test",
        "GIT_COMMITTER_EMAIL": "release-test@example.invalid",
    }
    subprocess.run([git_bin, "init", "-q", str(root)], check=True)
    subprocess.run([git_bin, "-C", str(root), "add", "-A"], check=True, env=author_env)
    subprocess.run(
        [git_bin, "-C", str(root), "commit", "-q", "-m", "fixture baseline"],
        check=True,
        env=author_env,
    )

    environment = {
        **os.environ,
        "CHROME_PATH": str(fake_chrome),
        "NODE_OPTIONS": (
            f'{os.environ.get("NODE_OPTIONS", "").strip()} --import={fetch_preload.as_uri()}'
        ).strip(),
        "FAKE_LIGHTHOUSE_LOG": str(invocation_log),
    }
    run = subprocess.run(
        [
            node_bin,
            "scripts/lighthouse-routes.mjs",
            "--preset=mobile",
            "--date=2099-02-07",
            "--base-url=https://fixture.invalid",
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stderr
    assert len(invocation_log.read_text(encoding="utf-8").splitlines()) == 4

    output_dir = root / "assets/audit/lighthouse-2099-02-07-mobile"
    assert len(list(output_dir.glob("*.json"))) >= 5
    summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
    provenance = summary["sourceProvenanceRun"]
    assert provenance["status"] == "unchanged"
    assert provenance["changedComponents"] == []
    assert provenance["start"]["workingTreeClean"] is True
    assert provenance["end"]["workingTreeClean"] is True

    monitoring = summary["sourceChangeMonitoring"]
    assert monitoring["status"] == "recorded"
    assert monitoring["changedPaths"] == []
    assert monitoring["affectedPasses"] == []
    assert monitoring["affectedBrandGuardSamples"] == []
    assert "SITE SOURCE CHANGE OBSERVED DURING MEASUREMENT:" not in run.stderr


def test_lighthouse_navigation_review_accepts_canonical_slash_but_flags_wrong_same_origin_route():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("Node.js is unavailable")

    runner = """
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  createLighthouseNavigationReview,
  formatLighthouseNavigationReviewNotice,
} from "./scripts/lighthouse-routes.mjs";

const fixture = JSON.parse(readFileSync(
  "tests/fixtures/lighthouse-summary-report.json",
  "utf8"
));
const requestedUrl = "https://fixture.invalid/lens-system/okhp3-brandguard/";
const canonical = createLighthouseNavigationReview("https://fixture.invalid", {
  "brandguard.json": {
    requestedUrl,
    report: { ...fixture, finalDisplayedUrl: requestedUrl.slice(0, -1) },
  },
});
assert.equal(canonical.status, "not-required");
assert.deepEqual(canonical.changedReports, []);
assert.deepEqual(canonical.reports["brandguard.json"], {
  requestedUrl,
  requestedPath: "/lens-system/okhp3-brandguard/",
  requestedSearch: "",
  requestedHash: "",
  url: "https://fixture.invalid/lens-system/okhp3-brandguard",
  origin: "https://fixture.invalid",
  path: "/lens-system/okhp3-brandguard",
  search: "",
  hash: "",
  pathMatches: true,
  queryMatches: true,
  fragmentMatches: true,
  originMatches: true,
  diagnostics: [],
});

const requestedSearchUrl = "https://fixture.invalid/search/?q=BrandGuard&cat=all#main";
const trackingOnly = createLighthouseNavigationReview("https://fixture.invalid", {
  "search-tracking-only.json": {
    requestedUrl: requestedSearchUrl,
    report: {
      ...fixture,
      finalDisplayedUrl:
        "https://fixture.invalid/search/?utm_source=qa&q=BrandGuard&gclid=123&cat=all#main",
    },
  },
});
assert.equal(trackingOnly.status, "not-required");
assert.deepEqual(trackingOnly.changedReports, []);
assert.equal(trackingOnly.reports["search-tracking-only.json"].queryMatches, true);
assert.equal(trackingOnly.reports["search-tracking-only.json"].fragmentMatches, true);

const nonCanonicalPaths = createLighthouseNavigationReview("https://fixture.invalid", {
  "repeated-trailing-slashes.json": {
    requestedUrl: "https://fixture.invalid/lens-system/okhp3-brandguard/",
    report: {
      ...fixture,
      finalDisplayedUrl: "https://fixture.invalid/lens-system/okhp3-brandguard///",
    },
  },
  "internal-duplicate-slash.json": {
    requestedUrl: "https://fixture.invalid/lens-system/okhp3-brandguard/",
    report: {
      ...fixture,
      finalDisplayedUrl: "https://fixture.invalid/lens-system//okhp3-brandguard/",
    },
  },
  "encoded-separator.json": {
    requestedUrl: "https://fixture.invalid/lens-system/okhp3-brandguard/",
    report: {
      ...fixture,
      finalDisplayedUrl: "https://fixture.invalid/lens-system%2Fokhp3-brandguard/",
    },
  },
  "encoded-dot-segment.json": {
    requestedUrl: "https://fixture.invalid/lens-system/%2e%2e/universe/",
    report: {
      ...fixture,
      finalDisplayedUrl: "https://fixture.invalid/universe/",
    },
  },
  "unrelated-path.json": {
    requestedUrl: "https://fixture.invalid/lens-system/okhp3-brandguard/",
    report: {
      ...fixture,
      finalDisplayedUrl: "https://fixture.invalid/universe/",
    },
  },
});
assert.equal(nonCanonicalPaths.status, "required");
assert.deepEqual(nonCanonicalPaths.changedReports, [
  "repeated-trailing-slashes.json",
  "internal-duplicate-slash.json",
  "encoded-separator.json",
  "encoded-dot-segment.json",
  "unrelated-path.json",
]);
for (const [name, navigation] of Object.entries(nonCanonicalPaths.reports)) {
  assert.equal(navigation.pathMatches, false, name);
}
const nonCanonicalNotice = formatLighthouseNavigationReviewNotice(nonCanonicalPaths);
for (const [name, requestedUrl, finalUrl] of [
  [
    "repeated-trailing-slashes.json",
    "https://fixture.invalid/lens-system/okhp3-brandguard/",
    "https://fixture.invalid/lens-system/okhp3-brandguard///",
  ],
  [
    "internal-duplicate-slash.json",
    "https://fixture.invalid/lens-system/okhp3-brandguard/",
    "https://fixture.invalid/lens-system//okhp3-brandguard/",
  ],
  [
    "encoded-separator.json",
    "https://fixture.invalid/lens-system/okhp3-brandguard/",
    "https://fixture.invalid/lens-system%2Fokhp3-brandguard/",
  ],
  [
    "encoded-dot-segment.json",
    "https://fixture.invalid/lens-system/%2e%2e/universe/",
    "https://fixture.invalid/universe/",
  ],
  [
    "unrelated-path.json",
    "https://fixture.invalid/lens-system/okhp3-brandguard/",
    "https://fixture.invalid/universe/",
  ],
]) {
  assert.ok(
    nonCanonicalNotice.includes(
          `${name} (requested ${requestedUrl}; final ${finalUrl}; diagnostics: final route path differs from requested route)`,
    ),
    `navigation review notice should identify ${name} and both URLs`,
  );
}

const contentChangingUrls = createLighthouseNavigationReview("https://fixture.invalid", {
  "search-query-changed.json": {
    requestedUrl: requestedSearchUrl,
    report: {
      ...fixture,
      finalDisplayedUrl: "https://fixture.invalid/search/?q=resume&cat=all#main",
    },
  },
  "search-filter-changed.json": {
    requestedUrl: requestedSearchUrl,
    report: {
      ...fixture,
      finalDisplayedUrl: "https://fixture.invalid/search/?q=BrandGuard&cat=services#main",
    },
  },
  "unknown-query-changed.json": {
    requestedUrl: "https://fixture.invalid/universe/?view=full",
    report: { ...fixture, finalDisplayedUrl: "https://fixture.invalid/universe/?view=compact" },
  },
  "fragment-changed.json": {
    requestedUrl: requestedSearchUrl,
    report: {
      ...fixture,
      finalDisplayedUrl: "https://fixture.invalid/search/?q=BrandGuard&cat=all#results",
    },
  },
});
assert.equal(contentChangingUrls.status, "required");
assert.deepEqual(contentChangingUrls.changedReports, [
  "search-query-changed.json",
  "search-filter-changed.json",
  "unknown-query-changed.json",
  "fragment-changed.json",
]);
assert.deepEqual(contentChangingUrls.changedComponents, [
  "final navigation query differs from requested route",
  "final navigation fragment differs from requested route",
]);

const wrongRoute = createLighthouseNavigationReview("https://fixture.invalid", {
  "brandguard-sample-02.json": {
    requestedUrl,
    report: { ...fixture, finalDisplayedUrl: "https://fixture.invalid/universe/" },
  },
  "brandguard.json": {
    requestedUrl,
    report: { ...fixture, finalDisplayedUrl: requestedUrl.slice(0, -1) },
  },
});
assert.equal(wrongRoute.status, "required");
assert.deepEqual(wrongRoute.changedReports, ["brandguard-sample-02.json"]);
assert.deepEqual(wrongRoute.changedComponents, [
  "final navigation path differs from requested route",
]);
assert.deepEqual(
  wrongRoute.reports["brandguard-sample-02.json"].diagnostics,
  ["final route path differs from requested route"],
);
assert.match(
  formatLighthouseNavigationReviewNotice(wrongRoute),
  /brandguard-sample-02\\.json \\(requested https:\\/\\/fixture\\.invalid\\/lens-system\\/okhp3-brandguard\\/; final https:\\/\\/fixture\\.invalid\\/universe\\//,
);

const mismatchedOrigin = createLighthouseNavigationReview("https://fixture.invalid", {
  "brandguard-other-origin.json": {
    requestedUrl,
    report: {
      ...fixture,
      finalDisplayedUrl: "https://redirected.invalid/lens-system/okhp3-brandguard/",
    },
  },
});
assert.deepEqual(
  mismatchedOrigin.reports["brandguard-other-origin.json"].diagnostics,
  ["final origin differs from requested origin"],
);
assert.match(
  formatLighthouseNavigationReviewNotice(mismatchedOrigin),
  /diagnostics: final origin differs from requested origin/,
);

const unavailableRequestedUrl = createLighthouseNavigationReview("https://fixture.invalid", {
  "brandguard-missing-requested-url.json": {
    report: { ...fixture, finalDisplayedUrl: requestedUrl },
  },
  "brandguard-invalid-requested-url.json": {
    requestedUrl: "not a valid URL",
    report: { ...fixture, finalDisplayedUrl: requestedUrl },
  },
});
assert.equal(unavailableRequestedUrl.status, "required");
assert.deepEqual(unavailableRequestedUrl.changedReports, [
  "brandguard-missing-requested-url.json",
  "brandguard-invalid-requested-url.json",
]);
for (const name of [
  "brandguard-missing-requested-url.json",
  "brandguard-invalid-requested-url.json",
]) {
  assert.deepEqual(unavailableRequestedUrl.reports[name].diagnostics, [
    "requested URL unavailable",
    "requested origin unavailable",
    "requested route path unavailable",
    "requested query unavailable",
    "requested fragment unavailable",
  ]);
}

const missingFinalUrlReport = { ...fixture };
delete missingFinalUrlReport.finalDisplayedUrl;
const unavailableFinalNavigation = createLighthouseNavigationReview("https://fixture.invalid", {
  "brandguard-missing-final-url.json": {
    requestedUrl,
    report: missingFinalUrlReport,
  },
});
assert.equal(unavailableFinalNavigation.status, "required");
assert.deepEqual(unavailableFinalNavigation.changedReports, [
  "brandguard-missing-final-url.json",
]);
assert.equal(
  unavailableFinalNavigation.reports["brandguard-missing-final-url.json"].url,
  null,
);
assert.deepEqual(
  unavailableFinalNavigation.reports["brandguard-missing-final-url.json"].diagnostics,
  [
    "final displayed URL unavailable",
    "final origin unavailable",
    "final route path unavailable",
    "final query unavailable",
    "final fragment unavailable",
  ],
);
assert.match(
  formatLighthouseNavigationReviewNotice(unavailableFinalNavigation),
  /brandguard-missing-final-url\\.json \\(requested https:\\/\\/fixture\\.invalid\\/lens-system\\/okhp3-brandguard\\/; final unavailable; diagnostics: final displayed URL unavailable/,
);

const invalidFinalNavigation = createLighthouseNavigationReview("https://fixture.invalid", {
  "brandguard-invalid-final-url.json": {
    requestedUrl,
    report: { ...fixture, finalDisplayedUrl: "not a valid URL" },
  },
});
assert.equal(invalidFinalNavigation.status, "required");
assert.deepEqual(invalidFinalNavigation.changedReports, [
  "brandguard-invalid-final-url.json",
]);
assert.equal(
  invalidFinalNavigation.reports["brandguard-invalid-final-url.json"].url,
  null,
);
assert.match(
  formatLighthouseNavigationReviewNotice(invalidFinalNavigation),
  /brandguard-invalid-final-url\\.json \\(requested https:\\/\\/fixture\\.invalid\\/lens-system\\/okhp3-brandguard\\/; final unavailable; diagnostics: final displayed URL unavailable/,
);

const mixedFinalNavigation = createLighthouseNavigationReview("https://fixture.invalid", {
  "brandguard-canonical.json": {
    requestedUrl,
    report: { ...fixture, finalDisplayedUrl: requestedUrl.slice(0, -1) },
  },
  "brandguard-missing-final-url.json": {
    requestedUrl,
    report: missingFinalUrlReport,
  },
  "brandguard-invalid-final-url.json": {
    requestedUrl,
    report: { ...fixture, finalDisplayedUrl: "not a valid URL" },
  },
});
assert.equal(mixedFinalNavigation.status, "required");
assert.deepEqual(mixedFinalNavigation.changedReports, [
  "brandguard-missing-final-url.json",
  "brandguard-invalid-final-url.json",
]);
const mixedFinalNavigationNotice = formatLighthouseNavigationReviewNotice(
  mixedFinalNavigation,
);
assert.match(mixedFinalNavigationNotice, /brandguard-missing-final-url\\.json/);
assert.match(mixedFinalNavigationNotice, /brandguard-invalid-final-url\\.json/);
assert.doesNotMatch(mixedFinalNavigationNotice, /brandguard-canonical\\.json/);
"""
    result = subprocess.run(
        [node_bin, "--input-type=module", "-e", runner],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout == ""


@pytest.mark.parametrize(
    "base_url,reference_origin,expected_target_review,final_displayed_url,expected_navigation_review",
    [
        (
            "http://127.0.0.1:5000",
            "http://127.0.0.1:5000",
            "not-required",
            "http://127.0.0.1:5000/final/",
            "not-required",
        ),
        (
            "http://127.0.0.1:5000",
            "http://127.0.0.1:5000",
            "not-required",
            "https://redirect.invalid/final/",
            "required",
        ),
        (
            "http://127.0.0.1:6000",
            "http://127.0.0.1:5000",
            "required",
            "http://127.0.0.1:6000/final/",
            "not-required",
        ),
        (
            "https://fixture.invalid",
            "http://127.0.0.1:5000",
            "required",
            "https://fixture.invalid/final/",
            "not-required",
        ),
        (
            "http://127.0.0.1:5000",
            None,
            "required",
            "http://127.0.0.1:5000/final/",
            "not-required",
        ),
    ],
)
def test_lighthouse_cli_desktop_run_uses_the_desktop_reference(
    tmp_path,
    base_url,
    reference_origin,
    expected_target_review,
    final_displayed_url,
    expected_navigation_review,
):
    if os.name == "nt":
        pytest.skip("POSIX fake executables require shebang support unavailable on Windows")
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("Node.js is unavailable")

    root = tmp_path
    (root / "scripts").mkdir()
    shutil.copy2(ROOT / "scripts/lighthouse-routes.mjs", root / "scripts/lighthouse-routes.mjs")
    (root / "tests/fixtures").mkdir(parents=True)
    shutil.copy2(
        ROOT / "tests/fixtures/lighthouse-summary-report.json",
        root / "tests/fixtures/lighthouse-summary-report.json",
    )
    (root / "assets/docs").mkdir(parents=True)
    shutil.copy2(
        ROOT / "assets/docs/performance-baseline-desktop-2026-09-07.json",
        root / "assets/docs/performance-baseline-desktop-2026-09-07.json",
    )
    reference_path = root / "assets/docs/performance-baseline-desktop-2026-09-07.json"
    reference = json.loads(reference_path.read_text(encoding="utf-8"))
    reference["targetOrigin"] = reference_origin
    reference_path.write_text(json.dumps(reference), encoding="utf-8")
    (root / "assets/audit").mkdir(parents=True)
    lighthouse_dir = root / "node_modules/.bin"
    lighthouse_dir.mkdir(parents=True)
    fake_lighthouse = lighthouse_dir / "lighthouse"
    fake_lighthouse.write_text(
        """#!/usr/bin/env node
const fs = require("node:fs");
const path = require("node:path");
const args = process.argv.slice(2);
if (!args.includes("--chrome-flags=--headless --no-sandbox --disable-dev-shm-usage")) {
  process.exit(31);
}
const reportPath = args.find((arg) => arg.startsWith("--output-path=")).slice("--output-path=".length);
const report = JSON.parse(fs.readFileSync(
  path.join(process.cwd(), "tests/fixtures/lighthouse-summary-report.json"),
  "utf8"
));
const requestedUrl = new URL(args[0]);
const finalDisplayedUrl = new URL(process.env.FAKE_LIGHTHOUSE_FINAL_URL);
finalDisplayedUrl.pathname = requestedUrl.pathname;
finalDisplayedUrl.search = requestedUrl.search;
finalDisplayedUrl.hash = requestedUrl.hash;
report.finalDisplayedUrl = finalDisplayedUrl.href;
const repeatPass = Number(reportPath.match(/pass-(\\d+)/)?.[1] || 0);
if (repeatPass) {
  const offset = repeatPass - 1;
  report.categories.performance.score = 0.91 + offset * 0.01;
  report.audits["largest-contentful-paint"].numericValue += offset * 100;
  report.audits["first-contentful-paint"].numericValue += offset * 20;
  report.audits["speed-index"].numericValue += offset * 50;
  report.audits["total-blocking-time"].numericValue += offset * 10;
}
if (
  process.env.FAKE_MISSING_DESKTOP_REPEAT_METRIC === "1"
  && repeatPass === 2
  && requestedUrl.pathname === "/search/"
) {
  delete report.audits["speed-index"].numericValue;
}
const reference = JSON.parse(fs.readFileSync(
  path.join(process.cwd(), "assets/docs/performance-baseline-desktop-2026-09-07.json"),
  "utf8"
));
const conditions = reference.runConditions;
report.lighthouseVersion = "12.8.2";
report.userAgent = "Mozilla/5.0 HeadlessChrome/148.0.0.0";
report.environment = { hostUserAgent: "Mozilla/5.0 HeadlessChrome/148.0.0.0" };
report.configSettings = {
  formFactor: conditions.formFactor,
  throttlingMethod: conditions.throttlingMethod,
  throttling: conditions.throttling,
  screenEmulation: conditions.screenEmulation,
  emulatedUserAgent: conditions.emulatedUserAgent,
  blockedUrlPatterns: conditions.blockedUrlPatterns,
  ...conditions.collectionTiming,
  locale: conditions.locale,
  disableStorageReset: conditions.storage.disableStorageReset,
  clearStorageTypes: conditions.storage.clearStorageTypes,
  output: ["json"],
  ...conditions.additionalConfigSettings
};
fs.writeFileSync(reportPath, JSON.stringify(report));
""",
        encoding="utf-8",
    )
    fake_lighthouse.chmod(0o755)
    fake_chrome = root / "fake-chrome"
    fake_chrome.write_text(
        "#!/bin/sh\nprintf '%s\\n' 'Chromium 148.0.7778.96'\n",
        encoding="utf-8",
    )
    fake_chrome.chmod(0o755)

    result = subprocess.run(
        [
            node_bin,
            "scripts/lighthouse-routes.mjs",
            "--preset=desktop",
            f"--base-url={base_url}",
            "--date=2099-01-02",
            "--run-id=desktop-reference",
        ],
        cwd=root,
        env={
            **os.environ,
            "CHROME_PATH": str(fake_chrome),
            "FAKE_LIGHTHOUSE_FINAL_URL": final_displayed_url,
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    summary_path = (
        root
        / "assets/audit/lighthouse-2099-01-02-desktop-reference/summary.json"
    )
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["baseline"] == "assets/docs/performance-baseline-desktop-2026-09-07.json"
    run_status = json.loads(
        (summary_path.parent / "run-status.json").read_text(encoding="utf-8")
    )
    assert run_status["status"] == "completed"
    assert run_status["completedAt"]
    assert summary["measurementStackReview"]["status"] == "not-required"
    assert summary["sourceRevision"] is None
    assert summary["sourceWorkingTreeClean"] is None
    assert summary["sourceRevisionStatus"] == "unavailable"
    assert summary["sourceRevisionError"]
    assert summary["baselineSourceRevision"] is None
    assert summary["baselineSourceWorkingTreeClean"] is None
    assert summary["baselineSourceRevisionStatus"] == "not-recorded"
    expected_kind = "hosted" if base_url.startswith("https:") else "local"
    assert summary["environment"] == f"{expected_kind.title()} target, desktop preset"
    assert summary["targetReview"]["status"] == expected_target_review
    assert summary["targetReview"]["current"]["kind"] == expected_kind
    assert summary["navigationReview"]["status"] == expected_navigation_review
    assert summary["navigationReview"]["requestedOrigin"] == (
        base_url.rstrip("/") or base_url
    )
    assert all(
        urlsplit(report["requestedUrl"]).path == report["requestedPath"]
        and urlsplit(report["url"]).path == report["path"]
        and report["path"] == report["requestedPath"]
        and report["origin"] == (
            f"{urlsplit(final_displayed_url).scheme}://{urlsplit(final_displayed_url).netloc}"
        )
        for report in summary["navigationReview"]["reports"].values()
    )
    if expected_navigation_review == "required":
        assert result.stderr.count("LIGHTHOUSE NAVIGATION REVIEW REQUIRED:") == 1
        assert "final navigation origin differs from requested target" in result.stderr
        assert "brandguard.json" in result.stderr
    else:
        assert "LIGHTHOUSE NAVIGATION REVIEW REQUIRED:" not in result.stderr
    if expected_target_review == "required":
        assert result.stderr.count("LIGHTHOUSE TARGET REVIEW REQUIRED:") == 1
        assert summary["targetReview"]["action"] in result.stderr
        current_origin = summary["targetReview"]["current"]["origin"] or "unavailable"
        saved_reference_origin = summary["targetReview"]["baseline"]["origin"] or "unavailable"
        assert (
            f"Run origin: {current_origin}; "
            f"matching reference origin: {saved_reference_origin}."
        ) in result.stderr
        if reference_origin is None:
            assert summary["targetReview"]["changedComponents"] == [
                "reference origin unavailable"
            ]
    else:
        assert "LIGHTHOUSE TARGET REVIEW REQUIRED:" not in result.stderr
    assert "REFERENCE SOURCE REVISION NOT RECORDED:" in result.stderr
    assert summary["runConditionsReview"]["status"] == "required"
    assert summary["runConditionsReview"]["changedComponents"] == ["chromeFlags"]
    assert summary["runConditionsReview"]["baseline"]["formFactor"] == "desktop"
    assert summary["referenceApproval"]["status"] == "not-recorded"
    assert summary["pages"]["brandguard"]["deltaPerformance"] == -8
    assert summary["pages"]["brandguard"]["deltaLcpMs"] == 1616
    assert "DESKTOP REFERENCE NOT OWNER-APPROVED" in result.stderr

    reference_before_repeats = reference_path.read_bytes()
    repeat_result = subprocess.run(
        [
            node_bin,
            "scripts/lighthouse-routes.mjs",
            "--preset=desktop",
            "--desktop-repeats=3",
            f"--base-url={base_url}",
            "--date=2099-01-02",
            "--run-id=desktop-repeat",
        ],
        cwd=root,
        env={
            **os.environ,
            "CHROME_PATH": str(fake_chrome),
            "FAKE_LIGHTHOUSE_FINAL_URL": final_displayed_url,
        },
        capture_output=True,
        text=True,
    )
    assert repeat_result.returncode == 0, repeat_result.stderr
    repeat_output_dir = (
        root
        / "assets/audit/lighthouse-2099-01-02-desktop-repeats-3-desktop-repeat"
    )
    repeat_summary = json.loads(
        (repeat_output_dir / "summary.json").read_text(encoding="utf-8")
    )
    desktop_repeatability = repeat_summary["desktopRepeatability"]
    assert desktop_repeatability["preset"] == "desktop"
    assert desktop_repeatability["passCount"] == 3
    assert set(desktop_repeatability["routes"]) == {
        "homepage",
        "brandguard",
        "universe",
        "search",
    }
    assert "Desktop complete passes: 3" in repeat_result.stdout
    assert "Unavailable desktop repeat metrics" not in repeat_result.stderr
    for route_name in ("homepage", "brandguard", "universe", "search"):
        route_summary = desktop_repeatability["routes"][route_name]
        assert route_summary["metrics"]["performance"]["min"] == 91
        assert route_summary["metrics"]["performance"]["max"] == 93
        assert route_summary["metrics"]["lcpMs"]["min"] == 2345
        assert route_summary["metrics"]["lcpMs"]["max"] == 2545
        assert route_summary["metrics"]["fcpMs"]["min"] == 1234
        assert route_summary["metrics"]["fcpMs"]["max"] == 1274
        assert route_summary["metrics"]["speedIndexMs"]["min"] == 1790
        assert route_summary["metrics"]["speedIndexMs"]["max"] == 1890
        assert route_summary["metrics"]["tbtMs"]["min"] == 17
        assert route_summary["metrics"]["tbtMs"]["max"] == 37
        assert [sample["pass"] for sample in route_summary["passes"]] == [1, 2, 3]
        for sample in route_summary["passes"]:
            assert sample["unavailableMetrics"] == []
            raw_report = repeat_output_dir / sample["report"]
            assert raw_report.is_file()
            assert json.loads(raw_report.read_text(encoding="utf-8"))["lighthouseVersion"] == "12.8.2"
    assert len(repeat_summary["runConditions"]["reports"]) == 12
    assert reference_path.read_bytes() == reference_before_repeats

    missing_repeat_result = subprocess.run(
        [
            node_bin,
            "scripts/lighthouse-routes.mjs",
            "--preset=desktop",
            "--desktop-repeats=3",
            f"--base-url={base_url}",
            "--date=2099-01-02",
            "--run-id=desktop-repeat-missing",
        ],
        cwd=root,
        env={
            **os.environ,
            "CHROME_PATH": str(fake_chrome),
            "FAKE_LIGHTHOUSE_FINAL_URL": final_displayed_url,
            "FAKE_MISSING_DESKTOP_REPEAT_METRIC": "1",
        },
        capture_output=True,
        text=True,
    )
    assert missing_repeat_result.returncode == 0, missing_repeat_result.stderr
    assert (
        "Unavailable desktop repeat metrics; inspect the saved pass reports:\n"
        "  search pass 2 (pass-02/search.json): speedIndexMs"
    ) in missing_repeat_result.stderr
    missing_repeat_output_dir = (
        root
        / "assets/audit/lighthouse-2099-01-02-desktop-repeats-3-desktop-repeat-missing"
    )
    missing_repeat_summary = json.loads(
        (missing_repeat_output_dir / "summary.json").read_text(encoding="utf-8")
    )
    missing_search_pass = (
        missing_repeat_summary["desktopRepeatability"]["routes"]["search"]["passes"][1]
    )
    assert missing_search_pass["unavailableMetrics"] == [
        {
            "field": "speedIndexMs",
            "source": "audits.speed-index.numericValue",
        }
    ]

    malformed_approval_reference = json.loads(
        reference_path.read_text(encoding="utf-8")
    )
    malformed_approval_reference["approval"] = {
        "status": "owner-approved",
        "approvedAt": "2099-01-01",
        "approvedBy": "site owner",
        "approvalRecord": "docs/desktop-reference-approval.md",
        "integrityFingerprint": "sha256:not-a-valid-fingerprint",
    }
    reference_path.write_text(
        json.dumps(malformed_approval_reference),
        encoding="utf-8",
    )
    malformed_approval_run = subprocess.run(
        [
            node_bin,
            "scripts/lighthouse-routes.mjs",
            "--preset=desktop",
            f"--base-url={base_url}",
            "--date=2099-01-02",
            "--run-id=malformed-approval",
        ],
        cwd=root,
        env={
            **os.environ,
            "CHROME_PATH": str(fake_chrome),
            "FAKE_LIGHTHOUSE_FINAL_URL": final_displayed_url,
        },
        capture_output=True,
        text=True,
    )
    assert malformed_approval_run.returncode == 0, malformed_approval_run.stderr
    malformed_approval_summary_path = (
        root
        / "assets/audit/lighthouse-2099-01-02-malformed-approval/summary.json"
    )
    malformed_approval_summary = json.loads(
        malformed_approval_summary_path.read_text(encoding="utf-8")
    )
    approval_summary = malformed_approval_summary["referenceApproval"]
    assert approval_summary["status"] == "stale"
    assert approval_summary["ownerApproved"] is False
    assert approval_summary["approvedAt"] == "2099-01-01"
    assert approval_summary["approvedBy"] == "site owner"
    assert approval_summary["approvalRecord"] == "docs/desktop-reference-approval.md"
    assert approval_summary["approvalIntegrityFingerprint"] == (
        "sha256:not-a-valid-fingerprint"
    )
    assert "malformed integrity fingerprint" in approval_summary["action"]
    assert "fresh owner decision" in approval_summary["action"]
    assert "desktop reference changed" not in approval_summary["action"]

    for run_id, approval_status, expected_reason, remove_status in (
        ("unknown-approval-status", "reviewed-and-approved", "unsupported-status", False),
        ("numeric-approval-status", 17, "malformed-status", False),
        ("null-approval-status", None, "malformed-status", False),
        ("missing-approval-status", None, "malformed-status", True),
    ):
        if remove_status:
            malformed_approval_reference["approval"].pop("status", None)
        else:
            malformed_approval_reference["approval"]["status"] = approval_status
        reference_path.write_text(
            json.dumps(malformed_approval_reference),
            encoding="utf-8",
        )
        unfamiliar_status_run = subprocess.run(
            [
                node_bin,
                "scripts/lighthouse-routes.mjs",
                "--preset=desktop",
                f"--base-url={base_url}",
                "--date=2099-01-02",
                f"--run-id={run_id}",
            ],
            cwd=root,
            env={
                **os.environ,
                "CHROME_PATH": str(fake_chrome),
                "FAKE_LIGHTHOUSE_FINAL_URL": final_displayed_url,
            },
            capture_output=True,
            text=True,
        )
        assert unfamiliar_status_run.returncode == 0, unfamiliar_status_run.stderr
        unfamiliar_summary_path = (
            root / f"assets/audit/lighthouse-2099-01-02-{run_id}/summary.json"
        )
        unfamiliar_summary = json.loads(
            unfamiliar_summary_path.read_text(encoding="utf-8")
        )
        unfamiliar_approval = unfamiliar_summary["referenceApproval"]
        assert unfamiliar_approval["status"] == "stale"
        assert unfamiliar_approval["reason"] == expected_reason
        assert unfamiliar_approval["ownerApproved"] is False
        assert "not treated as owner-approved" in unfamiliar_approval["action"]
        assert "DESKTOP REFERENCE NOT OWNER-APPROVED" in unfamiliar_status_run.stderr
        assert unfamiliar_approval["action"] in unfamiliar_status_run.stderr
        assert unfamiliar_summary["pages"]["brandguard"]["deltaPerformance"] == -8

    malformed_approval_reference["approval"]["status"] = "owner-approved"
    reference_path.write_text(
        json.dumps(malformed_approval_reference),
        encoding="utf-8",
    )

    malformed_approval_repeat_run = subprocess.run(
        [
            node_bin,
            "scripts/lighthouse-routes.mjs",
            "--preset=desktop",
            "--desktop-repeats=3",
            f"--base-url={base_url}",
            "--date=2099-01-02",
            "--run-id=malformed-approval-repeat",
        ],
        cwd=root,
        env={
            **os.environ,
            "CHROME_PATH": str(fake_chrome),
            "FAKE_LIGHTHOUSE_FINAL_URL": final_displayed_url,
        },
        capture_output=True,
        text=True,
    )
    assert malformed_approval_repeat_run.returncode == 0, (
        malformed_approval_repeat_run.stderr
    )
    malformed_approval_repeat_summary_path = (
        root
        / "assets/audit/lighthouse-2099-01-02-desktop-repeats-3-malformed-approval-repeat/summary.json"
    )
    malformed_approval_repeat_summary = json.loads(
        malformed_approval_repeat_summary_path.read_text(encoding="utf-8")
    )
    assert malformed_approval_repeat_summary["desktopRepeatability"]["passCount"] == 3
    repeated_approval_summary = malformed_approval_repeat_summary["referenceApproval"]
    assert repeated_approval_summary["status"] == "stale"
    assert repeated_approval_summary["reason"] == "malformed"
    assert repeated_approval_summary["ownerApproved"] is False
    assert repeated_approval_summary["approvedAt"] == "2099-01-01"
    assert repeated_approval_summary["approvedBy"] == "site owner"
    assert repeated_approval_summary["approvalRecord"] == (
        "docs/desktop-reference-approval.md"
    )
    assert repeated_approval_summary["approvalIntegrityFingerprint"] == (
        "sha256:not-a-valid-fingerprint"
    )
    assert "malformed integrity fingerprint" in repeated_approval_summary["action"]
    assert "fresh owner decision" in repeated_approval_summary["action"]
    assert "desktop reference changed" not in repeated_approval_summary["action"]
    assert "DESKTOP REFERENCE NOT OWNER-APPROVED" in malformed_approval_repeat_run.stderr
    assert repeated_approval_summary["action"] in malformed_approval_repeat_run.stderr

    for malformed_fingerprint in (17, {"unexpected": "object"}):
        malformed_approval_reference["approval"]["integrityFingerprint"] = (
            malformed_fingerprint
        )
        reference_path.write_text(
            json.dumps(malformed_approval_reference),
            encoding="utf-8",
        )
        run_id = (
            "malformed-approval-number"
            if isinstance(malformed_fingerprint, int)
            else "malformed-approval-object"
        )
        malformed_typed_approval_run = subprocess.run(
            [
                node_bin,
                "scripts/lighthouse-routes.mjs",
                "--preset=desktop",
                f"--base-url={base_url}",
                "--date=2099-01-02",
                f"--run-id={run_id}",
            ],
            cwd=root,
            env={
                **os.environ,
                "CHROME_PATH": str(fake_chrome),
                "FAKE_LIGHTHOUSE_FINAL_URL": final_displayed_url,
            },
            capture_output=True,
            text=True,
        )
        assert malformed_typed_approval_run.returncode == 0, (
            malformed_typed_approval_run.stderr
        )
        malformed_typed_summary_path = (
            root / f"assets/audit/lighthouse-2099-01-02-{run_id}/summary.json"
        )
        malformed_typed_summary = json.loads(
            malformed_typed_summary_path.read_text(encoding="utf-8")
        )
        typed_approval_summary = malformed_typed_summary["referenceApproval"]
        assert typed_approval_summary["status"] == "stale"
        assert typed_approval_summary["reason"] == "malformed"
        assert typed_approval_summary["ownerApproved"] is False
        assert (
            typed_approval_summary["approvalIntegrityFingerprint"]
            == malformed_fingerprint
        )
        assert "malformed integrity fingerprint" in typed_approval_summary["action"]
        assert "fresh owner decision" in typed_approval_summary["action"]
        assert "desktop reference changed" not in typed_approval_summary["action"]

    mobile_repeat = subprocess.run(
        [
            node_bin,
            "scripts/lighthouse-routes.mjs",
            "--preset=mobile",
            "--desktop-repeats=3",
            "--date=2099-01-02",
            "--run-id=desktop-repeat-rejected",
        ],
        cwd=root,
        env={**os.environ, "CHROME_PATH": str(fake_chrome)},
        capture_output=True,
        text=True,
    )
    assert mobile_repeat.returncode != 0
    assert "only supported with --preset=desktop" in mobile_repeat.stderr


@pytest.mark.parametrize(
    "run_id,run_args,fail_report_suffix,expected_reports",
    [
        ("route-failure", [], "brandguard.json", ["homepage.json"]),
        (
            "repeat-failure",
            ["--desktop-repeats=2"],
            "pass-02/brandguard.json",
            [
                "pass-01/homepage.json",
                "pass-01/brandguard.json",
                "pass-01/universe.json",
                "pass-01/search.json",
                "pass-02/homepage.json",
            ],
        ),
    ],
)
def test_lighthouse_failed_run_is_marked_incomplete_without_browser(
    tmp_path, run_id, run_args, fail_report_suffix, expected_reports
):
    if os.name == "nt":
        pytest.skip("POSIX fake executables require shebang support unavailable on Windows")
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("Node.js is unavailable")

    root = tmp_path
    (root / "scripts").mkdir()
    shutil.copy2(ROOT / "scripts/lighthouse-routes.mjs", root / "scripts/lighthouse-routes.mjs")
    (root / "tests/fixtures").mkdir(parents=True)
    shutil.copy2(
        ROOT / "tests/fixtures/lighthouse-summary-report.json",
        root / "tests/fixtures/lighthouse-summary-report.json",
    )
    (root / "assets/docs").mkdir(parents=True)
    shutil.copy2(
        ROOT / "assets/docs/performance-baseline-desktop-2026-09-07.json",
        root / "assets/docs/performance-baseline-desktop-2026-09-07.json",
    )
    (root / "assets/audit").mkdir(parents=True)
    lighthouse_dir = root / "node_modules/.bin"
    lighthouse_dir.mkdir(parents=True)
    fake_lighthouse = lighthouse_dir / "lighthouse"
    fake_lighthouse.write_text(
        """#!/usr/bin/env node
const fs = require("node:fs");
const path = require("node:path");
const args = process.argv.slice(2);
const reportPath = args.find((arg) => arg.startsWith("--output-path="))
  .slice("--output-path=".length);
if (reportPath.endsWith(process.env.FAIL_REPORT_SUFFIX)) process.exit(42);
const report = JSON.parse(fs.readFileSync(
  path.join(process.cwd(), "tests/fixtures/lighthouse-summary-report.json"),
  "utf8"
));
const reference = JSON.parse(fs.readFileSync(
  path.join(process.cwd(), "assets/docs/performance-baseline-desktop-2026-09-07.json"),
  "utf8"
));
const conditions = reference.runConditions;
report.lighthouseVersion = "12.8.2";
report.userAgent = "Mozilla/5.0 HeadlessChrome/148.0.0.0";
report.environment = { hostUserAgent: "Mozilla/5.0 HeadlessChrome/148.0.0.0" };
report.configSettings = {
  formFactor: conditions.formFactor,
  throttlingMethod: conditions.throttlingMethod,
  throttling: conditions.throttling,
  screenEmulation: conditions.screenEmulation,
  emulatedUserAgent: conditions.emulatedUserAgent,
  blockedUrlPatterns: conditions.blockedUrlPatterns,
  ...conditions.collectionTiming,
  locale: conditions.locale,
  disableStorageReset: conditions.storage.disableStorageReset,
  clearStorageTypes: conditions.storage.clearStorageTypes,
  output: ["json"],
  ...conditions.additionalConfigSettings
};
fs.writeFileSync(reportPath, JSON.stringify(report));
""",
        encoding="utf-8",
    )
    fake_lighthouse.chmod(0o755)
    fake_chrome = root / "fake-chrome"
    fake_chrome.write_text(
        "#!/bin/sh\nprintf '%s\\n' 'Chromium 148.0.7778.96'\n",
        encoding="utf-8",
    )
    fake_chrome.chmod(0o755)

    result = subprocess.run(
        [
            node_bin,
            "scripts/lighthouse-routes.mjs",
            "--preset=desktop",
            *run_args,
            "--date=2099-01-02",
            f"--run-id={run_id}",
        ],
        cwd=root,
        env={
            **os.environ,
            "CHROME_PATH": str(fake_chrome),
            "FAIL_REPORT_SUFFIX": fail_report_suffix,
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    output_suffix = "-desktop-repeats-2" if run_args else ""
    output_dir = root / "assets/audit" / f"lighthouse-2099-01-02{output_suffix}-{run_id}"
    run_status = json.loads(
        (output_dir / "run-status.json").read_text(encoding="utf-8")
    )
    assert run_status["status"] == "incomplete"
    assert run_status["finishedAt"]
    assert run_status["error"]
    assert not (output_dir / "summary.json").exists()
    actual_reports = sorted(
        path.relative_to(output_dir).as_posix()
        for path in output_dir.rglob("*.json")
        if path.name != "run-status.json"
    )
    assert actual_reports == sorted(expected_reports)


@pytest.mark.parametrize(
    "run_args,run_id,malformed_report,prior_report",
    [
        ([], "malformed-report", "brandguard.json", "homepage.json"),
        (
            ["--desktop-repeats=2"],
            "malformed-repeat-report",
            "pass-02/brandguard.json",
            "pass-02/homepage.json",
        ),
    ],
)
def test_lighthouse_malformed_report_marks_run_incomplete_and_preserves_prior_evidence(
    tmp_path, run_args, run_id, malformed_report, prior_report
):
    if os.name == "nt":
        pytest.skip("POSIX fake executables require shebang support unavailable on Windows")
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("Node.js is unavailable")

    root = tmp_path
    (root / "scripts").mkdir()
    shutil.copy2(ROOT / "scripts/lighthouse-routes.mjs", root / "scripts/lighthouse-routes.mjs")
    (root / "tests/fixtures").mkdir(parents=True)
    shutil.copy2(
        ROOT / "tests/fixtures/lighthouse-summary-report.json",
        root / "tests/fixtures/lighthouse-summary-report.json",
    )
    (root / "assets/docs").mkdir(parents=True)
    shutil.copy2(
        ROOT / "assets/docs/performance-baseline-desktop-2026-09-07.json",
        root / "assets/docs/performance-baseline-desktop-2026-09-07.json",
    )
    (root / "assets/audit").mkdir(parents=True)
    lighthouse_dir = root / "node_modules/.bin"
    lighthouse_dir.mkdir(parents=True)
    prior_report_backup = root / "prior-homepage-report.json"
    fake_lighthouse = lighthouse_dir / "lighthouse"
    fake_lighthouse.write_text(
        """#!/usr/bin/env node
const fs = require("node:fs");
const path = require("node:path");
const args = process.argv.slice(2);
const reportPath = args.find((arg) => arg.startsWith("--output-path="))
  .slice("--output-path=".length);
if (reportPath.endsWith(process.env.MALFORMED_REPORT_SUFFIX)) {
  fs.writeFileSync(reportPath, "{ malformed Lighthouse JSON");
  process.exit(0);
}
const report = JSON.parse(fs.readFileSync(
  path.join(process.cwd(), "tests/fixtures/lighthouse-summary-report.json"),
  "utf8"
));
const reference = JSON.parse(fs.readFileSync(
  path.join(process.cwd(), "assets/docs/performance-baseline-desktop-2026-09-07.json"),
  "utf8"
));
const conditions = reference.runConditions;
report.lighthouseVersion = "12.8.2";
report.userAgent = "Mozilla/5.0 HeadlessChrome/148.0.0.0";
report.environment = { hostUserAgent: "Mozilla/5.0 HeadlessChrome/148.0.0.0" };
report.configSettings = {
  formFactor: conditions.formFactor,
  throttlingMethod: conditions.throttlingMethod,
  throttling: conditions.throttling,
  screenEmulation: conditions.screenEmulation,
  emulatedUserAgent: conditions.emulatedUserAgent,
  blockedUrlPatterns: conditions.blockedUrlPatterns,
  ...conditions.collectionTiming,
  locale: conditions.locale,
  disableStorageReset: conditions.storage.disableStorageReset,
  clearStorageTypes: conditions.storage.clearStorageTypes,
  output: ["json"],
  ...conditions.additionalConfigSettings
};
fs.writeFileSync(reportPath, JSON.stringify(report));
fs.copyFileSync(reportPath, process.env.PRIOR_REPORT_BACKUP);
""",
        encoding="utf-8",
    )
    fake_lighthouse.chmod(0o755)
    fake_chrome = root / "fake-chrome"
    fake_chrome.write_text(
        "#!/bin/sh\nprintf '%s\\n' 'Chromium 148.0.7778.96'\n",
        encoding="utf-8",
    )
    fake_chrome.chmod(0o755)

    result = subprocess.run(
        [
            node_bin,
            "scripts/lighthouse-routes.mjs",
            "--preset=desktop",
            *run_args,
            "--date=2099-01-02",
            f"--run-id={run_id}",
        ],
        cwd=root,
        env={
            **os.environ,
            "CHROME_PATH": str(fake_chrome),
            "PRIOR_REPORT_BACKUP": str(prior_report_backup),
            "MALFORMED_REPORT_SUFFIX": malformed_report,
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    repeat_suffix = "-desktop-repeats-2" if run_args else ""
    output_dir = root / "assets/audit" / (
        f"lighthouse-2099-01-02{repeat_suffix}-{run_id}"
    )
    status = json.loads((output_dir / "run-status.json").read_text(encoding="utf-8"))
    assert status["status"] == "incomplete"
    assert status["finishedAt"]
    assert status["error"].startswith(
        f"Could not parse Lighthouse report for brandguard ({malformed_report}):"
    )
    homepage_report = output_dir / prior_report
    assert homepage_report.read_bytes() == prior_report_backup.read_bytes()
    assert (output_dir / malformed_report).read_text(encoding="utf-8") == (
        "{ malformed Lighthouse JSON"
    )
    assert not (output_dir / "summary.json").exists()


def test_lighthouse_summary_fixture_covers_normal_and_controlled_outputs_without_browser(tmp_path):
    fixture = ROOT / "tests/fixtures/lighthouse-summary-report.json"
    assert fixture.is_file()
    expected_routes = {
        "homepage": "/",
        "brandguard": "/lens-system/okhp3-brandguard/",
        "universe": "/universe/",
        "search": "/search/",
    }
    runner = tmp_path / "summary-fixture.mjs"
    runner.write_text(
        """
import { readFileSync } from "node:fs";
import { defaultSettings as lighthouseDefaultSettings } from "./node_modules/lighthouse/core/config/constants.js";
import { CHROME_STARTUP_FLAGS, CONTROLLED_BLOCKED_URL_PATTERNS, HISTORICAL_REFERENCE_MEASUREMENT_STACK, HISTORICAL_REFERENCE_RUN_CONDITIONS, HISTORICAL_REFERENCE_TARGET_ORIGIN, LIGHTHOUSE_CONFIG_SETTINGS_INVENTORY, createSummary, formatLighthouseTargetReviewNotice, formatRunConditionsReviewNotice, getLighthouseReference, LIGHTHOUSE_ROUTES, parseChromiumVersion, summarizePage, summarizeRunConditions } from "./scripts/lighthouse-routes.mjs";

const report = JSON.parse(readFileSync("tests/fixtures/lighthouse-summary-report.json", "utf8"));
const fixtureConfigSettingKeys = Object.keys(report.configSettings).sort();
const inventoriedConfigSettingKeys = [
  ...LIGHTHOUSE_CONFIG_SETTINGS_INVENTORY.tracked,
  ...Object.keys(LIGHTHOUSE_CONFIG_SETTINGS_INVENTORY.recordedWithoutComparison)
].sort();
const lighthouseSchemaConfigSettingKeys = Object.keys(lighthouseDefaultSettings).sort();
const baseline = { pages: {
  homepage: { performance: 80, lcpMs: 1000 },
  brandguard: { performance: 88, lcpMs: 2000 },
  universe: { performance: 90, lcpMs: 2500 },
  search: { performance: 91, lcpMs: 3000 }
}, measurementStack: HISTORICAL_REFERENCE_MEASUREMENT_STACK };
const baselineRunConditions = summarizeRunConditions(report, CHROME_STARTUP_FLAGS);
const matchingReferenceRunConditions = {
  ...HISTORICAL_REFERENCE_RUN_CONDITIONS,
  chromeFlags: [...CHROME_STARTUP_FLAGS]
};
const allBaselineRunConditions = Object.fromEntries(
  Object.keys(LIGHTHOUSE_ROUTES).map((name) => [`${name}.json`, baselineRunConditions])
);
const controlledRunConditions = Object.fromEntries(
  Object.entries(allBaselineRunConditions).map(([name, conditions]) => [
    name,
    { ...conditions, blockedUrlPatterns: CONTROLLED_BLOCKED_URL_PATTERNS }
  ])
);
const mobileReference = getLighthouseReference("mobile", baseline);
const desktopBaseline = JSON.parse(readFileSync(
  "assets/docs/performance-baseline-desktop-2026-09-07.json",
  "utf8"
));
const independentDesktopBaseline = {
  ...desktopBaseline,
  targetOrigin: "https://desktop-reference.invalid"
};
const desktopReference = getLighthouseReference("desktop", independentDesktopBaseline);
const emit = (
  controlled,
  measurementStack,
  runConditionsReports = controlled ? controlledRunConditions : allBaselineRunConditions,
  referenceRunConditions = matchingReferenceRunConditions
) => {
  const summary = createSummary({
    date: "2099-01-02",
    preset: "mobile",
    controlled,
    baseUrl: "https://fixture.invalid",
    measurementStack,
    baselineMeasurementStack: baseline.measurementStack,
    runConditionsReports,
    baselineRunConditions: referenceRunConditions,
    baselineTargetOrigin: mobileReference.targetOrigin
  });
  for (const [name, path] of Object.entries(LIGHTHOUSE_ROUTES)) {
    summary.pages[name] = summarizePage({
      report,
      path,
      baselinePage: baseline.pages[name]
    });
  }
  return summary;
};
const targetSummary = (
  baseUrl,
  baselineTargetOrigin = HISTORICAL_REFERENCE_TARGET_ORIGIN,
  preset = "desktop"
) =>
  createSummary({
    date: "2099-01-02",
    preset,
    controlled: false,
    baseUrl,
    measurementStack: baseline.measurementStack,
    baselineMeasurementStack: baseline.measurementStack,
    runConditionsReports: allBaselineRunConditions,
    baselineRunConditions: matchingReferenceRunConditions,
    baselineTargetOrigin
  });
const changedRunConditions = {
  ...baselineRunConditions,
  collectionTiming: {
    ...baselineRunConditions.collectionTiming,
    pauseAfterFcpMs: 1250
  },
  locale: "fr-FR",
  storage: {
    ...baselineRunConditions.storage,
    disableStorageReset: true
  },
  throttling: {
    ...baselineRunConditions.throttling,
    cpuSlowdownMultiplier: 5
  },
  additionalConfigSettings: {
    ...baselineRunConditions.additionalConfigSettings,
    channel: "node"
  }
};
const outputFormatChange = {
  ...allBaselineRunConditions,
  "homepage.json": {
    ...baselineRunConditions,
    nonComparabilitySettings: { output: ["html"] }
  }
};
const reportWithUnknownSetting = {
  ...report,
  configSettings: {
    ...report.configSettings,
    futureNetworkSetting: "fast"
  }
};
const unknownSettingChange = {
  ...allBaselineRunConditions,
  "homepage.json": summarizeRunConditions(reportWithUnknownSetting, CHROME_STARTUP_FLAGS)
};
const changedChromeFlags = {
  ...allBaselineRunConditions,
  "brandguard-sample-02.json": {
    ...baselineRunConditions,
    chromeFlags: ["--headless", "--no-sandbox", "--disable-gpu"]
  }
};
const changedRouteChromeFlags = {
  ...allBaselineRunConditions,
  "brandguard.json": {
    ...baselineRunConditions,
    chromeFlags: ["--headless", "--no-sandbox", "--disable-gpu"]
  }
};
const changedMixedChromeFlags = {
  ...allBaselineRunConditions,
  "brandguard.json": {
    ...baselineRunConditions,
    chromeFlags: ["--headless", "--no-sandbox", "--disable-gpu"]
  },
  "brandguard-sample-02.json": {
    ...baselineRunConditions,
    chromeFlags: ["--headless", "--no-sandbox", "--disable-gpu"]
  }
};
const changedMixedSettings = {
  ...allBaselineRunConditions,
  "brandguard.json": {
    ...baselineRunConditions,
    locale: "fr-FR"
  },
  "search.json": {
    ...baselineRunConditions,
    formFactor: "desktop"
  },
  "brandguard-sample-02.json": {
    ...baselineRunConditions,
    collectionTiming: {
      ...baselineRunConditions.collectionTiming,
      pauseAfterFcpMs: 1250
    }
  },
  "brandguard-sample-04.json": {
    ...baselineRunConditions,
    locale: "de-DE"
  }
};
const changedMixedSettingsReordered = Object.fromEntries(
  Object.entries(changedMixedSettings).reverse()
);
const changedRouteLocale = {
  ...allBaselineRunConditions,
  "brandguard.json": {
    ...baselineRunConditions,
    locale: "fr-FR"
  }
};
const changedRepeatedSampleTiming = {
  ...allBaselineRunConditions,
  "brandguard-sample-04.json": {
    ...baselineRunConditions,
    collectionTiming: {
      ...baselineRunConditions.collectionTiming,
      pauseAfterFcpMs: 1250
    }
  }
};
const missingChromeFlags = {
  ...allBaselineRunConditions,
  "brandguard-sample-03.json": summarizeRunConditions(report, null)
};
const mixedChromeFlagAvailability = {
  "brandguard.json": {
    ...baselineRunConditions,
    chromeFlags: ["--headless", "--no-sandbox", "--disable-gpu"]
  },
  "brandguard-sample-02.json": {
    ...baselineRunConditions,
    chromeFlags: "unavailable"
  },
  "homepage.json": baselineRunConditions
};
const implicitChromeFlags = summarizeRunConditions(report);
process.stdout.write(JSON.stringify({
  routes: LIGHTHOUSE_ROUTES,
  referenceTargets: {
    mobile: mobileReference.targetOrigin,
    desktop: desktopReference.targetOrigin
  },
  targetCases: {
    localAlias: targetSummary("http://localhost:5000"),
    localOriginChange: targetSummary("http://localhost:6000"),
    localSameOrigin: targetSummary(
      "http://localhost:5000/run",
      "http://localhost:5000"
    ),
    missingReference: targetSummary("http://localhost:5000", null),
    invalidReference: targetSummary("http://localhost:5000", "ftp://localhost:5000"),
    mobileMatchingReference: targetSummary(
      HISTORICAL_REFERENCE_TARGET_ORIGIN,
      mobileReference.targetOrigin,
      "mobile"
    ),
    desktopMatchingReference: targetSummary(
      desktopReference.targetOrigin,
      desktopReference.targetOrigin,
      "desktop"
    ),
    mobileAgainstDesktop: targetSummary(
      desktopReference.targetOrigin,
      mobileReference.targetOrigin,
      "mobile"
    ),
    desktopAgainstMobile: targetSummary(
      mobileReference.targetOrigin,
      desktopReference.targetOrigin,
      "desktop"
    ),
    hostedAgainstLocal: targetSummary("https://askjamie.bot"),
    hostedOriginChange: targetSummary("https://staging.askjamie.bot", "https://askjamie.bot")
  },
  targetReviewNotices: {
    currentUnavailable: formatLighthouseTargetReviewNotice({
      status: "required",
      changedComponents: ["current target unavailable"],
      current: { origin: null },
      baseline: { origin: "https://askjamie.bot" }
    }),
    referenceUnavailable: formatLighthouseTargetReviewNotice(
      targetSummary("https://fixture.invalid", null).targetReview
    ),
    notRequired: formatLighthouseTargetReviewNotice({
      status: "not-required"
    })
  },
  fixtureConfigSettingKeys,
  inventoriedConfigSettingKeys,
  lighthouseSchemaConfigSettingKeys,
  parsedChromiumVersion: parseChromiumVersion("Google Chrome for Testing 153.0.8010.12"),
  normal: emit(false, baseline.measurementStack),
  lighthouseUpgrade: emit(false, {
    ...baseline.measurementStack,
    lighthouseVersion: "13.5.0"
  }),
  chromiumUpgrade: emit(false, {
    ...baseline.measurementStack,
    chromiumVersion: "153.0.8010.12",
    chromiumUserAgent: "HeadlessChrome/153.0.0.0"
  }),
  chromiumPatchChange: emit(false, {
    ...baseline.measurementStack,
    chromiumVersion: "148.0.7778.97"
  }),
  runConditionChange: emit(false, baseline.measurementStack, {
    ...allBaselineRunConditions,
    "brandguard.json": changedRunConditions,
    "search.json": {
      ...baselineRunConditions,
      formFactor: "desktop"
    }
  }),
  changedChromeFlags: emit(false, baseline.measurementStack, changedChromeFlags),
  changedRouteChromeFlags: emit(
    false,
    baseline.measurementStack,
    changedRouteChromeFlags
  ),
  missingChromeFlags: emit(false, baseline.measurementStack, missingChromeFlags),
  implicitChromeFlags,
  runConditionNotices: {
    matching: formatRunConditionsReviewNotice(emit(false, baseline.measurementStack).runConditionsReview),
    changedRoute: formatRunConditionsReviewNotice(
      emit(false, baseline.measurementStack, changedRouteChromeFlags).runConditionsReview
    ),
    changedMixedChromeFlags: formatRunConditionsReviewNotice(
      emit(false, baseline.measurementStack, changedMixedChromeFlags).runConditionsReview
    ),
    changedMixedSettings: formatRunConditionsReviewNotice(
      emit(false, baseline.measurementStack, changedMixedSettings).runConditionsReview
    ),
    changedMixedSettingsReordered: formatRunConditionsReviewNotice(
      emit(
        false,
        baseline.measurementStack,
        changedMixedSettingsReordered
      ).runConditionsReview
    ),
    changedNonChromeRoute: formatRunConditionsReviewNotice(
      emit(false, baseline.measurementStack, changedRouteLocale).runConditionsReview
    ),
    changedRepeatedSample: formatRunConditionsReviewNotice(
      emit(false, baseline.measurementStack, changedChromeFlags).runConditionsReview
    ),
    changedNonChromeRepeatedSample: formatRunConditionsReviewNotice(
      emit(false, baseline.measurementStack, changedRepeatedSampleTiming).runConditionsReview
    ),
    unavailableRepeatedSample: formatRunConditionsReviewNotice(
      emit(false, baseline.measurementStack, missingChromeFlags).runConditionsReview
    ),
    mixedChromeFlagAvailability: formatRunConditionsReviewNotice(
      emit(false, baseline.measurementStack, mixedChromeFlagAvailability).runConditionsReview
    )
  },
  missingReferenceChromeFlags: emit(
    false,
    baseline.measurementStack,
    allBaselineRunConditions,
    HISTORICAL_REFERENCE_RUN_CONDITIONS
  ),
  outputFormatChange: emit(false, baseline.measurementStack, outputFormatChange),
  unknownSettingChange: emit(false, baseline.measurementStack, unknownSettingChange),
  unknownSettingWithMatchingReference: emit(
    false,
    baseline.measurementStack,
    unknownSettingChange,
    {
      ...matchingReferenceRunConditions,
      unclassifiedConfigSettings: { futureNetworkSetting: "fast" }
    }
  ),
  missingRunConditions: emit(false, baseline.measurementStack, {
  "homepage.json": summarizeRunConditions({})
  }),
  controlled: emit(true, {
    lighthouseVersion: "13.5.0",
    chromiumVersion: "153.0.8010.12",
    chromiumUserAgent: "HeadlessChrome/153.0.0.0"
  })
}));
""".strip()
        + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["node", "--input-type=module", "-e", runner.read_text(encoding="utf-8")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    emitted = json.loads(result.stdout)
    assert not list((ROOT / "assets/audit").glob("lighthouse-2099-01-02*"))
    assert emitted["routes"] == expected_routes
    assert emitted["referenceTargets"] == {
        "mobile": "http://127.0.0.1:5000",
        "desktop": "https://desktop-reference.invalid",
    }

    normal = emitted["normal"]
    assert emitted["parsedChromiumVersion"] == "153.0.8010.12"
    assert normal["schemaVersion"] == 12
    assert normal["capturedAt"] == "2099-01-02"
    assert emitted["fixtureConfigSettingKeys"] == emitted["inventoriedConfigSettingKeys"]
    assert emitted["lighthouseSchemaConfigSettingKeys"] == emitted["inventoriedConfigSettingKeys"]
    assert normal["tool"] == "Lighthouse 12.8.2"
    assert normal["measurementStack"] == {
        "lighthouseVersion": "12.8.2",
        "chromiumVersion": "148.0.7778.96",
        "chromiumUserAgent": "HeadlessChrome/148.0.0.0",
    }
    assert normal["measurementStackReview"] == {
        "baseline": {
            "lighthouseVersion": "12.8.2",
            "chromiumVersion": "148.0.7778.96",
            "chromiumUserAgent": "HeadlessChrome/148.0.0.0",
        },
        "referenceNote": (
            "Historical reference only. The exact Chromium 148.0.7778.96 build is inferred from "
            "Playwright metadata; historical reports establish major version 148, not an independently "
            "owner-approved exact build."
        ),
        "status": "not-required",
        "changedComponents": [],
        "action": "No Lighthouse or Chromium version change from the historical reference measurement stack.",
    }
    assert normal["environment"] == "Hosted target, mobile preset"
    assert normal["targetReview"]["status"] == "required"
    assert normal["targetReview"]["changedComponents"] == ["origin", "targetKind"]
    assert emitted["targetReviewNotices"] == {
        "currentUnavailable": (
            "LIGHTHOUSE TARGET REVIEW REQUIRED: current target unavailable. "
            "Run origin: unavailable; matching reference origin: https://askjamie.bot."
        ),
        "referenceUnavailable": (
            "LIGHTHOUSE TARGET REVIEW REQUIRED: reference origin unavailable. "
            "Run origin: https://fixture.invalid; matching reference origin: unavailable."
        ),
        "notRequired": None,
    }
    assert emitted["targetCases"]["localAlias"]["targetReview"]["changedComponents"] == [
        "origin",
    ]
    assert emitted["targetCases"]["localAlias"]["targetReview"]["current"]["kind"] == "local"
    assert emitted["targetCases"]["localOriginChange"]["targetReview"]["changedComponents"] == [
        "origin",
    ]
    assert emitted["targetCases"]["localSameOrigin"]["targetReview"]["status"] == "not-required"
    assert emitted["targetCases"]["missingReference"]["targetReview"]["changedComponents"] == [
        "reference origin unavailable",
    ]
    assert emitted["targetCases"]["invalidReference"]["targetReview"]["changedComponents"] == [
        "reference origin unavailable",
    ]
    mobile_target_review = emitted["targetCases"]["mobileMatchingReference"]
    assert mobile_target_review["environment"] == "Local target, mobile preset"
    assert mobile_target_review["targetReview"]["status"] == "not-required"
    assert mobile_target_review["targetReview"]["baseline"]["origin"] == (
        emitted["referenceTargets"]["mobile"]
    )
    desktop_target_review = emitted["targetCases"]["desktopMatchingReference"]
    assert desktop_target_review["environment"] == "Hosted target, desktop preset"
    assert desktop_target_review["targetReview"]["status"] == "not-required"
    assert desktop_target_review["targetReview"]["baseline"]["origin"] == (
        emitted["referenceTargets"]["desktop"]
    )
    assert emitted["targetCases"]["mobileAgainstDesktop"]["targetReview"]["changedComponents"] == [
        "origin",
        "targetKind",
    ]
    assert emitted["targetCases"]["desktopAgainstMobile"]["targetReview"]["changedComponents"] == [
        "origin",
        "targetKind",
    ]
    assert emitted["targetCases"]["hostedAgainstLocal"]["targetReview"]["changedComponents"] == [
        "origin",
        "targetKind",
    ]
    assert emitted["targetCases"]["hostedOriginChange"]["targetReview"]["changedComponents"] == [
        "origin",
    ]
    assert normal["runConditions"]["condition"] == "normal"
    assert normal["runConditions"]["reports"]["homepage.json"]["formFactor"] == "mobile"
    assert normal["runConditions"]["reports"]["homepage.json"]["chromeFlags"] == [
        "--headless",
        "--no-sandbox",
        "--disable-dev-shm-usage",
    ]
    assert normal["runConditions"]["reports"]["homepage.json"]["blockedUrlPatterns"] is None
    assert normal["runConditions"]["reports"]["homepage.json"]["throttling"] == {
        "rttMs": 150,
        "throughputKbps": 1638.4,
        "requestLatencyMs": 562.5,
        "downloadThroughputKbps": 1474.5600000000002,
        "uploadThroughputKbps": 675,
        "cpuSlowdownMultiplier": 4,
    }
    assert normal["runConditions"]["reports"]["homepage.json"]["collectionTiming"] == {
        "maxWaitForFcp": 30000,
        "maxWaitForLoad": 45000,
        "pauseAfterFcpMs": 1000,
        "pauseAfterLoadMs": 1000,
        "networkQuietThresholdMs": 1000,
        "cpuQuietThresholdMs": 1000,
    }
    assert normal["runConditions"]["reports"]["homepage.json"]["locale"] == "en-US"
    assert normal["runConditions"]["reports"]["homepage.json"]["storage"] == {
        "disableStorageReset": False,
        "clearStorageTypes": [
            "file_systems",
            "shader_cache",
            "service_workers",
            "cache_storage",
        ],
    }
    assert normal["runConditions"]["reports"]["homepage.json"]["additionalConfigSettings"] == {
        "auditMode": False,
        "gatherMode": False,
        "debugNavigation": False,
        "channel": "cli",
        "usePassiveGathering": False,
        "disableFullPageScreenshot": False,
        "skipAboutBlank": False,
        "blankPage": "about:blank",
        "ignoreStatusCode": False,
        "additionalTraceCategories": None,
        "extraHeaders": None,
        "precomputedLanternData": None,
        "onlyAudits": None,
        "onlyCategories": None,
        "skipAudits": None,
    }
    assert normal["runConditions"]["reports"]["homepage.json"]["nonComparabilitySettings"] == {
        "output": ["json"]
    }
    assert normal["runConditions"]["reports"]["homepage.json"]["unclassifiedConfigSettings"] == {}
    assert normal["runConditionsReview"]["status"] == "not-required"
    assert normal["runConditionsReview"]["changedComponents"] == []
    assert normal["runConditionsReview"]["changedReports"] == []
    assert normal["controls"] == {
        "thirdPartyFonts": "in flight",
        "analytics": "in flight",
        "interpretation": "No third-party isolation applied.",
    }

    lighthouse_upgrade = emitted["lighthouseUpgrade"]
    assert lighthouse_upgrade["measurementStackReview"]["status"] == "required"
    assert lighthouse_upgrade["measurementStackReview"]["changedComponents"] == [
        "lighthouseVersion",
    ]
    chromium_upgrade = emitted["chromiumUpgrade"]
    assert chromium_upgrade["measurementStackReview"]["status"] == "required"
    assert chromium_upgrade["measurementStackReview"]["changedComponents"] == [
        "chromiumVersion",
    ]
    chromium_patch_change = emitted["chromiumPatchChange"]
    assert chromium_patch_change["measurementStackReview"]["status"] == "required"
    assert chromium_patch_change["measurementStackReview"]["changedComponents"] == [
        "chromiumVersion",
    ]

    run_condition_change = emitted["runConditionChange"]
    assert run_condition_change["measurementStackReview"]["status"] == "not-required"
    assert run_condition_change["runConditionsReview"]["status"] == "required"
    assert run_condition_change["runConditionsReview"]["changedComponents"] == [
        "additionalConfigSettings.channel",
        "collectionTiming.pauseAfterFcpMs",
        "formFactor",
        "locale",
        "storage.disableStorageReset",
        "throttling.cpuSlowdownMultiplier",
    ]
    assert run_condition_change["runConditionsReview"]["changedReports"] == [{
        "report": "brandguard.json",
        "changedComponents": [
            "additionalConfigSettings.channel",
            "collectionTiming.pauseAfterFcpMs",
            "locale",
            "storage.disableStorageReset",
            "throttling.cpuSlowdownMultiplier",
        ],
    }, {
        "report": "search.json",
        "changedComponents": ["formFactor"],
    }]
    assert "Owner review is required" in run_condition_change["runConditionsReview"]["action"]
    assert emitted["runConditionNotices"]["matching"] is None

    output_change = emitted["outputFormatChange"]
    assert output_change["runConditionsReview"]["status"] == "not-required"
    assert output_change["runConditionsReview"]["changedComponents"] == []
    assert output_change["runConditions"]["reports"]["homepage.json"]["nonComparabilitySettings"] == {
        "output": ["html"]
    }

    unknown_setting_change = emitted["unknownSettingChange"]
    assert unknown_setting_change["runConditionsReview"]["status"] == "required"
    assert unknown_setting_change["runConditionsReview"]["changedComponents"] == [
        "unclassifiedConfigSettings.futureNetworkSetting"
    ]
    unknown_with_matching_reference = emitted["unknownSettingWithMatchingReference"]
    assert unknown_with_matching_reference["runConditionsReview"]["status"] == "required"
    assert unknown_with_matching_reference["runConditionsReview"]["changedComponents"] == [
        "unclassifiedConfigSettings.futureNetworkSetting"
    ]
    for key in ("changedChromeFlags", "missingChromeFlags", "missingReferenceChromeFlags"):
        assert emitted[key]["runConditionsReview"]["status"] == "required"
        assert emitted[key]["runConditionsReview"]["changedComponents"] == ["chromeFlags"]
    assert emitted["changedChromeFlags"]["runConditionsReview"]["changedReports"] == [{
        "report": "brandguard-sample-02.json",
        "changedComponents": ["chromeFlags"],
    }]
    assert emitted["runConditionNotices"]["matching"] is None
    assert "route brandguard (brandguard.json)" in emitted["runConditionNotices"]["changedRoute"]
    mixed_settings_notice = emitted["runConditionNotices"]["changedMixedSettings"]
    assert (
        emitted["runConditionNotices"]["changedMixedSettingsReordered"]
        == mixed_settings_notice
    )
    assert (
        "route brandguard (brandguard.json) (changed settings: locale)"
        in mixed_settings_notice
    )
    assert (
        "BrandGuard repeated sample 2 (brandguard-sample-02.json) "
        "(changed settings: collectionTiming.pauseAfterFcpMs)"
    ) in mixed_settings_notice
    assert "route search (search.json) (changed settings: formFactor)" in mixed_settings_notice
    assert (
        "BrandGuard repeated sample 4 (brandguard-sample-04.json) "
        "(changed settings: locale)"
    ) in mixed_settings_notice
    assert "route homepage (homepage.json)" not in mixed_settings_notice
    assert "route universe (universe.json)" not in mixed_settings_notice
    assert (
        "BrandGuard repeated sample 3 (brandguard-sample-03.json)"
        not in mixed_settings_notice
    )
    assert "changed settings: locale, collectionTiming.pauseAfterFcpMs" not in mixed_settings_notice
    mixed_chrome_flag_notice = emitted["runConditionNotices"]["changedMixedChromeFlags"]
    assert mixed_chrome_flag_notice.count("route brandguard (brandguard.json)") == 1
    assert (
        mixed_chrome_flag_notice.count(
            "BrandGuard repeated sample 2 (brandguard-sample-02.json)"
        ) == 1
    )
    assert "route homepage (homepage.json)" not in mixed_chrome_flag_notice
    assert (
        "BrandGuard repeated sample 3 (brandguard-sample-03.json)"
        not in mixed_chrome_flag_notice
    )
    assert (
        "BrandGuard repeated sample 2 (brandguard-sample-02.json)"
        in emitted["runConditionNotices"]["changedRepeatedSample"]
    )
    assert "route brandguard (brandguard.json)" in emitted["runConditionNotices"]["changedNonChromeRoute"]
    assert (
        "BrandGuard repeated sample 4 (brandguard-sample-04.json)"
        in emitted["runConditionNotices"]["changedNonChromeRepeatedSample"]
    )
    assert (
        "BrandGuard repeated sample 3 (brandguard-sample-03.json)"
        in emitted["runConditionNotices"]["unavailableRepeatedSample"]
    )
    mixed_chrome_flag_notice = emitted["runConditionNotices"]["mixedChromeFlagAvailability"]
    assert mixed_chrome_flag_notice.count("route brandguard (brandguard.json)") == 1
    assert mixed_chrome_flag_notice.count(
        "BrandGuard repeated sample 2 (brandguard-sample-02.json)"
    ) == 1
    assert "route homepage (homepage.json)" not in mixed_chrome_flag_notice
    assert emitted["missingChromeFlags"]["runConditions"]["reports"][
        "brandguard-sample-03.json"
    ]["chromeFlags"] == "unavailable"
    assert emitted["implicitChromeFlags"]["chromeFlags"] == "unavailable"

    missing_run_conditions = emitted["missingRunConditions"]
    assert missing_run_conditions["runConditionsReview"]["status"] == "required"
    assert "formFactor" in missing_run_conditions["runConditionsReview"]["changedComponents"]
    assert "blockedUrlPatterns" in missing_run_conditions["runConditionsReview"]["changedComponents"]
    assert "collectionTiming.cpuQuietThresholdMs" in missing_run_conditions["runConditionsReview"]["changedComponents"]
    assert "locale" in missing_run_conditions["runConditionsReview"]["changedComponents"]
    assert "storage.clearStorageTypes" in missing_run_conditions["runConditionsReview"]["changedComponents"]
    assert "storage.disableStorageReset" in missing_run_conditions["runConditionsReview"]["changedComponents"]
    assert "additionalConfigSettings.auditMode" in missing_run_conditions["runConditionsReview"]["changedComponents"]
    assert missing_run_conditions["runConditions"]["reports"]["homepage.json"]["additionalConfigSettings"]["channel"] == "unavailable"
    assert missing_run_conditions["runConditions"]["reports"]["homepage.json"]["collectionTiming"] == {
        "maxWaitForFcp": "unavailable",
        "maxWaitForLoad": "unavailable",
        "pauseAfterFcpMs": "unavailable",
        "pauseAfterLoadMs": "unavailable",
        "networkQuietThresholdMs": "unavailable",
        "cpuQuietThresholdMs": "unavailable",
    }

    controlled = emitted["controlled"]
    assert controlled["tool"] == "Lighthouse 13.5.0"
    assert controlled["measurementStackReview"]["status"] == "required"
    assert controlled["measurementStackReview"]["changedComponents"] == [
        "lighthouseVersion",
        "chromiumVersion",
    ]
    assert "Owner review is required" in controlled["measurementStackReview"]["action"]
    assert controlled["environment"] == "Hosted target, controlled mobile preset"
    assert controlled["runConditions"]["condition"] == "controlled"
    assert controlled["runConditionsReview"]["status"] == "not-required"
    assert controlled["runConditionsReview"]["baseline"]["blockedUrlPatterns"] is None
    assert controlled["runConditionsReview"]["expected"]["blockedUrlPatterns"] == [
        "https://fonts.googleapis.com/*",
        "https://fonts.gstatic.com/*",
        "https://www.googletagmanager.com/*",
        "https://www.google-analytics.com/*",
        "https://*.google-analytics.com/*",
    ]
    assert controlled["controls"] == {
        "thirdPartyFonts": "blocked",
        "analytics": "blocked",
        "interpretation": "Controlled lab measurement only. Not field data.",
    }

    baseline = {"pages": {
        "homepage": {"performance": 80, "lcpMs": 1000},
        "brandguard": {"performance": 88, "lcpMs": 2000},
        "universe": {"performance": 90, "lcpMs": 2500},
        "search": {"performance": 91, "lcpMs": 3000},
    }}
    for summary in (normal, controlled):
        assert summary["schemaVersion"] == 12
        assert summary["property"] == "https://fixture.invalid"
        assert summary["baseline"] == "assets/docs/performance-baseline-mobile-2026-08-22.json"
        assert set(summary["pages"]) == set(expected_routes)
        for name, path in expected_routes.items():
            assert summary["pages"][name] == {
                "path": path,
                "finalNavigationUrl": "https://fixture.invalid/",
                "finalNavigationOrigin": "https://fixture.invalid",
                "performance": 91,
                "accessibility": 95,
                "bestPractices": 88,
                "seo": 93,
                "lcpMs": 2345,
                "cls": 0.012346,
                "tbtMs": 17,
                "fcpMs": 1234,
                "speedIndexMs": 1790,
                "lcpInvalidated": False,
                "lcpElement": "div.askjamie-hero-copy > p.hero-tagline",
                "deltaPerformance": 91 - baseline["pages"][name]["performance"],
                "deltaLcpMs": 2345 - baseline["pages"][name]["lcpMs"],
                "unavailableMetrics": [],
            }


def test_lighthouse_incomplete_fixture_marks_missing_metrics_unavailable_without_browser(tmp_path):
    fixture = ROOT / "tests/fixtures/lighthouse-summary-incomplete-report.json"
    assert fixture.is_file()
    runner = tmp_path / "incomplete-summary-fixture.mjs"
    runner.write_text(
        """
import { readFileSync } from "node:fs";
import { LIGHTHOUSE_ROUTES, summarizePage } from "./scripts/lighthouse-routes.mjs";

const report = JSON.parse(readFileSync("tests/fixtures/lighthouse-summary-incomplete-report.json", "utf8"));
const baseline = { pages: { brandguard: { performance: 88, lcpMs: 2000 } } };
const summary = summarizePage({
  report,
  path: LIGHTHOUSE_ROUTES.brandguard,
  baselinePage: baseline.pages.brandguard
});
process.stdout.write(JSON.stringify(summary));
""".strip()
        + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["node", "--input-type=module", "-e", runner.read_text(encoding="utf-8")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    summary = json.loads(result.stdout)
    assert summary["path"] == "/lens-system/okhp3-brandguard/"
    assert summary["performance"] is None
    assert summary["deltaPerformance"] is None
    assert summary["tbtMs"] is None
    assert summary["lcpElement"] is None
    assert summary["unavailableMetrics"] == [
        {"field": "performance", "source": "categories.performance.score"},
        {"field": "tbtMs", "source": "audits.total-blocking-time.numericValue"},
        {
            "field": "lcpElement",
            "source": "audits.largest-contentful-paint-element.details.items[0].items[0].node.selector or audits.lcp-breakdown-insight.details.items[type=node].selector",
        },
    ]


def test_lighthouse_repeated_lcp_invalidation_requires_all_details_to_agree_without_browser(tmp_path):
    fixture = ROOT / "tests/fixtures/lighthouse-summary-multiple-lcp-invalidated-report.json"
    assert fixture.is_file()
    runner = tmp_path / "multiple-lcp-invalidated-fixture.mjs"
    runner.write_text(
        """
import { readFileSync } from "node:fs";
import {
  LIGHTHOUSE_ROUTES,
  summarizeBrandGuardSamples,
  summarizePage
} from "./scripts/lighthouse-routes.mjs";

const report = JSON.parse(readFileSync(
  "tests/fixtures/lighthouse-summary-multiple-lcp-invalidated-report.json",
  "utf8"
));
const summarize = (items) => summarizePage({
  report: {
    ...report,
    audits: {
      ...report.audits,
      metrics: { details: { items } }
    }
  },
  path: LIGHTHOUSE_ROUTES.brandguard
});
const consistent = summarize([{ lcpInvalidated: false }, { lcpInvalidated: false }]);
const mixed = summarize(report.audits.metrics.details.items);
const repeated = summarizeBrandGuardSamples([
  { report: "brandguard-sample-02.json", page: mixed }
], { controlled: true });
process.stdout.write(JSON.stringify({ consistent, mixed, repeated }));
""".strip()
        + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["node", "--input-type=module", "-e", runner.read_text(encoding="utf-8")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    emitted = json.loads(result.stdout)
    source = "audits.metrics.details.items[].lcpInvalidated"
    assert emitted["consistent"]["lcpInvalidated"] is False
    assert not any(
        metric["field"] == "lcpInvalidated"
        for metric in emitted["consistent"]["unavailableMetrics"]
    )
    assert emitted["mixed"]["lcpInvalidated"] is None
    assert {
        "field": "lcpInvalidated",
        "source": source,
    } in emitted["mixed"]["unavailableMetrics"]
    assert emitted["repeated"]["metrics"]["lcpInvalidated"] == {
        "trueCount": 0,
        "falseCount": 0,
        "unavailableCount": 1,
    }
    assert emitted["repeated"]["samples"][0]["lcpInvalidated"] is None
    repeated_unavailable = emitted["repeated"]["samples"][0]["unavailableMetrics"]
    assert repeated_unavailable.count(
        {"field": "lcpInvalidated", "source": source}
    ) == 1


def test_lighthouse_modern_insight_retains_lcp_node_without_accepting_unrelated_items():
    runner = """
import { summarizePage } from "./scripts/lighthouse-routes.mjs";
const summarize = (details, legacy) => summarizePage({ path: "/", report: { audits: {
  "lcp-breakdown-insight": { details },
  "largest-contentful-paint-element": legacy,
} } });
const valid = { type: "node", selector: "p.askjamie-hero-tagline" };
const modern = summarize({ items: [{ type: "table", selector: "wrong" }, valid] });
const legacy = summarize({ items: [valid] }, { details: { items: [{ items: [{ node: { selector: "img.hero" } }] }] } });
const missing = summarize({ items: [{ type: "table", selector: "wrong" }, { type: "node", selector: 42 }] });
const malformed = summarize({ items: {} });
process.stdout.write(JSON.stringify({ modern, legacy, missing, malformed }));
"""
    result = subprocess.run(["node", "--input-type=module", "-e", runner],
                            cwd=ROOT, capture_output=True, text=True, check=True)
    rows = json.loads(result.stdout)
    assert rows["modern"]["lcpElement"] == "p.askjamie-hero-tagline"
    assert not any(m["field"] == "lcpElement" for m in rows["modern"]["unavailableMetrics"])
    assert rows["legacy"]["lcpElement"] == "img.hero"
    for name in ("missing", "malformed"):
        assert rows[name]["lcpElement"] is None
        assert any(m["field"] == "lcpElement" for m in rows[name]["unavailableMetrics"])


def test_lighthouse_cli_unavailable_metric_notice_names_route_and_fields_without_browser():
    runner = """
import { readFileSync } from "node:fs";
import {
  formatUnavailableMetricsNotice,
  LIGHTHOUSE_ROUTES,
  summarizePage,
} from "./scripts/lighthouse-routes.mjs";

const report = JSON.parse(readFileSync("tests/fixtures/lighthouse-summary-incomplete-report.json", "utf8"));
const page = summarizePage({
  report,
  path: LIGHTHOUSE_ROUTES.brandguard,
  baselinePage: { performance: 88, lcpMs: 2000 }
});
process.stdout.write(formatUnavailableMetricsNotice({ brandguard: page }));
""".strip()

    result = subprocess.run(
        ["node", "--input-type=module", "-e", runner],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout == (
        "Unavailable Lighthouse metrics:\n"
        "  /lens-system/okhp3-brandguard/: performance, tbtMs, lcpElement"
    )


def test_lighthouse_cli_unavailable_metric_notice_can_show_sources_without_browser():
    runner = """
import { readFileSync } from "node:fs";
import {
  formatUnavailableMetricsNotice,
  LIGHTHOUSE_ROUTES,
  summarizePage,
} from "./scripts/lighthouse-routes.mjs";

const report = JSON.parse(readFileSync("tests/fixtures/lighthouse-summary-incomplete-report.json", "utf8"));
const page = summarizePage({
  report,
  path: LIGHTHOUSE_ROUTES.brandguard,
  baselinePage: { performance: 88, lcpMs: 2000 }
});
process.stdout.write(formatUnavailableMetricsNotice(
  { brandguard: page },
  { includeSources: true }
));
""".strip()

    result = subprocess.run(
        ["node", "--input-type=module", "-e", runner],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout == (
        "Unavailable Lighthouse metrics:\n"
        "  /lens-system/okhp3-brandguard/: performance, tbtMs, lcpElement\n"
        "    performance: categories.performance.score\n"
        "    tbtMs: audits.total-blocking-time.numericValue\n"
        "    lcpElement: audits.largest-contentful-paint-element.details.items[0].items[0].node.selector or audits.lcp-breakdown-insight.details.items[type=node].selector"
    )


def test_lighthouse_cli_detail_switch_controls_terminal_notice_without_browser(tmp_path):
    if os.name == "nt":
        pytest.skip("POSIX fake executables require shebang support unavailable on Windows")
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("Node.js is unavailable")

    root = tmp_path
    (root / "scripts").mkdir()
    shutil.copy2(ROOT / "scripts/lighthouse-routes.mjs", root / "scripts/lighthouse-routes.mjs")
    (root / "assets/audit").mkdir(parents=True)
    (root / "assets/docs").mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        ROOT / "assets/docs/performance-baseline-mobile-2026-08-22.json",
        root / "assets/docs/performance-baseline-mobile-2026-08-22.json",
    )
    (root / "tests/fixtures").mkdir(parents=True)
    shutil.copy2(
        ROOT / "tests/fixtures/lighthouse-summary-incomplete-report.json",
        root / "tests/fixtures/lighthouse-summary-incomplete-report.json",
    )

    lighthouse_dir = root / "node_modules/.bin"
    lighthouse_dir.mkdir(parents=True)
    fake_lighthouse_log = root / "fake-lighthouse-arguments.txt"
    fake_lighthouse = lighthouse_dir / "lighthouse"
    fake_lighthouse.write_text(
        """#!/usr/bin/env node
const fs = require("node:fs");
const path = require("node:path");
const args = process.argv.slice(2);
fs.appendFileSync(process.env.FAKE_LIGHTHOUSE_ARGS_LOG, `${args[0]}\\n`);
const reportPath = args.find((arg) => arg.startsWith("--output-path="))
  .slice("--output-path=".length);
const report = JSON.parse(fs.readFileSync(
  path.join(process.cwd(), "tests/fixtures/lighthouse-summary-incomplete-report.json"),
  "utf8"
));
report.lighthouseVersion = "12.8.2";
report.environment = { hostUserAgent: "HeadlessChrome/148.0.0.0" };
report.configSettings = {};
fs.writeFileSync(reportPath, JSON.stringify(report));
""",
        encoding="utf-8",
    )
    fake_lighthouse.chmod(0o755)

    fake_chrome_log = root / "fake-chrome-arguments.txt"
    fake_chrome = root / "fake-chrome"
    fake_chrome.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$FAKE_CHROME_ARGS_LOG\"\n"
        "echo 'Chromium 148.0.7778.96'\n",
        encoding="utf-8",
    )
    fake_chrome.chmod(0o755)

    environment = {
        **os.environ,
        "CHROME_PATH": str(fake_chrome),
        "FAKE_CHROME_ARGS_LOG": str(fake_chrome_log),
        "FAKE_LIGHTHOUSE_ARGS_LOG": str(fake_lighthouse_log),
    }
    common_args = [
        node_bin,
        "scripts/lighthouse-routes.mjs",
        "--preset=mobile",
        "--date=2099-02-04",
        "--base-url=https://unavailable-source-switch.invalid",
    ]
    concise_run = subprocess.run(
        [*common_args, "--run-id=concise"],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert concise_run.returncode == 0, concise_run.stderr
    assert (
        "  /lens-system/okhp3-brandguard/ (brandguard.json): "
        "performance, tbtMs, lcpElement"
    ) in concise_run.stderr
    assert "categories.performance.score" not in concise_run.stderr
    assert "audits.total-blocking-time.numericValue" not in concise_run.stderr

    detailed_run = subprocess.run(
        [*common_args, "--run-id=detailed", "--show-unavailable-sources"],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert detailed_run.returncode == 0, detailed_run.stderr
    assert "performance: categories.performance.score" in detailed_run.stderr
    assert "tbtMs: audits.total-blocking-time.numericValue" in detailed_run.stderr

    for run_id, invalid_argument, expected_error in (
        (
            "misspelled",
            "--show-unavailable-source",
            "Unknown Lighthouse route option: --show-unavailable-source",
        ),
        (
            "unsupported",
            "--not-a-route-option",
            "Unknown Lighthouse route option: --not-a-route-option",
        ),
        (
            "positional",
            "stray-value",
            "Unexpected positional Lighthouse route argument: stray-value",
        ),
    ):
        invalid_run = subprocess.run(
            [
                *common_args,
                f"--run-id={run_id}",
                invalid_argument,
            ],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
        )
        assert invalid_run.returncode != 0
        assert expected_error in invalid_run.stderr
        if invalid_argument.startswith("--"):
            assert "--show-unavailable-sources" in invalid_run.stderr
        assert not (
            root / "assets/audit" / f"lighthouse-2099-02-04-mobile-{run_id}"
        ).exists()

    # The executables are test doubles; the only Chromium invocation is its
    # version check, so neither run launches a browser.
    assert fake_chrome_log.read_text(encoding="utf-8").splitlines() == ["--version", "--version"]
    assert len(fake_lighthouse_log.read_text(encoding="utf-8").splitlines()) == 8


@pytest.mark.parametrize("help_flag", ["-h", "--help"])
def test_lighthouse_cli_help_lists_options_without_running_or_creating_output(tmp_path, help_flag):
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("Node.js is unavailable")

    root = tmp_path
    (root / "scripts").mkdir()
    shutil.copy2(ROOT / "scripts/lighthouse-routes.mjs", root / "scripts/lighthouse-routes.mjs")
    lighthouse_bin = root / "node_modules/.bin/lighthouse"
    lighthouse_bin.parent.mkdir(parents=True)
    lighthouse_marker = root / "lighthouse-invoked"
    lighthouse_bin.write_text(
        f"#!/bin/sh\nprintf invoked > '{lighthouse_marker}'\n",
        encoding="utf-8",
    )
    lighthouse_bin.chmod(0o755)
    chrome_bin = root / "fake-chrome"
    chrome_bin.write_text(
        "#!/bin/sh\nprintf 'Google Chrome 130.0.0.0\\n'\n",
        encoding="utf-8",
    )
    chrome_bin.chmod(0o755)
    environment = os.environ.copy()
    environment["CHROME_PATH"] = str(chrome_bin)

    result = subprocess.run(
        [
            node_bin,
            "scripts/lighthouse-routes.mjs",
            help_flag,
            "--preset=mobile",
            "--date=2099-02-05",
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert "Usage: node scripts/lighthouse-routes.mjs [options]" in result.stdout
    for option in (
        "-h, --help",
        "--base-url=<url>",
        "--preset=<desktop|mobile>",
        "--date=<YYYY-MM-DD>",
        "--run-id=<identifier>",
        "--replace",
        "--controlled",
        "--show-unavailable-sources",
        "--brandguard-samples=<count>",
        "--desktop-repeats=<count>",
        "--print-desktop-reference-fingerprint",
    ):
        assert option in result.stdout
    assert "Choose the Lighthouse preset" in result.stdout
    assert "Block selected third-party requests" in result.stdout
    assert not lighthouse_marker.exists()
    assert not (root / "assets/audit").exists()


LIGHTHOUSE_VALUE_OPTION_CONFLICT_CASES = (
    ("--base-url", "https://first.invalid", "https://second.invalid"),
    ("--preset", "desktop", "mobile"),
    ("--date", "2099-02-05", "2099-02-06"),
    ("--run-id", "first-run", "second-run"),
    ("--brandguard-samples", "2", "3"),
    ("--desktop-repeats", "2", "3"),
)


def test_lighthouse_value_options_are_all_covered_by_repeat_conflict_checks():
    source = (ROOT / "scripts/lighthouse-routes.mjs").read_text(encoding="utf-8")
    accepted_options_match = re.search(
        r"const SCRIPT_VALUE_OPTIONS = Object\.freeze\(\[(.*?)\]\);",
        source,
        re.DOTALL,
    )
    assert accepted_options_match, "Lighthouse CLI value-option list is missing"
    accepted_options = set(
        re.findall(r'"(--[a-z0-9-]+)"', accepted_options_match.group(1))
    )
    runner_options = set(
        re.findall(
            r'process\.argv\.find\(\(arg\)\s*=>\s*arg\.startsWith\("(--[a-z0-9-]+)="\)\)',
            source,
        )
    )
    conflict_test_options = {
        option
        for option, _first_value, _second_value in LIGHTHOUSE_VALUE_OPTION_CONFLICT_CASES
    }

    assert accepted_options == runner_options, (
        "Lighthouse CLI value options must match runner argument reads; "
        f"accepted but not read: {sorted(accepted_options - runner_options)}; "
        f"read but not accepted: {sorted(runner_options - accepted_options)}"
    )
    missing_conflict_checks = runner_options - conflict_test_options
    assert not missing_conflict_checks, (
        "Runner-consumed Lighthouse value options missing repeat-conflict "
        f"coverage: {', '.join(sorted(missing_conflict_checks))}"
    )


@pytest.mark.parametrize(
    ("option", "first_value", "second_value"),
    LIGHTHOUSE_VALUE_OPTION_CONFLICT_CASES,
)
def test_lighthouse_cli_rejects_conflicting_repeated_value_options_before_output(
    tmp_path, option, first_value, second_value
):
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("Node.js is unavailable")

    root = tmp_path
    (root / "scripts").mkdir()
    shutil.copy2(ROOT / "scripts/lighthouse-routes.mjs", root / "scripts/lighthouse-routes.mjs")

    result = subprocess.run(
        [
            node_bin,
            "scripts/lighthouse-routes.mjs",
            f"{option}={first_value}",
            f"{option}={second_value}",
        ],
        cwd=root,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert f"Conflicting repeated Lighthouse route option: {option}" in result.stderr
    assert not (root / "assets/audit").exists()


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("--base-url", "https://fixture.invalid"),
        ("--preset", "desktop"),
        ("--date", "2099-02-05"),
        ("--run-id", "same-run"),
        ("--brandguard-samples", "2"),
        ("--desktop-repeats", "2"),
    ],
)
def test_lighthouse_cli_allows_identical_repeated_value_options(
    tmp_path, option, value
):
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("Node.js is unavailable")

    root = tmp_path
    (root / "scripts").mkdir()
    shutil.copy2(ROOT / "scripts/lighthouse-routes.mjs", root / "scripts/lighthouse-routes.mjs")
    reference_path = root / "assets/docs/performance-baseline-desktop-2026-09-07.json"
    reference_path.parent.mkdir(parents=True)
    shutil.copy2(
        ROOT / "assets/docs/performance-baseline-desktop-2026-09-07.json",
        reference_path,
    )

    result = subprocess.run(
        [
            node_bin,
            "scripts/lighthouse-routes.mjs",
            f"{option}={value}",
            f"{option}={value}",
            "--print-desktop-reference-fingerprint",
        ],
        cwd=root,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert re.fullmatch(r"sha256:[0-9a-f]{64}\n", result.stdout)
    assert f"Conflicting repeated Lighthouse route option: {option}" not in result.stderr
    assert not (root / "assets/audit").exists()


@pytest.mark.parametrize(
    "stop_signal",
    [signal.SIGTERM, signal.SIGKILL],
    ids=["sigterm", "sigkill"],
)
def test_lighthouse_runner_termination_preserves_incomplete_status_and_reports(
    tmp_path, stop_signal
):
    if os.name == "nt":
        pytest.skip("POSIX process groups and fake executables are unavailable on Windows")
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("Node.js is unavailable")

    root = tmp_path
    (root / "scripts").mkdir()
    shutil.copy2(ROOT / "scripts/lighthouse-routes.mjs", root / "scripts/lighthouse-routes.mjs")
    (root / "assets/audit").mkdir(parents=True)
    (root / "assets/docs").mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        ROOT / "assets/docs/performance-baseline-mobile-2026-08-22.json",
        root / "assets/docs/performance-baseline-mobile-2026-08-22.json",
    )
    (root / "tests/fixtures").mkdir(parents=True)
    shutil.copy2(
        ROOT / "tests/fixtures/lighthouse-summary-incomplete-report.json",
        root / "tests/fixtures/lighthouse-summary-incomplete-report.json",
    )

    lighthouse_dir = root / "node_modules/.bin"
    lighthouse_dir.mkdir(parents=True)
    blocked_marker = root / "fake-lighthouse-blocked"
    fake_lighthouse = lighthouse_dir / "lighthouse"
    fake_lighthouse.write_text(
        """#!/usr/bin/env node
const fs = require("node:fs");
const path = require("node:path");
const args = process.argv.slice(2);
const reportPath = args.find((arg) => arg.startsWith("--output-path="))
  .slice("--output-path=".length);
if (args[0].endsWith("/lens-system/okhp3-brandguard/")) {
  fs.writeFileSync(process.env.FAKE_LIGHTHOUSE_BLOCKED_MARKER, String(process.pid));
  while (true) Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 1000);
}
const report = JSON.parse(fs.readFileSync(
  path.join(process.cwd(), "tests/fixtures/lighthouse-summary-incomplete-report.json"),
  "utf8"
));
report.lighthouseVersion = "12.8.2";
report.environment = { hostUserAgent: "HeadlessChrome/148.0.0.0" };
report.configSettings = {};
fs.writeFileSync(reportPath, JSON.stringify(report));
""",
        encoding="utf-8",
    )
    fake_lighthouse.chmod(0o755)

    fake_chrome = root / "fake-chrome"
    fake_chrome.write_text(
        "#!/bin/sh\necho 'Chromium 148.0.7778.96'\n",
        encoding="utf-8",
    )
    fake_chrome.chmod(0o755)

    output_dir = root / "assets/audit/lighthouse-2099-02-05-mobile-interrupted"
    process = subprocess.Popen(
        [
            node_bin,
            "scripts/lighthouse-routes.mjs",
            "--preset=mobile",
            "--date=2099-02-05",
            "--run-id=interrupted",
            "--base-url=http://127.0.0.1:5000",
        ],
        cwd=root,
        env={
            **os.environ,
            "CHROME_PATH": str(fake_chrome),
            "FAKE_LIGHTHOUSE_BLOCKED_MARKER": str(blocked_marker),
        },
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                pytest.fail(
                    "Lighthouse runner exited before reaching the blocked route: "
                    f"{process.returncode}"
                )
            if blocked_marker.is_file():
                break
            time.sleep(0.01)
        else:
            pytest.fail("Fake Lighthouse did not block on the BrandGuard route")

        homepage_report = output_dir / "homepage.json"
        assert homepage_report.is_file(), "the completed homepage report was not retained"
        homepage = json.loads(homepage_report.read_text(encoding="utf-8"))
        assert homepage["lighthouseVersion"] == "12.8.2"

        os.killpg(process.pid, stop_signal)
        assert process.wait(timeout=10) == -stop_signal
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)

    status_path = output_dir / "run-status.json"
    assert status_path.is_file(), "the initial incomplete run marker was lost"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["status"] == "incomplete"
    assert status["reason"] == "The Lighthouse run has not completed."
    assert status["startedAt"]
    homepage_report = output_dir / "homepage.json"
    assert homepage_report.is_file(), "the completed homepage report was not retained"
    homepage = json.loads(homepage_report.read_text(encoding="utf-8"))
    assert homepage["lighthouseVersion"] == "12.8.2"
    assert not (output_dir / "brandguard.json").exists()
    assert not (output_dir / "summary.json").exists()


def test_lighthouse_success_without_next_report_keeps_earlier_evidence_incomplete(tmp_path):
    if os.name == "nt":
        pytest.skip("POSIX fake executables require shebang support unavailable on Windows")
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("Node.js is unavailable")

    root = tmp_path
    (root / "scripts").mkdir()
    shutil.copy2(ROOT / "scripts/lighthouse-routes.mjs", root / "scripts/lighthouse-routes.mjs")
    (root / "assets/audit").mkdir(parents=True)
    (root / "assets/docs").mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        ROOT / "assets/docs/performance-baseline-mobile-2026-08-22.json",
        root / "assets/docs/performance-baseline-mobile-2026-08-22.json",
    )

    report = json.loads(
        (ROOT / "tests/fixtures/lighthouse-summary-incomplete-report.json")
        .read_text(encoding="utf-8")
    )
    report["lighthouseVersion"] = "12.8.2"
    report["environment"] = {"hostUserAgent": "HeadlessChrome/148.0.0.0"}
    report["configSettings"] = {}
    expected_report_bytes = json.dumps(report, separators=(",", ":")).encode("utf-8")
    report_source = root / "fake-valid-report.json"
    report_source.write_bytes(expected_report_bytes)

    lighthouse_dir = root / "node_modules/.bin"
    lighthouse_dir.mkdir(parents=True)
    invocation_log = root / "fake-lighthouse-invocations.txt"
    fake_lighthouse = lighthouse_dir / "lighthouse"
    fake_lighthouse.write_text(
        """#!/usr/bin/env node
const fs = require("node:fs");
const path = require("node:path");
const args = process.argv.slice(2);
const reportPath = args.find((arg) => arg.startsWith("--output-path="))
  .slice("--output-path=".length);
const invocationCount = fs.existsSync(process.env.FAKE_LIGHTHOUSE_LOG)
  ? fs.readFileSync(process.env.FAKE_LIGHTHOUSE_LOG, "utf8").trim().split("\\n").length
  : 0;
fs.appendFileSync(process.env.FAKE_LIGHTHOUSE_LOG, `${args[0]}\\n`);
if (invocationCount === 0) {
  fs.copyFileSync(process.env.FAKE_LIGHTHOUSE_REPORT, reportPath);
} else if (path.basename(reportPath) !== "brandguard.json") {
  process.exit(31);
}
""",
        encoding="utf-8",
    )
    fake_lighthouse.chmod(0o755)

    fake_chrome = root / "fake-chrome"
    fake_chrome.write_text(
        "#!/bin/sh\necho 'Chromium 148.0.7778.96'\n",
        encoding="utf-8",
    )
    fake_chrome.chmod(0o755)

    run = subprocess.run(
        [
            node_bin,
            "scripts/lighthouse-routes.mjs",
            "--preset=mobile",
            "--date=2099-02-07",
            "--run-id=missing-report",
            "--base-url=https://missing-report.invalid",
        ],
        cwd=root,
        env={
            **os.environ,
            "CHROME_PATH": str(fake_chrome),
            "FAKE_LIGHTHOUSE_LOG": str(invocation_log),
            "FAKE_LIGHTHOUSE_REPORT": str(report_source),
        },
        capture_output=True,
        text=True,
    )

    output_dir = root / "assets/audit/lighthouse-2099-02-07-mobile-missing-report"
    earlier_report = output_dir / "homepage.json"
    assert run.returncode != 0
    assert invocation_log.read_text(encoding="utf-8").splitlines() == [
        "https://missing-report.invalid/",
        "https://missing-report.invalid/lens-system/okhp3-brandguard/",
    ]
    assert earlier_report.read_bytes() == expected_report_bytes
    assert not (output_dir / "brandguard.json").exists()
    assert not (output_dir / "summary.json").exists()
    assert "Wrote " not in run.stdout
    assert "Measurement stack:" not in run.stdout

    status = json.loads((output_dir / "run-status.json").read_text(encoding="utf-8"))
    assert status["status"] == "incomplete"
    assert "exited successfully but did not write the requested report" in status["error"]
    assert "brandguard (brandguard.json)" in status["error"]
    assert str(output_dir / "brandguard.json") in status["error"]
    assert "exited successfully but did not write the requested report" in run.stderr


def test_lighthouse_invalid_fixture_marks_present_invalid_metrics_unavailable_without_browser(tmp_path):
    fixture = ROOT / "tests/fixtures/lighthouse-summary-invalid-report.json"
    assert fixture.is_file()
    runner = tmp_path / "invalid-summary-fixture.mjs"
    runner.write_text(
        """
import { readFileSync } from "node:fs";
import { LIGHTHOUSE_ROUTES, summarizePage } from "./scripts/lighthouse-routes.mjs";

const report = JSON.parse(readFileSync("tests/fixtures/lighthouse-summary-invalid-report.json", "utf8"));
const baselinePage = { performance: 88, lcpMs: 2000 };
const summary = summarizePage({
  report,
  path: LIGHTHOUSE_ROUTES.brandguard,
  baselinePage
});
process.stdout.write(JSON.stringify(summary));
""".strip()
        + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["node", "--input-type=module", "-e", runner.read_text(encoding="utf-8")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    summary = json.loads(result.stdout)
    assert summary["path"] == "/lens-system/okhp3-brandguard/"
    assert summary["performance"] is None
    assert summary["accessibility"] is None
    assert summary["deltaPerformance"] is None
    assert summary["lcpMs"] is None
    assert summary["cls"] is None
    assert summary["tbtMs"] is None
    assert summary["unavailableMetrics"] == [
        {"field": "performance", "source": "categories.performance.score"},
        {"field": "accessibility", "source": "categories.accessibility.score"},
        {"field": "lcpMs", "source": "audits.largest-contentful-paint.numericValue"},
        {"field": "cls", "source": "audits.cumulative-layout-shift.numericValue"},
        {"field": "tbtMs", "source": "audits.total-blocking-time.numericValue"},
    ]


def test_lighthouse_out_of_range_values_are_unavailable_with_report_sources_without_browser(tmp_path):
    fixture = ROOT / "tests/fixtures/lighthouse-summary-out-of-range-report.json"
    assert fixture.is_file()
    runner = tmp_path / "out-of-range-summary-fixture.mjs"
    runner.write_text(
        """
import { readFileSync } from "node:fs";
import { LIGHTHOUSE_ROUTES, summarizePage } from "./scripts/lighthouse-routes.mjs";

const report = JSON.parse(readFileSync("tests/fixtures/lighthouse-summary-out-of-range-report.json", "utf8"));
const summary = summarizePage({
  report,
  path: LIGHTHOUSE_ROUTES.brandguard,
  baselinePage: { performance: 88, lcpMs: 2000 }
});
process.stdout.write(JSON.stringify(summary));
""".strip()
        + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["node", "--input-type=module", "-e", runner.read_text(encoding="utf-8")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    summary = json.loads(result.stdout)
    assert summary["path"] == "/lens-system/okhp3-brandguard/"
    assert summary["performance"] is None
    assert summary["accessibility"] is None
    assert summary["bestPractices"] is None
    assert summary["seo"] is None
    assert summary["lcpMs"] is None
    assert summary["cls"] is None
    assert summary["tbtMs"] is None
    assert summary["fcpMs"] is None
    assert summary["speedIndexMs"] is None
    assert summary["deltaPerformance"] is None
    assert summary["deltaLcpMs"] is None
    assert summary["unavailableMetrics"] == [
        {"field": "performance", "source": "categories.performance.score"},
        {"field": "accessibility", "source": "categories.accessibility.score"},
        {"field": "bestPractices", "source": "categories.best-practices.score"},
        {"field": "seo", "source": "categories.seo.score"},
        {"field": "lcpMs", "source": "audits.largest-contentful-paint.numericValue"},
        {"field": "cls", "source": "audits.cumulative-layout-shift.numericValue"},
        {"field": "tbtMs", "source": "audits.total-blocking-time.numericValue"},
        {"field": "fcpMs", "source": "audits.first-contentful-paint.numericValue"},
        {"field": "speedIndexMs", "source": "audits.speed-index.numericValue"},
    ]


def test_lighthouse_numeric_ranges_keep_zero_and_cls_above_one_valid_without_browser():
    runner = """
import { LIGHTHOUSE_ROUTES, summarizePage } from "./scripts/lighthouse-routes.mjs";

const summary = summarizePage({
  path: LIGHTHOUSE_ROUTES.brandguard,
  report: {
    categories: {
      performance: { score: 0 },
      accessibility: { score: 1 },
      "best-practices": { score: 0 },
      seo: { score: 1 }
    },
    audits: {
      "largest-contentful-paint": { numericValue: 0 },
      "cumulative-layout-shift": { numericValue: 1.5 },
      "total-blocking-time": { numericValue: 0 },
      "first-contentful-paint": { numericValue: 0 },
      "speed-index": { numericValue: 0 },
      "largest-contentful-paint-element": {
        details: { items: [{ items: [{ node: { selector: "main" } }] }] }
      }
    }
  }
});
process.stdout.write(JSON.stringify(summary));
""".strip()

    result = subprocess.run(
        ["node", "--input-type=module", "-e", runner],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    summary = json.loads(result.stdout)
    assert summary["path"] == "/lens-system/okhp3-brandguard/"
    assert summary["performance"] == 0
    assert summary["accessibility"] == 100
    assert summary["bestPractices"] == 0
    assert summary["seo"] == 100
    assert summary["lcpMs"] == 0
    assert summary["cls"] == 1.5
    assert summary["tbtMs"] == 0
    assert summary["fcpMs"] == 0
    assert summary["speedIndexMs"] == 0
    assert summary["unavailableMetrics"] == []


def test_lighthouse_non_finite_metrics_are_unavailable_without_browser():
    runner = """
import {
  LIGHTHOUSE_ROUTES,
  summarizePage,
} from "./scripts/lighthouse-routes.mjs";

const report = {
  categories: {
    performance: { score: Number.NaN },
    accessibility: { score: Number.POSITIVE_INFINITY },
    "best-practices": { score: Number.NEGATIVE_INFINITY },
    seo: { score: Number.NaN },
  },
  audits: {
    "largest-contentful-paint": { numericValue: Number.POSITIVE_INFINITY },
    "cumulative-layout-shift": { numericValue: Number.NEGATIVE_INFINITY },
    "total-blocking-time": { numericValue: Number.NaN },
    "first-contentful-paint": { numericValue: Number.POSITIVE_INFINITY },
    "speed-index": { numericValue: Number.NEGATIVE_INFINITY },
    "largest-contentful-paint-element": {
      details: { items: [{ items: [{ node: { selector: "main" } }] }] },
    },
  },
};
const summary = summarizePage({
  report,
  path: LIGHTHOUSE_ROUTES.brandguard,
  baselinePage: { performance: 88, lcpMs: 2000 },
});
process.stdout.write(JSON.stringify(summary));
""".strip()

    result = subprocess.run(
        ["node", "--input-type=module", "-e", runner],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    summary = json.loads(result.stdout)
    assert summary["path"] == "/lens-system/okhp3-brandguard/"
    assert summary["performance"] is None
    assert summary["accessibility"] is None
    assert summary["bestPractices"] is None
    assert summary["seo"] is None
    assert summary["lcpMs"] is None
    assert summary["cls"] is None
    assert summary["tbtMs"] is None
    assert summary["fcpMs"] is None
    assert summary["speedIndexMs"] is None
    assert summary["deltaPerformance"] is None
    assert summary["deltaLcpMs"] is None
    assert summary["unavailableMetrics"] == [
        {"field": "performance", "source": "categories.performance.score"},
        {"field": "accessibility", "source": "categories.accessibility.score"},
        {"field": "bestPractices", "source": "categories.best-practices.score"},
        {"field": "seo", "source": "categories.seo.score"},
        {"field": "lcpMs", "source": "audits.largest-contentful-paint.numericValue"},
        {"field": "cls", "source": "audits.cumulative-layout-shift.numericValue"},
        {"field": "tbtMs", "source": "audits.total-blocking-time.numericValue"},
        {"field": "fcpMs", "source": "audits.first-contentful-paint.numericValue"},
        {"field": "speedIndexMs", "source": "audits.speed-index.numericValue"},
    ]


def test_lighthouse_desktop_repeat_rows_preserve_missing_metric_sources(tmp_path):
    runner = tmp_path / "desktop-repeat-summary-fixture.mjs"
    runner.write_text(
        """
import assert from "node:assert/strict";
import { routes, summarizeDesktopRepeats, summarizePage } from "./scripts/lighthouse-routes.mjs";

const makeReport = () => ({
  categories: {
    performance: { score: 0.91 },
    accessibility: { score: 1 },
    "best-practices": { score: 1 },
    seo: { score: 1 }
  },
  audits: {
    "largest-contentful-paint": { numericValue: 2400 },
    "cumulative-layout-shift": { numericValue: 0.01 },
    "total-blocking-time": { numericValue: 30 },
    "first-contentful-paint": { numericValue: 1200 },
    "speed-index": { numericValue: 1800 },
    "largest-contentful-paint-element": {
      details: { items: [{ items: [{ node: { selector: "main" } }] }] }
    }
  }
});

const passes = [1, 2, 3].map((pass) => ({
  pass,
  pages: Object.fromEntries(Object.entries(routes).map(([name, path]) => {
    const report = makeReport();
    if (pass === 1 && name === "homepage") {
      report.categories.performance.score = null;
    }
    if (pass === 3 && name === "search") {
      delete report.audits["speed-index"].numericValue;
    }
    if (name === "homepage") {
      delete report.audits["largest-contentful-paint"].numericValue;
    }
    return [name, {
      report: `pass-0${pass}/${name}.json`,
      page: summarizePage({ report, path })
    }];
  }))
}));

const summary = summarizeDesktopRepeats(passes);
const homepageFirstPass = summary.routes.homepage.passes[0];
const searchLaterPass = summary.routes.search.passes[2];
assert.deepEqual(summary.routes.homepage.metrics.lcpMs, {
  availableCount: 0,
  unavailableCount: passes.length,
  min: null,
  max: null,
  spread: null
});
assert.equal(summary.routes.homepage.metrics.performance.min, 91);
assert.equal(summary.routes.homepage.metrics.performance.max, 91);
assert.equal(summary.routes.homepage.passes[0].fcpMs, 1200);
assert.deepEqual(
  summary.routes.homepage.passes.map(({ report, lcpMs, unavailableMetrics }) => ({
    report,
    lcpMs,
    unavailableMetrics
  })),
  [1, 2, 3].map((pass) => ({
    report: `pass-0${pass}/homepage.json`,
    lcpMs: null,
    unavailableMetrics: [
      ...(pass === 1 ? [
        { field: "performance", source: "categories.performance.score" }
      ] : []),
      { field: "lcpMs", source: "audits.largest-contentful-paint.numericValue" }
    ]
  }))
);
assert.equal(homepageFirstPass.performance, null);
assert.deepEqual(homepageFirstPass.unavailableMetrics, [
  { field: "performance", source: "categories.performance.score" },
  { field: "lcpMs", source: "audits.largest-contentful-paint.numericValue" }
]);
assert.equal(homepageFirstPass.report, "pass-01/homepage.json");
assert.equal(searchLaterPass.speedIndexMs, null);
assert.deepEqual(searchLaterPass.unavailableMetrics, [
  { field: "speedIndexMs", source: "audits.speed-index.numericValue" }
]);
assert.equal(searchLaterPass.report, "pass-03/search.json");
process.stdout.write(JSON.stringify(summary));
""".strip()
        + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["node", "--input-type=module", "-e", runner.read_text(encoding="utf-8")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    summary = json.loads(result.stdout)
    assert summary["routes"]["homepage"]["passes"][0]["unavailableMetrics"] == [
        {"field": "performance", "source": "categories.performance.score"},
        {
            "field": "lcpMs",
            "source": "audits.largest-contentful-paint.numericValue",
        },
    ]
    assert summary["routes"]["search"]["passes"][2]["unavailableMetrics"] == [
        {"field": "speedIndexMs", "source": "audits.speed-index.numericValue"}
    ]
    homepage = summary["routes"]["homepage"]
    assert homepage["metrics"]["lcpMs"] == {
        "availableCount": 0,
        "unavailableCount": 3,
        "min": None,
        "max": None,
        "spread": None,
    }
    assert homepage["metrics"]["performance"]["availableCount"] == 2
    assert homepage["passes"][0]["fcpMs"] == 1200
    assert [sample["report"] for sample in homepage["passes"]] == [
        "pass-01/homepage.json",
        "pass-02/homepage.json",
        "pass-03/homepage.json",
    ]
    assert homepage["passes"][0]["unavailableMetrics"] == [
        {"field": "performance", "source": "categories.performance.score"},
        {
            "field": "lcpMs",
            "source": "audits.largest-contentful-paint.numericValue",
        },
    ]
    assert all(
        sample["unavailableMetrics"] == [
            {
                "field": "lcpMs",
                "source": "audits.largest-contentful-paint.numericValue",
            }
        ]
        for sample in homepage["passes"][1:]
    )


def test_lighthouse_brandguard_repeat_summary_keeps_conditions_and_missing_values_separate(tmp_path):
    runner = tmp_path / "repeat-summary-fixture.mjs"
    runner.write_text(
        """
import {
  formatBrandGuardSampleScores,
  formatUnavailableBrandGuardSampleMetricsNotice,
  summarizeBrandGuardSamples
} from "./scripts/lighthouse-routes.mjs";

const pages = [
  {
    performance: 91, accessibility: 84, bestPractices: 96, seo: 88, cls: 0.012,
    fcpMs: 100, speedIndexMs: 500, lcpMs: 3500, tbtMs: 20, lcpInvalidated: false
  },
  {
    performance: 93, accessibility: 86, bestPractices: 94, seo: 90, cls: 0.024,
    fcpMs: 140, speedIndexMs: 700, lcpMs: 3700, tbtMs: 40, lcpInvalidated: true
  },
  {
    performance: 95, accessibility: null, bestPractices: 92, seo: null, cls: null,
    fcpMs: null, speedIndexMs: 650, lcpMs: 3600, tbtMs: null, lcpInvalidated: null
  }
];
const samples = pages.map((page, index) => ({
  report: index === 0 ? "brandguard.json" : `brandguard-sample-0${index + 1}.json`,
  page: {
    ...page,
    unavailableMetrics: index === 2 ? [
      { field: "accessibility", source: "categories.accessibility.score" },
      { field: "seo", source: "categories.seo.score" },
      { field: "cls", source: "audits.cumulative-layout-shift.numericValue" },
      { field: "fcpMs", source: "audits.first-contentful-paint.numericValue" },
      { field: "tbtMs", source: "audits.total-blocking-time.numericValue" }
    ] : []
  }
}));
const controlledSummary = summarizeBrandGuardSamples(samples, { controlled: true });
const completeSummary = summarizeBrandGuardSamples(samples.slice(0, 2), { controlled: true });
process.stdout.write(JSON.stringify({
  controlled: controlledSummary,
  complete: completeSummary,
  normal: summarizeBrandGuardSamples(samples, { controlled: false }),
  sampleScores: formatBrandGuardSampleScores(
    summarizeBrandGuardSamples(samples, { controlled: false }).samples
  ),
  controlledNotice: formatUnavailableBrandGuardSampleMetricsNotice(samples),
  detailedNotice: formatUnavailableBrandGuardSampleMetricsNotice(samples, { includeSources: true }),
  completeNotice: formatUnavailableBrandGuardSampleMetricsNotice(samples.slice(0, 2))
}));
""".strip()
        + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["node", "--input-type=module", "-e", runner.read_text(encoding="utf-8")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    emitted = json.loads(result.stdout)

    controlled = emitted["controlled"]
    assert controlled["condition"] == "controlled"
    assert controlled["controls"] == {"thirdPartyFonts": "blocked", "analytics": "blocked"}
    assert controlled["sampleCount"] == 3
    assert controlled["metrics"]["fcpMs"] == {
        "availableCount": 2,
        "unavailableCount": 1,
        "min": 100,
        "max": 140,
        "spread": 40,
    }
    assert controlled["metrics"]["speedIndexMs"]["spread"] == 200
    assert controlled["metrics"]["lcpMs"]["min"] == 3500
    assert controlled["metrics"]["lcpMs"]["max"] == 3700
    assert controlled["metrics"]["tbtMs"]["unavailableCount"] == 1
    assert controlled["metrics"]["accessibility"] == {
        "availableCount": 2,
        "unavailableCount": 1,
        "min": 84,
        "max": 86,
        "spread": 2,
    }
    assert controlled["metrics"]["bestPractices"] == {
        "availableCount": 3,
        "unavailableCount": 0,
        "min": 92,
        "max": 96,
        "spread": 4,
    }
    assert controlled["metrics"]["seo"] == {
        "availableCount": 2,
        "unavailableCount": 1,
        "min": 88,
        "max": 90,
        "spread": 2,
    }
    assert controlled["metrics"]["cls"] == {
        "availableCount": 2,
        "unavailableCount": 1,
        "min": 0.012,
        "max": 0.024,
        "spread": 0.012,
    }
    assert emitted["complete"]["metrics"]["accessibility"] == {
        "availableCount": 2,
        "unavailableCount": 0,
        "min": 84,
        "max": 86,
        "spread": 2,
    }
    assert emitted["complete"]["metrics"]["bestPractices"] == {
        "availableCount": 2,
        "unavailableCount": 0,
        "min": 94,
        "max": 96,
        "spread": 2,
    }
    assert emitted["complete"]["metrics"]["seo"] == {
        "availableCount": 2,
        "unavailableCount": 0,
        "min": 88,
        "max": 90,
        "spread": 2,
    }
    assert emitted["complete"]["metrics"]["cls"] == {
        "availableCount": 2,
        "unavailableCount": 0,
        "min": 0.012,
        "max": 0.024,
        "spread": 0.012,
    }
    assert controlled["metrics"]["lcpInvalidated"] == {
        "trueCount": 1,
        "falseCount": 1,
        "unavailableCount": 1,
    }
    assert controlled["samples"][1]["report"] == "brandguard-sample-02.json"
    assert [
        (
            sample["report"],
            sample["accessibility"],
            sample["bestPractices"],
            sample["seo"],
            sample["cls"],
        )
        for sample in controlled["samples"]
    ] == [
        ("brandguard.json", 84, 96, 88, 0.012),
        ("brandguard-sample-02.json", 86, 94, 90, 0.024),
        ("brandguard-sample-03.json", None, 92, None, None),
    ]
    assert controlled["samples"][2]["unavailableMetrics"] == [
        {
            "field": "accessibility",
            "source": "categories.accessibility.score",
        },
        {
            "field": "seo",
            "source": "categories.seo.score",
        },
        {
            "field": "cls",
            "source": "audits.cumulative-layout-shift.numericValue",
        },
        {
            "field": "fcpMs",
            "source": "audits.first-contentful-paint.numericValue",
        },
        {
            "field": "tbtMs",
            "source": "audits.total-blocking-time.numericValue",
        },
        {
            "field": "lcpInvalidated",
            "source": "audits.metrics.details.items[].lcpInvalidated",
        },
    ]
    assert emitted["controlledNotice"] == (
        "Unavailable repeated BrandGuard sample metrics:\n"
        "  sample 3 (brandguard-sample-03.json): accessibility, seo, cls, "
        "fcpMs, tbtMs, lcpInvalidated"
    )
    assert emitted["detailedNotice"] == (
        "Unavailable repeated BrandGuard sample metrics:\n"
        "  sample 3 (brandguard-sample-03.json): accessibility, seo, cls, "
        "fcpMs, tbtMs, lcpInvalidated\n"
        "    accessibility: categories.accessibility.score\n"
        "    seo: categories.seo.score\n"
        "    cls: audits.cumulative-layout-shift.numericValue\n"
        "    fcpMs: audits.first-contentful-paint.numericValue\n"
        "    tbtMs: audits.total-blocking-time.numericValue\n"
        "    lcpInvalidated: audits.metrics.details.items[].lcpInvalidated"
    )
    assert emitted["completeNotice"] == ""
    assert emitted["sampleScores"] == (
        "  sample 1 (brandguard.json): accessibility 84; best-practices 96; "
        "SEO 88; CLS 0.012\n"
        "  sample 2 (brandguard-sample-02.json): accessibility 86; "
        "best-practices 94; SEO 90; CLS 0.024\n"
        "  sample 3 (brandguard-sample-03.json): accessibility unavailable; "
        "best-practices 92; SEO unavailable; CLS unavailable"
    )

    normal = emitted["normal"]
    assert normal["condition"] == "normal"
    assert normal["controls"] == {"thirdPartyFonts": "in flight", "analytics": "in flight"}
    assert normal["metrics"] == controlled["metrics"]


def test_lighthouse_route_runner_captures_repeat_brandguard_samples_by_condition(tmp_path):
    if os.name == "nt":
        pytest.skip("POSIX fake executables require shebang support unavailable on Windows")
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("Node.js is unavailable")

    root = tmp_path
    (root / "scripts").mkdir()
    shutil.copy2(ROOT / "scripts/lighthouse-routes.mjs", root / "scripts/lighthouse-routes.mjs")
    audit_dir = root / "assets/audit"
    audit_dir.mkdir(parents=True)
    baseline_pages = {name: {} for name in ("homepage", "brandguard", "universe", "search")}
    (root / "assets/docs").mkdir(parents=True, exist_ok=True)
    (root / "assets/docs/performance-baseline-mobile-2026-08-22.json").write_text(
        json.dumps({"pages": baseline_pages}),
        encoding="utf-8",
    )

    lighthouse_dir = root / "node_modules/.bin"
    lighthouse_dir.mkdir(parents=True)
    fake_lighthouse = lighthouse_dir / "lighthouse"
    fake_lighthouse.write_text(
        """#!/usr/bin/env node
const fs = require("node:fs");
const path = require("node:path");
const args = process.argv.slice(2);
if (!args.includes("--chrome-flags=--headless --no-sandbox --disable-dev-shm-usage")) {
  process.exit(31);
}
const reportPath = args.find((arg) => arg.startsWith("--output-path=")).slice("--output-path=".length);
const reportName = path.basename(reportPath);
const sampleIndex = reportName === "brandguard.json"
  ? 1
  : Number(reportName.match(/sample-(\\d+)/)?.[1] || 0);
const isBrandGuard = args[0].includes("/lens-system/okhp3-brandguard/");
const baseUrl = new URL(args[0]).origin;
const sampleOffset = isBrandGuard ? sampleIndex : 0;
const report = {
  lighthouseVersion: "12.8.2",
  environment: { hostUserAgent: "HeadlessChrome/148.0.0.0" },
  configSettings: {
    formFactor: "mobile",
    throttlingMethod: "simulate",
    throttling: {
      rttMs: 150,
      throughputKbps: 1638.4,
      requestLatencyMs: 562.5,
      downloadThroughputKbps: 1474.5600000000002,
      uploadThroughputKbps: 675,
      cpuSlowdownMultiplier: 4
    },
    screenEmulation: {
      mobile: true,
      width: 412,
      height: 823,
      deviceScaleFactor: 1.75,
      disabled: false
    },
    emulatedUserAgent: "Mozilla/5.0 (Linux; Android 11; moto g power (2022)) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Mobile Safari/537.36",
    maxWaitForFcp: 30000,
    maxWaitForLoad: 45000,
    pauseAfterFcpMs: 1000,
    pauseAfterLoadMs: 1000,
    networkQuietThresholdMs: 1000,
    cpuQuietThresholdMs: 1000,
    locale: "en-US",
    disableStorageReset: false,
    clearStorageTypes: ["file_systems", "shader_cache", "service_workers", "cache_storage"],
    auditMode: false,
    gatherMode: false,
    debugNavigation: false,
    channel: "cli",
    usePassiveGathering: false,
    disableFullPageScreenshot: false,
    skipAboutBlank: false,
    blankPage: "about:blank",
    ignoreStatusCode: false,
    additionalTraceCategories: null,
    extraHeaders: null,
    precomputedLanternData: null,
    onlyAudits: null,
    onlyCategories: null,
    skipAudits: null,
    blockedUrlPatterns: args.some((arg) => arg.startsWith("--blocked-url-patterns="))
      ? args
        .filter((arg) => arg.startsWith("--blocked-url-patterns="))
        .map((arg) => arg.slice("--blocked-url-patterns=".length))
      : null
  },
  categories: {
    performance: { score: 0.9 },
    accessibility: { score: 0.7 + sampleOffset * 0.01 },
    "best-practices": { score: 0.8 + sampleOffset * 0.01 },
    seo: { score: 0.9 + sampleOffset * 0.01 }
  },
  audits: {
    "largest-contentful-paint": { numericValue: 3400 + sampleOffset * 100 },
    "cumulative-layout-shift": { numericValue: 0.01 * sampleOffset },
    "total-blocking-time": { numericValue: 10 + sampleOffset * 10 },
    "first-contentful-paint": { numericValue: 100 + sampleOffset * 20 },
    "speed-index": { numericValue: 500 + sampleOffset * 100 },
    "largest-contentful-paint-element": {
      details: { items: [{ items: [{ node: { selector: "#hero-title" } }] }] }
    },
    metrics: { details: { items: [{ lcpInvalidated: sampleOffset === 2 }] } }
  }
};
if (sampleIndex === 3 || (args[0].includes("single-sample.invalid") && isBrandGuard)) {
  delete report.audits["first-contentful-paint"];
}
if (
  isBrandGuard
  && (
    baseUrl === "https://all-lcp-unavailable.invalid"
    || (baseUrl === "https://incomplete-first.invalid" && sampleIndex === 1)
    || (baseUrl === "https://partial-lcp.invalid" && sampleIndex === 2)
  )
) {
  delete report.audits["largest-contentful-paint"];
}
if (
  isBrandGuard
  && sampleIndex === 2
  && process.env.FAKE_MISSING_LCP_INVALIDATED === "1"
) {
  delete report.audits.metrics;
}
if (
  isBrandGuard
  && sampleIndex === 2
  && process.env.FAKE_MALFORMED_LCP_INVALIDATED === "1"
) {
  report.audits.metrics.details.items[0].lcpInvalidated = "false";
}
if (
  isBrandGuard
  && sampleIndex === 1
  && process.env.FAKE_MALFORMED_PRIMARY_LCP_INVALIDATED === "1"
) {
  report.audits.metrics.details.items[0].lcpInvalidated = "false";
}
if (isBrandGuard && process.env.FAKE_MISSING_PERFORMANCE_CATEGORY === "1") {
  delete report.categories.performance.score;
}
fs.writeFileSync(reportPath, JSON.stringify(report));
""",
        encoding="utf-8",
    )
    fake_lighthouse.chmod(0o755)

    fake_chrome = root / "fake-chrome"
    fake_chrome_log = root / "fake-chrome-arguments.txt"
    fake_chrome.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$FAKE_CHROME_ARGS_LOG\"\n"
        "echo 'Chromium 148.0.7778.96'\n",
        encoding="utf-8",
    )
    fake_chrome.chmod(0o755)

    script = root / "scripts/lighthouse-routes.mjs"
    common_args = [
        node_bin,
        str(script),
        "--preset=mobile",
        "--brandguard-samples=3",
        "--date=2099-02-03",
        "--base-url=https://fixture.invalid",
    ]
    environment = {
        **os.environ,
        "CHROME_PATH": str(fake_chrome),
        "FAKE_CHROME_ARGS_LOG": str(fake_chrome_log),
    }
    normal_run = subprocess.run(
        common_args,
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert normal_run.returncode == 0, normal_run.stderr
    assert (
        "Unavailable repeated BrandGuard sample metrics:\n"
        "  sample 3 (brandguard-sample-03.json): fcpMs"
    ) in normal_run.stderr
    assert (
        "BrandGuard normal samples: 3; LCP 3500–3700 ms\n"
        "  repeatability: accessibility 71–73 (spread 2); 3 available, 0 unavailable; "
        "best-practices 81–83 (spread 2); 3 available, 0 unavailable; "
        "SEO 91–93 (spread 2); 3 available, 0 unavailable; "
        "CLS 0.01–0.03 (spread 0.02); 3 available, 0 unavailable\n"
        "  sample 1 (brandguard.json): accessibility 71; best-practices 81; "
        "SEO 91; CLS 0.01\n"
        "  sample 2 (brandguard-sample-02.json): accessibility 72; "
        "best-practices 82; SEO 92; CLS 0.02\n"
        "  sample 3 (brandguard-sample-03.json): accessibility 73; "
        "best-practices 83; SEO 93; CLS 0.03"
    ) in normal_run.stdout
    assert "route homepage (homepage.json)" in normal_run.stderr
    assert (
        "BrandGuard repeated sample 2 (brandguard-sample-02.json)"
        in normal_run.stderr
    )
    assert "sample 1 (brandguard.json)" not in normal_run.stderr
    normal_summary = json.loads(
        (audit_dir / "lighthouse-2099-02-03-mobile/summary.json")
        .read_text(encoding="utf-8")
    )
    normal_samples = normal_summary["brandguardRepeatSamples"]["samples"]
    assert normal_samples[2]["report"] == "brandguard-sample-03.json"
    assert normal_samples[2]["unavailableMetrics"] == [
        {
            "field": "fcpMs",
            "source": "audits.first-contentful-paint.numericValue",
        }
    ]

    all_lcp_args = [
        *common_args[:5],
        "--base-url=https://all-lcp-unavailable.invalid",
        "--run-id=all-lcp-unavailable",
    ]
    all_lcp_run = subprocess.run(
        all_lcp_args,
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert all_lcp_run.returncode == 0, all_lcp_run.stderr
    assert "BrandGuard normal samples: 3; LCP unavailable" in all_lcp_run.stdout
    assert "LCP null–null ms" not in all_lcp_run.stdout
    all_lcp_summary = json.loads(
        (
            audit_dir
            / "lighthouse-2099-02-03-mobile-all-lcp-unavailable/summary.json"
        ).read_text(encoding="utf-8")
    )
    assert all_lcp_summary["brandguardRepeatSamples"]["metrics"]["lcpMs"] == {
        "availableCount": 0,
        "unavailableCount": 3,
        "min": None,
        "max": None,
        "spread": None,
    }

    partial_lcp_run = subprocess.run(
        [
            *common_args[:5],
            "--base-url=https://partial-lcp.invalid",
            "--run-id=partial-lcp",
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert partial_lcp_run.returncode == 0, partial_lcp_run.stderr
    assert "BrandGuard normal samples: 3; LCP 3500–3700 ms" in partial_lcp_run.stdout
    assert (
        "Unavailable repeated BrandGuard sample metrics:\n"
        "  sample 2 (brandguard-sample-02.json): lcpMs"
    ) in partial_lcp_run.stderr
    assert "sample 1 (brandguard.json): lcpMs" not in partial_lcp_run.stderr
    partial_lcp_summary = json.loads(
        (audit_dir / "lighthouse-2099-02-03-mobile-partial-lcp/summary.json")
        .read_text(encoding="utf-8")
    )
    assert partial_lcp_summary["brandguardRepeatSamples"]["metrics"]["lcpMs"] == {
        "availableCount": 2,
        "unavailableCount": 1,
        "min": 3500,
        "max": 3700,
        "spread": 200,
    }

    incomplete_first_run = subprocess.run(
        [
            *common_args[:5],
            "--base-url=https://incomplete-first.invalid",
            "--run-id=incomplete-first",
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert incomplete_first_run.returncode == 0, incomplete_first_run.stderr
    assert (
        "  /lens-system/okhp3-brandguard/ (brandguard.json): lcpMs"
    ) in incomplete_first_run.stderr
    assert incomplete_first_run.stderr.count("lcpMs") == 1
    assert "sample 1 (brandguard.json): lcpMs" not in incomplete_first_run.stderr
    assert (
        "sample 3 (brandguard-sample-03.json): fcpMs"
    ) in incomplete_first_run.stderr
    incomplete_first_summary = json.loads(
        (
            audit_dir
            / "lighthouse-2099-02-03-mobile-incomplete-first/summary.json"
        ).read_text(encoding="utf-8")
    )
    incomplete_first_samples = incomplete_first_summary["brandguardRepeatSamples"]["samples"]
    assert incomplete_first_samples[0]["report"] == "brandguard.json"
    assert incomplete_first_samples[0]["unavailableMetrics"] == [
        {
            "field": "lcpMs",
            "source": "audits.largest-contentful-paint.numericValue",
        }
    ]
    assert incomplete_first_samples[2]["report"] == "brandguard-sample-03.json"
    assert incomplete_first_samples[2]["unavailableMetrics"] == [
        {
            "field": "fcpMs",
            "source": "audits.first-contentful-paint.numericValue",
        }
    ]

    controlled_incomplete_first_run = subprocess.run(
        [
            *common_args[:5],
            "--base-url=https://incomplete-first.invalid",
            "--run-id=controlled-incomplete-first",
            "--controlled",
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert controlled_incomplete_first_run.returncode == 0, (
        controlled_incomplete_first_run.stderr
    )
    assert (
        "  /lens-system/okhp3-brandguard/ (brandguard.json): lcpMs"
    ) in controlled_incomplete_first_run.stderr
    assert controlled_incomplete_first_run.stderr.count("lcpMs") == 1
    assert "sample 1 (brandguard.json): lcpMs" not in (
        controlled_incomplete_first_run.stderr
    )
    assert (
        "Unavailable repeated BrandGuard sample metrics:\n"
        "  sample 3 (brandguard-sample-03.json): fcpMs"
    ) in controlled_incomplete_first_run.stderr
    controlled_incomplete_first_summary = json.loads(
        (
            audit_dir
            / "lighthouse-2099-02-03-mobile-controlled-controlled-incomplete-first"
            / "summary.json"
        ).read_text(encoding="utf-8")
    )
    assert (
        controlled_incomplete_first_summary["runConditions"]["condition"]
        == "controlled"
    )
    controlled_incomplete_first_samples = (
        controlled_incomplete_first_summary["brandguardRepeatSamples"]["samples"]
    )
    assert controlled_incomplete_first_samples[0]["unavailableMetrics"] == [
        {
            "field": "lcpMs",
            "source": "audits.largest-contentful-paint.numericValue",
        }
    ]
    assert controlled_incomplete_first_samples[2]["unavailableMetrics"] == [
        {
            "field": "fcpMs",
            "source": "audits.first-contentful-paint.numericValue",
        }
    ]

    source_environment = {
        **environment,
        "FAKE_MISSING_PERFORMANCE_CATEGORY": "1",
    }
    concise_sources_run = subprocess.run(
        [
            *common_args[:5],
            "--base-url=https://source-fixture.invalid",
            "--run-id=source-concise",
        ],
        cwd=root,
        env=source_environment,
        capture_output=True,
        text=True,
    )
    assert concise_sources_run.returncode == 0, concise_sources_run.stderr
    assert "categories.performance.score" not in concise_sources_run.stderr
    assert "audits.first-contentful-paint.numericValue" not in concise_sources_run.stderr
    assert (
        "Unavailable repeated BrandGuard sample metrics:\n"
        "  sample 2 (brandguard-sample-02.json): performance\n"
        "  sample 3 (brandguard-sample-03.json): performance, fcpMs"
    ) in concise_sources_run.stderr

    detailed_sources_run = subprocess.run(
        [
            *common_args[:5],
            "--base-url=https://source-fixture.invalid",
            "--run-id=source-detailed",
            "--show-unavailable-sources",
        ],
        cwd=root,
        env=source_environment,
        capture_output=True,
        text=True,
    )
    assert detailed_sources_run.returncode == 0, detailed_sources_run.stderr
    assert "categories.performance.score" in detailed_sources_run.stderr
    assert "audits.first-contentful-paint.numericValue" in detailed_sources_run.stderr
    assert (
        "Unavailable repeated BrandGuard sample metrics:\n"
        "  sample 2 (brandguard-sample-02.json): performance\n"
        "    performance: categories.performance.score\n"
        "  sample 3 (brandguard-sample-03.json): performance, fcpMs\n"
        "    performance: categories.performance.score\n"
        "    fcpMs: audits.first-contentful-paint.numericValue"
    ) in detailed_sources_run.stderr
    concise_summary = json.loads(
        (audit_dir / "lighthouse-2099-02-03-mobile-source-concise/summary.json")
        .read_text(encoding="utf-8")
    )
    detailed_summary = json.loads(
        (audit_dir / "lighthouse-2099-02-03-mobile-source-detailed/summary.json")
        .read_text(encoding="utf-8")
    )
    assert detailed_summary == concise_summary
    source_samples = concise_summary["brandguardRepeatSamples"]["samples"]
    assert source_samples[1]["unavailableMetrics"] == [
        {
            "field": "performance",
            "source": "categories.performance.score",
        }
    ]
    assert source_samples[2]["unavailableMetrics"] == [
        {
            "field": "performance",
            "source": "categories.performance.score",
        },
        {
            "field": "fcpMs",
            "source": "audits.first-contentful-paint.numericValue",
        },
    ]

    missing_lcp_invalidation_environment = {
        **environment,
        "FAKE_MISSING_LCP_INVALIDATED": "1",
    }
    missing_lcp_invalidation_args = [
        *common_args[:3],
        "--brandguard-samples=2",
        "--date=2099-02-03",
        "--base-url=https://missing-lcp-invalidation.invalid",
    ]
    concise_missing_lcp_invalidation_run = subprocess.run(
        [
            *missing_lcp_invalidation_args,
            "--run-id=missing-lcp-invalidation-concise",
        ],
        cwd=root,
        env=missing_lcp_invalidation_environment,
        capture_output=True,
        text=True,
    )
    assert concise_missing_lcp_invalidation_run.returncode == 0, (
        concise_missing_lcp_invalidation_run.stderr
    )
    assert (
        "Unavailable repeated BrandGuard sample metrics:\n"
        "  sample 2 (brandguard-sample-02.json): lcpInvalidated"
    ) in concise_missing_lcp_invalidation_run.stderr
    assert "audits.metrics.details.items[].lcpInvalidated" not in (
        concise_missing_lcp_invalidation_run.stderr
    )

    detailed_missing_lcp_invalidation_run = subprocess.run(
        [
            *missing_lcp_invalidation_args,
            "--run-id=missing-lcp-invalidation-detailed",
            "--show-unavailable-sources",
        ],
        cwd=root,
        env=missing_lcp_invalidation_environment,
        capture_output=True,
        text=True,
    )
    assert detailed_missing_lcp_invalidation_run.returncode == 0, (
        detailed_missing_lcp_invalidation_run.stderr
    )
    assert (
        "Unavailable repeated BrandGuard sample metrics:\n"
        "  sample 2 (brandguard-sample-02.json): lcpInvalidated\n"
        "    lcpInvalidated: audits.metrics.details.items[].lcpInvalidated"
    ) in detailed_missing_lcp_invalidation_run.stderr
    missing_lcp_summary = json.loads(
        (
            audit_dir
            / "lighthouse-2099-02-03-mobile-missing-lcp-invalidation-concise/summary.json"
        ).read_text(encoding="utf-8")
    )
    assert missing_lcp_summary["brandguardRepeatSamples"]["samples"][1][
        "unavailableMetrics"
    ] == [
        {
            "field": "lcpInvalidated",
            "source": "audits.metrics.details.items[].lcpInvalidated",
        }
    ]

    controlled_missing_lcp_args = [
        *missing_lcp_invalidation_args,
        "--controlled",
        "--run-id=controlled-missing-lcp-invalidation",
    ]
    controlled_missing_lcp_run = subprocess.run(
        controlled_missing_lcp_args,
        cwd=root,
        env=missing_lcp_invalidation_environment,
        capture_output=True,
        text=True,
    )
    assert controlled_missing_lcp_run.returncode == 0, controlled_missing_lcp_run.stderr
    assert (
        "Unavailable repeated BrandGuard sample metrics:\n"
        "  sample 2 (brandguard-sample-02.json): lcpInvalidated"
    ) in controlled_missing_lcp_run.stderr
    assert "audits.metrics.details.items[].lcpInvalidated" not in (
        controlled_missing_lcp_run.stderr
    )

    controlled_detailed_missing_lcp_run = subprocess.run(
        [
            *missing_lcp_invalidation_args,
            "--controlled",
            "--run-id=controlled-missing-lcp-invalidation-detailed",
            "--show-unavailable-sources",
        ],
        cwd=root,
        env=missing_lcp_invalidation_environment,
        capture_output=True,
        text=True,
    )
    assert controlled_detailed_missing_lcp_run.returncode == 0, (
        controlled_detailed_missing_lcp_run.stderr
    )
    assert (
        "Unavailable repeated BrandGuard sample metrics:\n"
        "  sample 2 (brandguard-sample-02.json): lcpInvalidated\n"
        "    lcpInvalidated: audits.metrics.details.items[].lcpInvalidated"
    ) in controlled_detailed_missing_lcp_run.stderr
    controlled_missing_lcp_summary = json.loads(
        (
            audit_dir
            / "lighthouse-2099-02-03-mobile-controlled-controlled-missing-lcp-invalidation"
            / "summary.json"
        ).read_text(encoding="utf-8")
    )
    assert controlled_missing_lcp_summary["runConditions"]["condition"] == "controlled"
    assert controlled_missing_lcp_summary["brandguardRepeatSamples"]["samples"][1][
        "unavailableMetrics"
    ] == [
        {
            "field": "lcpInvalidated",
            "source": "audits.metrics.details.items[].lcpInvalidated",
        }
    ]

    malformed_lcp_invalidation_environment = {
        **environment,
        "FAKE_MALFORMED_LCP_INVALIDATED": "1",
    }
    malformed_lcp_invalidation_run = subprocess.run(
        [
            *missing_lcp_invalidation_args,
            "--run-id=malformed-lcp-invalidation",
            "--show-unavailable-sources",
        ],
        cwd=root,
        env=malformed_lcp_invalidation_environment,
        capture_output=True,
        text=True,
    )
    assert malformed_lcp_invalidation_run.returncode == 0, (
        malformed_lcp_invalidation_run.stderr
    )
    assert (
        "Unavailable repeated BrandGuard sample metrics:\n"
        "  sample 2 (brandguard-sample-02.json): lcpInvalidated\n"
        "    lcpInvalidated: audits.metrics.details.items[].lcpInvalidated"
    ) in malformed_lcp_invalidation_run.stderr
    malformed_lcp_summary = json.loads(
        (
            audit_dir
            / "lighthouse-2099-02-03-mobile-malformed-lcp-invalidation/summary.json"
        ).read_text(encoding="utf-8")
    )
    malformed_repeats = malformed_lcp_summary["brandguardRepeatSamples"]
    assert malformed_repeats["metrics"]["lcpInvalidated"] == {
        "trueCount": 0,
        "falseCount": 1,
        "unavailableCount": 1,
    }
    malformed_sample = malformed_repeats["samples"][1]
    assert malformed_sample["lcpInvalidated"] is None
    assert malformed_sample["unavailableMetrics"] == [
        {
            "field": "lcpInvalidated",
            "source": "audits.metrics.details.items[].lcpInvalidated",
        }
    ]
    assert fake_chrome_log.read_text(encoding="utf-8").splitlines()
    assert set(fake_chrome_log.read_text(encoding="utf-8").splitlines()) == {"--version"}

    single_sample_run = subprocess.run(
        [
            *common_args[:3],
            "--brandguard-samples=1",
            common_args[4],
            "--base-url=https://single-sample.invalid",
            "--run-id=single-sample",
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert single_sample_run.returncode == 0, single_sample_run.stderr
    assert "Unavailable Lighthouse metrics:" in single_sample_run.stderr
    assert "Unavailable repeated BrandGuard sample metrics:" not in single_sample_run.stderr
    single_sample_summary = json.loads(
        (
            audit_dir
            / "lighthouse-2099-02-03-mobile-single-sample/summary.json"
        ).read_text(encoding="utf-8")
    )
    assert "brandguardRepeatSamples" not in single_sample_summary

    malformed_primary_environment = {
        **environment,
        "FAKE_MALFORMED_PRIMARY_LCP_INVALIDATED": "1",
    }
    malformed_primary_run = subprocess.run(
        [
            *common_args[:3],
            "--brandguard-samples=1",
            common_args[4],
            "--base-url=https://malformed-primary.invalid",
            "--run-id=malformed-primary",
            "--show-unavailable-sources",
        ],
        cwd=root,
        env=malformed_primary_environment,
        capture_output=True,
        text=True,
    )
    assert malformed_primary_run.returncode == 0, malformed_primary_run.stderr
    assert (
        "Unavailable Lighthouse metrics:\n"
        "  /lens-system/okhp3-brandguard/ (brandguard.json): lcpInvalidated\n"
        "    lcpInvalidated: audits.metrics.details.items[].lcpInvalidated"
    ) in malformed_primary_run.stderr
    assert "Unavailable repeated BrandGuard sample metrics:" not in malformed_primary_run.stderr
    malformed_primary_dir = audit_dir / "lighthouse-2099-02-03-mobile-malformed-primary"
    malformed_primary_report = json.loads(
        (malformed_primary_dir / "brandguard.json").read_text(encoding="utf-8")
    )
    assert (
        malformed_primary_report["audits"]["metrics"]["details"]["items"][0][
            "lcpInvalidated"
        ]
        == "false"
    )
    malformed_primary_summary = json.loads(
        (malformed_primary_dir / "summary.json").read_text(encoding="utf-8")
    )
    assert {
        "field": "lcpInvalidated",
        "source": "audits.metrics.details.items[].lcpInvalidated",
    } in malformed_primary_summary["pages"]["brandguard"]["unavailableMetrics"]

    normal_dir = audit_dir / "lighthouse-2099-02-03-mobile"
    original_evidence = {
        path.name: path.read_bytes()
        for path in normal_dir.iterdir()
    }
    repeated_run = subprocess.run(
        common_args,
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert repeated_run.returncode != 0
    assert "Refusing to overwrite it" in repeated_run.stderr
    assert "--run-id=<identifier>" in repeated_run.stderr
    assert "--replace" in repeated_run.stderr
    assert {
        path.name: path.read_bytes()
        for path in normal_dir.iterdir()
    } == original_evidence

    identified_run = subprocess.run(
        [*common_args, "--run-id=rerun-2"],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert identified_run.returncode == 0, identified_run.stderr
    identified_dir = audit_dir / "lighthouse-2099-02-03-mobile-rerun-2"
    assert (identified_dir / "summary.json").is_file()

    (normal_dir / "stale-evidence.json").write_text("stale", encoding="utf-8")
    replacement_run = subprocess.run(
        [*common_args, "--replace"],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert replacement_run.returncode == 0, replacement_run.stderr
    assert not (normal_dir / "stale-evidence.json").exists()
    assert (normal_dir / "summary.json").is_file()

    controlled_run = subprocess.run(
        [*common_args, "--controlled"],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert controlled_run.returncode == 0, controlled_run.stderr

    controlled_dir = audit_dir / "lighthouse-2099-02-03-mobile-controlled"
    normal = json.loads((normal_dir / "summary.json").read_text(encoding="utf-8"))
    controlled = json.loads((controlled_dir / "summary.json").read_text(encoding="utf-8"))
    for output_dir, summary, condition in (
        (normal_dir, normal, "normal"),
        (controlled_dir, controlled, "controlled"),
    ):
        repeat = summary["brandguardRepeatSamples"]
        assert summary["runConditions"]["condition"] == condition
        assert set(summary["runConditions"]["reports"]) == {
            "homepage.json",
            "brandguard.json",
            "universe.json",
            "search.json",
            "brandguard-sample-02.json",
            "brandguard-sample-03.json",
        }
        assert summary["runConditionsReview"]["status"] == "required"
        assert summary["runConditionsReview"]["changedComponents"] == ["chromeFlags"]
        assert {
            tuple(conditions["chromeFlags"])
            for conditions in summary["runConditions"]["reports"].values()
        } == {("--headless", "--no-sandbox", "--disable-dev-shm-usage")}
        assert summary["runConditions"]["reports"]["brandguard-sample-03.json"]["collectionTiming"] == {
            "maxWaitForFcp": 30000,
            "maxWaitForLoad": 45000,
            "pauseAfterFcpMs": 1000,
            "pauseAfterLoadMs": 1000,
            "networkQuietThresholdMs": 1000,
            "cpuQuietThresholdMs": 1000,
        }
        assert summary["runConditions"]["reports"]["brandguard-sample-03.json"]["locale"] == "en-US"
        assert summary["runConditions"]["reports"]["brandguard-sample-03.json"]["storage"] == {
            "disableStorageReset": False,
            "clearStorageTypes": [
                "file_systems",
                "shader_cache",
                "service_workers",
                "cache_storage",
            ],
        }
        assert summary["runConditions"]["reports"]["brandguard.json"]["blockedUrlPatterns"] == (
            [
                "https://fonts.googleapis.com/*",
                "https://fonts.gstatic.com/*",
                "https://www.googletagmanager.com/*",
                "https://www.google-analytics.com/*",
                "https://*.google-analytics.com/*",
            ]
            if condition == "controlled"
            else None
        )
        expected_blocked_patterns = (
            [
                "https://fonts.googleapis.com/*",
                "https://fonts.gstatic.com/*",
                "https://www.googletagmanager.com/*",
                "https://www.google-analytics.com/*",
                "https://*.google-analytics.com/*",
            ]
            if condition == "controlled"
            else None
        )
        assert {
            report_name: summary["runConditions"]["reports"][report_name][
                "blockedUrlPatterns"
            ]
            for report_name in (
                "brandguard.json",
                "brandguard-sample-02.json",
                "brandguard-sample-03.json",
            )
        } == {
            "brandguard.json": expected_blocked_patterns,
            "brandguard-sample-02.json": expected_blocked_patterns,
            "brandguard-sample-03.json": expected_blocked_patterns,
        }
        assert repeat["condition"] == condition
        assert repeat["sampleCount"] == 3
        assert repeat["metrics"]["lcpMs"] == {
            "availableCount": 3,
            "unavailableCount": 0,
            "min": 3500,
            "max": 3700,
            "spread": 200,
        }
        assert repeat["metrics"]["fcpMs"] == {
            "availableCount": 2,
            "unavailableCount": 1,
            "min": 120,
            "max": 140,
            "spread": 20,
        }
        assert [sample["report"] for sample in repeat["samples"]] == [
            "brandguard.json",
            "brandguard-sample-02.json",
            "brandguard-sample-03.json",
        ]
        assert [
            (
                sample["accessibility"],
                sample["bestPractices"],
                sample["seo"],
                sample["cls"],
            )
            for sample in repeat["samples"]
        ] == [
            (71, 81, 91, 0.01),
            (72, 82, 92, 0.02),
            (73, 83, 93, 0.03),
        ]
        assert repeat["samples"][2]["fcpMs"] is None
        assert set(repeat["samples"][2]) == {
            "sample",
            "report",
            "accessibility",
            "bestPractices",
            "seo",
            "cls",
            "fcpMs",
            "speedIndexMs",
            "lcpMs",
            "tbtMs",
            "lcpInvalidated",
            "unavailableMetrics",
        }
        assert repeat["samples"][2]["unavailableMetrics"] == [
            {
                "field": "fcpMs",
                "source": "audits.first-contentful-paint.numericValue",
            }
        ]
        assert all((output_dir / report).is_file() for report in (
            "brandguard.json",
            "brandguard-sample-02.json",
            "brandguard-sample-03.json",
        ))


def test_responsive_qa_browser_fixture_isolates_pages_and_preserves_failures(tmp_path):
    node_bin = os.environ.get("ASKJAMIE_NODE") or shutil.which("node")
    bundled_node = Path(
        "/Users/okh/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"
    )
    if not node_bin and bundled_node.exists():
        node_bin = str(bundled_node)
    node_modules = os.environ.get("NODE_PATH")
    repo_modules = ROOT / "node_modules"
    if not node_modules and (repo_modules / "playwright").exists():
        node_modules = str(repo_modules)
    bundled_modules = Path(
        "/Users/okh/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules"
    )
    if not node_modules and (bundled_modules / "playwright").exists():
        node_modules = str(bundled_modules)
    if not node_bin or not node_modules or not (Path(node_modules) / "playwright").exists():
        pytest.skip("Playwright runtime is unavailable")

    events = []
    state = {"active": 0, "max_active": 0, "page_active": 0, "page_max_active": 0}
    page_paths = {
        "/lazy/", "/lazy-unobserved/", "/lazy-late/", "/clean/", "/abort/",
        "/console-404/", "/timeout/", "/third-party-warning/"
    }
    lock = threading.Lock()
    png_header = b"\x89PNG\r\n\x1a\n"
    lazy_png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/"
        "x8AAwMCAO+/n5sAAAAASUVORK5CYII="
    )

    class FixtureHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, _format, *_args):
            return

        def _record(self, phase, path):
            with lock:
                events.append((phase, path, time.monotonic()))
                if path in page_paths:
                    if phase == "start":
                        state["page_active"] += 1
                        state["page_max_active"] = max(
                            state["page_max_active"], state["page_active"]
                        )
                    elif phase == "end":
                        state["page_active"] -= 1

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path in page_paths:
                self._record("start", path)
                body = {
                    "/lazy/": '<div style="height: 200px"></div><img src="/slow-lazy.png" loading="lazy" width="10" height="10">',
                    "/lazy-unobserved/": (
                        '<div style="height:100000px"></div>'
                        '<img id="unobserved-lazy" src="/unobserved-lazy.png" '
                        'loading="lazy" width="10" height="10">'
                        '<script>window.addEventListener("scroll", () => { '
                        'const image = document.getElementById("unobserved-lazy"); '
                        'if (image?.getAttribute("src") === "/unobserved-lazy.png") '
                        'image.src = "data:image/png;base64,iVBORw0KGgo="; '
                        '}, { once: true });</script>'
                    ),
                    "/lazy-late/": (
                        '<div style="height:100000px"></div>'
                        '<img id="late-lazy" src="/late-lazy.png" '
                        'loading="lazy" width="10" height="10">'
                        '<script>window.addEventListener("scroll", () => { '
                        'const image = document.getElementById("late-lazy"); '
                        'image.src = "data:image/png;base64,iVBORw0KGgo="; '
                        'setTimeout(() => { image.src = "/late-lazy.png"; }, 5200); '
                        '}, { once: true });</script>'
                    ),
                    "/clean/": "",
                    "/abort/": '<img src="/aborted.png" width="10" height="10">',
                    "/console-404/": '<script>console.error("fixture console failure")</script><img src="/missing.png" width="10" height="10">',
                    "/timeout/": '<img src="/delayed.png" width="10" height="10">',
                    "/connection-reset/": "",
                    "/third-party-warning/": (
                        '<img src="https://www.googletagmanager.com/fixture.png" '
                        'width="10" height="10">'
                    ),
                }[path]
                payload = (
                    '<!doctype html><html><head><meta name="viewport" '
                    'content="width=device-width"><title>Fixture</title></head>'
                    f"<body><h1>Fixture</h1>{body}</body></html>"
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                time.sleep(0.01)
                self._record("end", path)
                return

            if path == "/connection-reset/":
                self._record("start", path)
                self.close_connection = True
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self.connection.close()
                self._record("end", path)
                return

            if path == "/missing.png":
                self.send_error(404, "missing fixture image")
                return

            if path == "/aborted.png":
                self._record("start", path)
                self.close_connection = True
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                self._record("end", path)
                return

            if path == "/slow-lazy.png":
                self._record("start", path)
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(lazy_png)))
                    self.end_headers()
                    self.wfile.flush()
                    time.sleep(0.5)
                    self.wfile.write(lazy_png)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    self._record("end", path)
                return

            if path == "/late-lazy.png":
                self._record("start", path)
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(png_header)))
                self.end_headers()
                self.wfile.write(png_header)
                self._record("end", path)
                return

            if path == "/delayed.png":
                self._record("start", path)
                with lock:
                    state["active"] += 1
                    state["max_active"] = max(state["max_active"], state["active"])
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", "999999")
                    self.end_headers()
                    self.wfile.write(png_header)
                    self.wfile.flush()
                    time.sleep(6)
                finally:
                    with lock:
                        state["active"] -= 1
                    self._record("end", path)
                return

            self.send_error(404, "unknown fixture route")

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    server.daemon_threads = True
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    fixture_root = tmp_path / "runner"
    (fixture_root / "scripts").mkdir(parents=True)
    _copy_responsive_qa_fixture(ROOT / "scripts/responsive-qa.mjs", fixture_root / "scripts/responsive-qa.mjs")
    sitemap = (
        '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + "".join(f"<url><loc>https://askjamie.bot{path}</loc></url>" for path in (
            "/lazy/", "/lazy-unobserved/", "/lazy-late/", "/clean/", "/abort/",
            "/console-404/", "/timeout/", "/connection-reset/", "/third-party-warning/"
        ))
        + "</urlset>"
    )
    (fixture_root / "sitemap.xml").write_text(sitemap, encoding="utf-8")
    _ensure_sitemap_fixture_pages(fixture_root, allow_fixture_sitemap_roots=True)

    # Each run covers the full route/viewport matrix; budget for slower shared runners.
    browser_qa_timeout_seconds = 300
    try:
        result = subprocess.run(
            [
                node_bin,
                "scripts/responsive-qa.mjs",
                f"--base=http://127.0.0.1:{server.server_address[1]}",
            ],
            cwd=fixture_root,
            text=True,
            capture_output=True,
            timeout=browser_qa_timeout_seconds,
            env={**os.environ, "NODE_PATH": node_modules},
        )
        report = json.loads(
            (fixture_root / "assets/audit/responsive-qa/results.json").read_text(
                encoding="utf-8"
            )
        )
        (fixture_root / "sitemap.xml").write_text(
            '<?xml version="1.0"?><urlset '
            'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            '<url><loc>https://askjamie.bot/lazy-unobserved/</loc></url>'
            "</urlset>",
            encoding="utf-8",
        )
        for relative_path in (
            "lazy",
            "lazy-late",
            "clean",
            "abort",
            "console-404",
            "timeout",
            "connection-reset",
            "third-party-warning",
        ):
            shutil.rmtree(fixture_root / relative_path)
        _ensure_sitemap_fixture_pages(fixture_root, allow_fixture_sitemap_roots=True)
        warning_only_result = subprocess.run(
            [
                node_bin,
                "scripts/responsive-qa.mjs",
                f"--base=http://127.0.0.1:{server.server_address[1]}",
            ],
            cwd=fixture_root,
            text=True,
            capture_output=True,
            timeout=browser_qa_timeout_seconds,
            env={**os.environ, "NODE_PATH": node_modules},
        )
        warning_only_report = json.loads(
            (fixture_root / "assets/audit/responsive-qa/results.json").read_text(
                encoding="utf-8"
            )
        )
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)

    rows = {
        path: [row for row in report["results"] if path in row["url"]]
        for path in (
            "/lazy/", "/lazy-unobserved/", "/lazy-late/", "/clean/", "/abort/",
            "/console-404/", "/timeout/", "/connection-reset/", "/third-party-warning/"
        )
    }

    assert result.returncode == 1
    assert warning_only_result.returncode == 0, warning_only_result.stdout
    for saved_report, run_result in (
        (report, result),
        (warning_only_report, warning_only_result),
    ):
        saved_rows = saved_report["results"]
        passing_rows = sum(1 for row in saved_rows if row["pass"])
        failing_rows = sum(1 for row in saved_rows if not row["pass"])
        assert saved_report["total_checks"] == len(saved_rows)
        assert saved_report["passing_checks"] == passing_rows
        assert saved_report["failing_checks"] == failing_rows
        assert saved_report["total_checks"] == passing_rows + failing_rows
        warning_count = sum(len(row.get("warnings", [])) for row in saved_rows)
        summary = re.search(
            r"^Total: (\d+) checks — (\d+) passed, (\d+) failures, (\d+) warnings$",
            run_result.stdout,
            re.MULTILINE,
        )
        assert summary, run_result.stdout
        assert int(summary.group(1)) == saved_report["total_checks"]
        assert int(summary.group(2)) == saved_report["passing_checks"]
        assert int(summary.group(3)) == saved_report["failing_checks"]
        assert int(summary.group(4)) == warning_count
        warning_counts = Counter(
            warning
            for row in saved_rows
            for warning in row.get("warnings", [])
        )
        assert any(count > 1 for count in warning_counts.values())
        assert "Warnings by message:" in run_result.stdout
        warning_output = run_result.stdout.split("Warnings by message:", 1)[1]
        warning_output = warning_output.split("\nBrowser QA duration:", 1)[0]
        breakdown = re.findall(
            r"^  (\d+) occurrences — (.+)$",
            warning_output,
            re.MULTILINE,
        )
        assert [(int(count), message) for count, message in breakdown] == sorted(
            ((count, message) for message, count in warning_counts.items()),
            key=lambda item: (-item[0], item[1]),
        )
    report_warning_counts = Counter(
        warning
        for row in report["results"]
        for warning in row.get("warnings", [])
    )
    assert len(report_warning_counts) > 1
    assert any(count > 1 for count in report_warning_counts.values())
    assert report["failing_checks"] > 0
    assert warning_only_report["failing_checks"] == 0
    assert warning_only_report["results"]
    assert all(row["pass"] and not row["errors"] for row in warning_only_report["results"])
    assert any(row["warnings"] for row in warning_only_report["results"])
    assert report["mode"] == "playwright"
    assert report["status"] == "complete"
    first_warning_count = sum(
        len(row.get("warnings", [])) for row in report["results"]
    )
    warning_only_count = sum(
        len(row.get("warnings", [])) for row in warning_only_report["results"]
    )
    assert (
        "Browser QA warning trend: current "
        f"{first_warning_count}; no previous completed browser report."
    ) in result.stdout
    assert "Browser QA warning trend: current " in warning_only_result.stdout
    assert (
        f"previous completed browser report: {first_warning_count} warnings"
    ) in warning_only_result.stdout
    baseline_path = (
        fixture_root
        / "assets/audit/responsive-qa/results.completed-browser-baseline.json"
    )
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert baseline["mode"] == "playwright"
    assert baseline["status"] == "complete"
    assert baseline["warning_count"] == warning_only_count
    assert baseline["generated"] == warning_only_report["generated"]
    runtime = re.search(
        r"^Browser runtime: Playwright (\d+\.\d+\.\d+); Chromium (\d+(?:\.\d+){2,})$",
        result.stdout,
        re.MULTILINE,
    )
    assert runtime, result.stdout
    assert report["playwright_version"] == runtime.group(1)
    assert report["chromium_version"] == runtime.group(2)
    assert report["browser_engine"] == "chromium"
    assert report["browser_version"] == runtime.group(2)
    assert warning_only_report["playwright_version"] == runtime.group(1)
    assert warning_only_report["chromium_version"] == runtime.group(2)
    assert warning_only_report["browser_engine"] == "chromium"
    assert warning_only_report["browser_version"] == runtime.group(2)
    assert report["viewports_checked"] == RESPONSIVE_QA_VIEWPORT_COUNT
    assert report["total_checks"] == 9 * RESPONSIVE_QA_VIEWPORT_COUNT
    expected_viewports = {
        "mobile-320",
        "mobile-349-reservation-edge",
        "mobile-350-edge",
        "mobile-355-boundary",
        "mobile-356-gap",
        "mobile-357-font-gap",
        "mobile-358-font-gap",
        "mobile-359-font-gap",
        "mobile-360",
        "mobile-361-font-gap",
        "mobile-362-font-gap",
        "mobile-363-font-gap",
        "mobile-364-gap",
        "mobile-365-boundary",
        "mobile-370-edge",
        "mobile-371-reservation-edge",
        "mobile-390",
        "mobile-400-edge",
        "mobile-401-edge",
        "mobile-430",
        "tablet-768",
        "tablet-769-edge",
        "tablet-899",
        "tablet-900-edge",
        "desktop-1024",
        "desktop-1280",
        "desktop-1440",
        "desktop-1920",
    }
    assert {
        row["viewport"] for row in rows["/lazy/"]
    } == expected_viewports
    assert {
        row["width"] for row in rows["/lazy/"]
        if row["viewport"] in {"tablet-769-edge", "tablet-900-edge"}
    } == {769, 900}
    assert all(
        {row["viewport"] for row in rows[path]} == expected_viewports
        for path in rows
    )
    assert all(
        not row["pass"]
        and any(
            error.startswith("navigation transport failure:")
            and re.search(r"net::ERR_(?:CONNECTION_RESET|EMPTY_RESPONSE)\b", error)
            for error in row["errors"]
        )
        and not any("DOMContentLoaded not observed" in warning for warning in row["warnings"])
        for row in rows["/connection-reset/"]
    ), json.dumps(rows["/connection-reset/"], indent=2)
    reset_terminal_rows = re.findall(
        r"^  FAIL\s+(\S+)\s+/connection-reset/$",
        result.stdout,
        re.MULTILINE,
    )
    assert set(reset_terminal_rows) == expected_viewports
    assert len(reset_terminal_rows) == len(expected_viewports)
    assert all(
        any(error in result.stdout for error in row["errors"])
        for row in rows["/connection-reset/"]
    )
    assert sum(
        sum(
            bool(
                re.search(
                    r"net::ERR_(?:CONNECTION_RESET|EMPTY_RESPONSE)\b",
                    error,
                )
            )
            for error in row["errors"]
            if error.startswith("navigation transport failure: page.goto:")
        )
        for row in rows["/connection-reset/"]
    ) == RESPONSIVE_QA_VIEWPORT_COUNT
    lazy_failures = [row for row in rows["/lazy/"] if not row["pass"]]
    assert not lazy_failures, json.dumps(lazy_failures, indent=2)
    lazy_warnings = [row for row in rows["/lazy/"] if row["warnings"]]
    assert not lazy_warnings, json.dumps(lazy_warnings, indent=2)
    assert all(row["pass"] for row in rows["/lazy-unobserved/"])
    assert all(
        any(
            "lazy image request was never triggered" in warning
            and row["url"] in warning
            and "unobserved-lazy.png" in warning
            for warning in row["warnings"]
        )
        for row in rows["/lazy-unobserved/"]
    )
    assert "WARN  /lazy-unobserved/" in result.stdout
    assert any(
        row["pass"] and warning in result.stdout
        for row in rows["/lazy-unobserved/"]
        for warning in row["warnings"]
    )
    assert not any(
        event[1] == "/unobserved-lazy.png" for event in events
    )
    assert all(row["pass"] for row in rows["/lazy-late/"])
    late_lazy_warnings = [
        (row, warning)
        for row in rows["/lazy-late/"]
        for warning in row["warnings"]
        if "late-lazy.png" in warning
    ]
    assert any(
        "lazy image request observed too late" in warning
        and "expected within 5000ms" in warning
        and row["url"] in warning
        for row, warning in late_lazy_warnings
    ), json.dumps(late_lazy_warnings, indent=2)
    late_lazy_starts = [
        event for event in events
        if event[0] == "start" and event[1] == "/late-lazy.png"
    ]
    # Every viewport's report above proves the browser observed the delayed
    # request. Do not require every request to reach the fixture handler after
    # Playwright closes its pages; socket acceptance can race with teardown.
    assert late_lazy_starts
    assert all(row["pass"] for row in rows["/clean/"])
    assert all(any("REQUEST FAILED" in error or "BROKEN IMG" in error for error in row["errors"])
               for row in rows["/abort/"])
    assert all(any("CONSOLE:" in error for error in row["errors"]) and
               any("HTTP 404" in error for error in row["errors"])
               for row in rows["/console-404/"])
    assert all(any("BROKEN IMG" in error or "REQUEST FAILED" in error for error in row["errors"])
               for row in rows["/timeout/"])
    assert all(row["pass"] and not row["errors"] for row in rows["/third-party-warning/"])
    third_party_warning = "blocked third-party resources: https://www.googletagmanager.com/fixture.png"
    assert all(
        any(third_party_warning in warning for warning in row["warnings"])
        for row in rows["/third-party-warning/"]
    )
    assert "WARN  /third-party-warning/" in result.stdout
    assert third_party_warning in result.stdout
    assert state["page_max_active"] <= 4
    lazy_starts = [event for event in events if event[0] == "start" and event[1] == "/slow-lazy.png"]
    assert len(lazy_starts) == len(rows["/lazy/"]) == len(expected_viewports)

    evidence = {
        "schema_version": 1,
        "generated": report["generated"],
        "playwright_version": report["playwright_version"],
        "browser_engine": report["browser_engine"],
        "browser_version": report["browser_version"],
        "cases": {
            "normal": {
                "status": "passed",
                "request_start_count": len(lazy_starts),
                "viewport_count": len(rows["/lazy/"]),
            },
            "never-started": {
                "status": "expected_warning",
                "request_start_count": sum(
                    event[0] == "start" and event[1] == "/unobserved-lazy.png"
                    for event in events
                ),
                "warning_count": len(rows["/lazy-unobserved/"]),
            },
            "late-starting": {
                "status": "expected_warning",
                "request_deadline_ms": 5000,
                "request_start_count": len(late_lazy_starts),
                "late_warning_count": len(late_lazy_warnings),
            },
        },
    }
    assert evidence["cases"]["normal"]["status"] == "passed"
    assert evidence["cases"]["normal"]["request_start_count"] == len(expected_viewports)
    assert evidence["cases"]["never-started"]["request_start_count"] == 0
    assert evidence["cases"]["never-started"]["warning_count"] == len(expected_viewports)
    assert evidence["cases"]["late-starting"]["request_start_count"] > 0
    assert evidence["cases"]["late-starting"]["late_warning_count"] > 0
    evidence_dir = (
        Path(os.environ["LAZY_IMAGE_EVIDENCE_DIR"])
        if os.environ.get("LAZY_IMAGE_EVIDENCE_DIR")
        else fixture_root / "assets/audit/responsive-qa"
    )
    evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence_path = evidence_dir / f"lazy-image-{evidence['browser_engine']}.json"
    evidence_path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    assert json.loads(evidence_path.read_text(encoding="utf-8")) == evidence


def test_index_freshness_checks_content_instead_of_checkout_times(tmp_path, monkeypatch):
    def load(filename):
        spec = importlib.util.spec_from_file_location(filename, ROOT / "scripts" / filename)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    builder = load("build-search-index.py")
    audit = load("audit-site.py")
    monkeypatch.setattr(builder, "REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(audit, "ROOT", tmp_path)
    page = tmp_path / "index.html"
    page.write_text('<html><head><title>Original</title></head><body><h1>Original</h1><main>Original text</main></body></html>')
    index = tmp_path / "assets/data/search-index.json"
    index.parent.mkdir(parents=True)
    index.write_text(json.dumps(builder.build_index_document()))
    os.utime(index, (1, 1))
    assert audit.check_search_index_freshness([page]) == []
    page.write_text(page.read_text().replace("Original", "Changed"))
    os.utime(page, (0, 0))
    assert "stale" in audit.check_search_index_freshness([page])[0]
