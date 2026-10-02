from __future__ import annotations

import base64
import http.server
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_COMMAND_RE = re.compile(r"scripts/([A-Za-z0-9_.-]+)")
ARCHIVED_SCRIPT_RE = re.compile(r"scripts/archive/[^\s`'\"|;&]+")


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
[ "$count" -gt 1 ]
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
        cwd=ROOT,
        text=True,
        capture_output=True,
        env={**os.environ, "NODE_OPTIONS": f"--require {preload}"},
    )

    assert result.returncode == 1
    assert "Required browser QA could not start" in result.stderr
    assert "static-lint mode" not in result.stdout


def test_responsive_qa_keeps_csp_suppression_narrow_and_reports_resource_failures():
    source = (ROOT / "scripts/responsive-qa.mjs").read_text(encoding="utf-8")

    assert "effectiveConsoleErrors" not in source
    assert "isMermaidInlineStyleWarning" in source
    assert "REQUEST FAILED: [" in source
    assert "HTTP ${r.status}: [" in source
    assert "CONSOLE: " in source


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
import { CONTROLLED_BLOCKED_URL_PATTERNS, HISTORICAL_REFERENCE_MEASUREMENT_STACK, HISTORICAL_REFERENCE_RUN_CONDITIONS, createSummary, LIGHTHOUSE_ROUTES, parseChromiumVersion, summarizePage, summarizeRunConditions } from "./scripts/lighthouse-routes.mjs";

const report = JSON.parse(readFileSync("tests/fixtures/lighthouse-summary-report.json", "utf8"));
const baseline = { pages: {
  homepage: { performance: 80, lcpMs: 1000 },
  brandguard: { performance: 88, lcpMs: 2000 },
  universe: { performance: 90, lcpMs: 2500 },
  search: { performance: 91, lcpMs: 3000 }
}, measurementStack: HISTORICAL_REFERENCE_MEASUREMENT_STACK };
const baselineRunConditions = summarizeRunConditions(report);
const allBaselineRunConditions = Object.fromEntries(
  Object.keys(LIGHTHOUSE_ROUTES).map((name) => [`${name}.json`, baselineRunConditions])
);
const controlledRunConditions = Object.fromEntries(
  Object.entries(allBaselineRunConditions).map(([name, conditions]) => [
    name,
    { ...conditions, blockedUrlPatterns: CONTROLLED_BLOCKED_URL_PATTERNS }
  ])
);
const emit = (
  controlled,
  measurementStack,
  runConditionsReports = controlled ? controlledRunConditions : allBaselineRunConditions
) => {
  const summary = createSummary({
    date: "2099-01-02",
    preset: "mobile",
    controlled,
    baseUrl: "https://fixture.invalid",
    measurementStack,
    baselineMeasurementStack: baseline.measurementStack,
    runConditionsReports,
    baselineRunConditions: HISTORICAL_REFERENCE_RUN_CONDITIONS
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
const changedRunConditions = {
  ...baselineRunConditions,
  throttling: {
    ...baselineRunConditions.throttling,
    cpuSlowdownMultiplier: 5
  }
};
process.stdout.write(JSON.stringify({
  routes: LIGHTHOUSE_ROUTES,
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

    normal = emitted["normal"]
    assert emitted["parsedChromiumVersion"] == "153.0.8010.12"
    assert normal["schemaVersion"] == 4
    assert normal["capturedAt"] == "2099-01-02"
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
    assert normal["environment"] == "Local or supplied static server, mobile preset"
    assert normal["runConditions"]["condition"] == "normal"
    assert normal["runConditions"]["reports"]["homepage.json"]["formFactor"] == "mobile"
    assert normal["runConditions"]["reports"]["homepage.json"]["blockedUrlPatterns"] is None
    assert normal["runConditions"]["reports"]["homepage.json"]["throttling"] == {
        "rttMs": 150,
        "throughputKbps": 1638.4,
        "requestLatencyMs": 562.5,
        "downloadThroughputKbps": 1474.5600000000002,
        "uploadThroughputKbps": 675,
        "cpuSlowdownMultiplier": 4,
    }
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
        "formFactor",
        "throttling.cpuSlowdownMultiplier",
    ]
    assert run_condition_change["runConditionsReview"]["changedReports"] == [{
        "report": "brandguard.json",
        "changedComponents": ["throttling.cpuSlowdownMultiplier"],
    }, {
        "report": "search.json",
        "changedComponents": ["formFactor"],
    }]
    assert "Owner review is required" in run_condition_change["runConditionsReview"]["action"]

    missing_run_conditions = emitted["missingRunConditions"]
    assert missing_run_conditions["runConditionsReview"]["status"] == "required"
    assert "formFactor" in missing_run_conditions["runConditionsReview"]["changedComponents"]
    assert "blockedUrlPatterns" in missing_run_conditions["runConditionsReview"]["changedComponents"]

    controlled = emitted["controlled"]
    assert controlled["tool"] == "Lighthouse 13.5.0"
    assert controlled["measurementStackReview"]["status"] == "required"
    assert controlled["measurementStackReview"]["changedComponents"] == [
        "lighthouseVersion",
        "chromiumVersion",
    ]
    assert "Owner review is required" in controlled["measurementStackReview"]["action"]
    assert controlled["environment"] == "Local or supplied static server, controlled mobile preset"
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
        assert summary["schemaVersion"] == 4
        assert summary["property"] == "https://fixture.invalid"
        assert summary["baseline"] == "assets/audit/lighthouse-baseline-2026-08-22.json"
        assert set(summary["pages"]) == set(expected_routes)
        for name, path in expected_routes.items():
            assert summary["pages"][name] == {
                "path": path,
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
            "source": "audits.largest-contentful-paint-element.details.items[0].items[0].node.selector",
        },
    ]


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


def test_lighthouse_brandguard_repeat_summary_keeps_conditions_and_missing_values_separate(tmp_path):
    runner = tmp_path / "repeat-summary-fixture.mjs"
    runner.write_text(
        """
import { summarizeBrandGuardSamples } from "./scripts/lighthouse-routes.mjs";

const pages = [
  { fcpMs: 100, speedIndexMs: 500, lcpMs: 3500, tbtMs: 20, lcpInvalidated: false },
  { fcpMs: 140, speedIndexMs: 700, lcpMs: 3700, tbtMs: 40, lcpInvalidated: true },
  { fcpMs: null, speedIndexMs: 650, lcpMs: 3600, tbtMs: null, lcpInvalidated: null }
];
const samples = pages.map((page, index) => ({
  report: index === 0 ? "brandguard.json" : `brandguard-sample-0${index + 1}.json`,
  page
}));
process.stdout.write(JSON.stringify({
  controlled: summarizeBrandGuardSamples(samples, { controlled: true }),
  normal: summarizeBrandGuardSamples(samples, { controlled: false })
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
    assert controlled["metrics"]["lcpInvalidated"] == {
        "trueCount": 1,
        "falseCount": 1,
        "unavailableCount": 1,
    }
    assert controlled["samples"][1]["report"] == "brandguard-sample-02.json"

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
    (audit_dir / "lighthouse-baseline-2026-08-22.json").write_text(
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
const reportPath = args.find((arg) => arg.startsWith("--output-path=")).slice("--output-path=".length);
const reportName = path.basename(reportPath);
const sampleIndex = reportName === "brandguard.json"
  ? 1
  : Number(reportName.match(/sample-(\\d+)/)?.[1] || 0);
const isBrandGuard = args[0].includes("/lens-system/okhp3-brandguard/");
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
    blockedUrlPatterns: args.some((arg) => arg.startsWith("--blocked-url-patterns="))
      ? args
        .filter((arg) => arg.startsWith("--blocked-url-patterns="))
        .map((arg) => arg.slice("--blocked-url-patterns=".length))
      : null
  },
  categories: {
    performance: { score: 0.9 },
    accessibility: { score: 1 },
    "best-practices": { score: 1 },
    seo: { score: 1 }
  },
  audits: {
    "largest-contentful-paint": { numericValue: 3400 + sampleOffset * 100 },
    "cumulative-layout-shift": { numericValue: 0.01 },
    "total-blocking-time": { numericValue: 10 + sampleOffset * 10 },
    "first-contentful-paint": { numericValue: 100 + sampleOffset * 20 },
    "speed-index": { numericValue: 500 + sampleOffset * 100 },
    "largest-contentful-paint-element": {
      details: { items: [{ items: [{ node: { selector: "#hero-title" } }] }] }
    },
    metrics: { details: { items: [{ lcpInvalidated: sampleOffset === 2 }] } }
  }
};
fs.writeFileSync(reportPath, JSON.stringify(report));
""",
        encoding="utf-8",
    )
    fake_lighthouse.chmod(0o755)

    fake_chrome = root / "fake-chrome"
    fake_chrome.write_text("#!/bin/sh\necho 'Chromium 148.0.7778.96'\n", encoding="utf-8")
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
    environment = {**os.environ, "CHROME_PATH": str(fake_chrome)}
    normal_run = subprocess.run(
        common_args,
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert normal_run.returncode == 0, normal_run.stderr
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
        assert summary["runConditionsReview"]["status"] == "not-required"
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
        assert repeat["condition"] == condition
        assert repeat["sampleCount"] == 3
        assert repeat["metrics"]["lcpMs"] == {
            "availableCount": 3,
            "unavailableCount": 0,
            "min": 3500,
            "max": 3700,
            "spread": 200,
        }
        assert [sample["report"] for sample in repeat["samples"]] == [
            "brandguard.json",
            "brandguard-sample-02.json",
            "brandguard-sample-03.json",
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
        "/console-404/", "/timeout/"
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
    shutil.copy2(ROOT / "scripts/responsive-qa.mjs", fixture_root / "scripts/responsive-qa.mjs")
    sitemap = (
        '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + "".join(f"<url><loc>https://askjamie.bot{path}</loc></url>" for path in (
            "/lazy/", "/lazy-unobserved/", "/lazy-late/", "/clean/", "/abort/",
            "/console-404/", "/timeout/"
        ))
        + "</urlset>"
    )
    (fixture_root / "sitemap.xml").write_text(sitemap, encoding="utf-8")

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
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)

    report = json.loads(
        (fixture_root / "assets/audit/responsive-qa/results.json").read_text(
            encoding="utf-8"
        )
    )
    rows = {
        path: [row for row in report["results"] if path in row["url"]]
        for path in (
            "/lazy/", "/lazy-unobserved/", "/lazy-late/", "/clean/", "/abort/",
            "/console-404/", "/timeout/"
        )
    }

    assert result.returncode == 1
    assert report["mode"] == "playwright"
    assert report["total_checks"] == 56
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
    assert not any(
        event[1] == "/unobserved-lazy.png" for event in events
    )
    assert all(row["pass"] for row in rows["/lazy-late/"])
    assert all(
        any(
            "lazy image request observed too late" in warning
            and "expected within 5000ms" in warning
            and row["url"] in warning
            and "late-lazy.png" in warning
            for warning in row["warnings"]
        )
        for row in rows["/lazy-late/"]
    )
    late_lazy_starts = [
        event for event in events
        if event[0] == "start" and event[1] == "/late-lazy.png"
    ]
    assert len(late_lazy_starts) == len(rows["/lazy-late/"]) == 8
    assert all(row["pass"] for row in rows["/clean/"])
    assert all(any("REQUEST FAILED" in error or "BROKEN IMG" in error for error in row["errors"])
               for row in rows["/abort/"])
    assert all(any("CONSOLE:" in error for error in row["errors"]) and
               any("HTTP 404" in error for error in row["errors"])
               for row in rows["/console-404/"])
    assert all(any("BROKEN IMG" in error or "REQUEST FAILED" in error for error in row["errors"])
               for row in rows["/timeout/"])
    assert state["page_max_active"] <= 4
    lazy_starts = [event for event in events if event[0] == "start" and event[1] == "/slow-lazy.png"]
    assert len(lazy_starts) == len(rows["/lazy/"]) == 8


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
