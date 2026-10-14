"""Release detection and CI-contract regressions, with no network dependency."""

import copy
import gzip
import importlib.util
import json
from pathlib import Path
import shutil

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("technology_audit", ROOT / "scripts/technology-audit.py")
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


@pytest.mark.parametrize("current,latest,status", [
    ("1.9.0", "1.10.0", "outdated"),
    ("12.0.0", "11.17.2", "ahead"),
    ("v7.0.1", "7.0.1", "current"),
    ("1.60.0", "1.63.0-beta.1", "unresolved"),
    ("v4", "v7.0.1", "unresolved"),
])
def test_compare_never_uses_lexical_order_or_invents_an_exact_version(current, latest, status):
    assert audit.compare(current, latest) == status


def test_package_fetch_rejects_prerelease_and_yanked_release(monkeypatch):
    monkeypatch.setattr(audit, "fetch", lambda url: {"version": "2.0.0-rc.1"})
    assert audit.latest_package(("npm", "example"))[1]["latest"] is None
    monkeypatch.setattr(audit, "fetch", lambda url: {"info": {"version": "2.0.0", "yanked": True}})
    assert audit.latest_package(("pypi", "example"))[1]["latest"] is None


def test_python_year_based_stable_versions_are_accepted(monkeypatch):
    monkeypatch.setattr(audit, "fetch", lambda url: {"info": {"version": "26.3"}})
    assert audit.latest_package(("pypi", "packaging"))[1]["latest"] == "26.3"


def test_prerelease_latest_tag_retains_channel_and_selects_numeric_stable(monkeypatch):
    def fake_fetch(url):
        if url.endswith("/latest"):
            return {"version": "158.0.8104-0"}
        return {"versions": {"1.9.0": {}, "1.10.0": {"engines": {"node": ">=20"}},
                             "158.0.8104-0": {}}}
    monkeypatch.setattr(audit, "fetch", fake_fetch)
    row = audit.latest_package(("npm", "example"))[1]
    assert row["latest"] == "1.10.0"
    assert row["tagged_latest"] == "158.0.8104-0"
    assert row["source"] == "https://registry.npmjs.org/example"
    assert "older release line" in row["release_note"]
    assert row["requires"] == {"node": ">=20"}


def test_prerelease_fallback_lookup_failure_remains_evidence_gap(monkeypatch):
    def fake_fetch(url):
        if url.endswith("/latest"):
            return {"version": "2.0.0-rc.1"}
        raise OSError("registry unavailable")
    monkeypatch.setattr(audit, "fetch", fake_fetch)
    row = audit.latest_package(("npm", "example"))[1]
    assert row["latest"] is None
    assert "registry unavailable" in row["error"]


def test_fetch_handles_compressed_publisher_response(monkeypatch):
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self):
            return gzip.compress(b'{"version": "1.2.3"}')
    monkeypatch.setattr(audit, "urlopen", lambda *a, **kw: Response())
    assert audit.fetch("https://example.com/") == {"version": "1.2.3"}


def test_actions_resolve_floating_ref_and_sha_without_assuming_latest():
    tags = [{"name": name, "commit": {"sha": sha}} for name, sha in [
        ("v4", "old"), ("v4.3.0", "old"), ("v7.0.1", "new")]]
    assert audit.resolve_action("v4", tags) == "v4.3.0"
    assert audit.resolve_action("new", tags) == "v7.0.1"
    assert audit.resolve_action("missing", tags) is None


@pytest.fixture
def project(tmp_path):
    for name in ["package.json", "package-lock.json", ".node-version", ".replit", "requirements-qa.txt",
                 "assets/vendor/mermaid/VERSION", ".github/workflows/validate.yml"]:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    return tmp_path


def test_manifest_contract_accepts_synchronized_future_upgrade(project):
    package = audit.read_json(project / "package.json")
    lock = audit.read_json(project / "package-lock.json")
    package["devDependencies"]["playwright"] = "99.0.0"
    lock["packages"][""]["devDependencies"]["playwright"] = "99.0.0"
    lock["packages"]["node_modules/playwright"]["version"] = "99.0.0"
    (project / "package.json").write_text(json.dumps(package))
    (project / "package-lock.json").write_text(json.dumps(lock))
    assert audit.contracts(project) == []


def test_manifest_contract_catches_stale_lock_and_bypassed_requirements(project):
    package = audit.read_json(project / "package.json")
    package["devDependencies"]["playwright"] = "99.0.0"
    (project / "package.json").write_text(json.dumps(package))
    workflow = project / ".github/workflows/validate.yml"
    workflow.write_text(workflow.read_text().replace("python3 -m pip install -r requirements-qa.txt", "pip install pytest"))
    errors = audit.contracts(project)
    assert any("locked" in error for error in errors)
    assert any("does not consume" in error for error in errors)


def test_runtime_major_upgrade_requires_matching_replit_configuration(project):
    current_major = int((project / ".node-version").read_text().split(".")[0])
    (project / ".node-version").write_text(f"{current_major + 2}.0.0\n")
    errors = audit.contracts(project)
    assert "Replit Node module differs from .node-version major" in errors


def test_python_latest_excludes_rc_release_and_reports_configured_branch(project, monkeypatch):
    def fake_fetch(url, as_json=True):
        if "nodejs.org" in url:
            return [{"version": "v22.19.0", "lts": "LTS", "npm": "10.9.3"}]
        return '<a>Python 3.14.7</a><a>Python 3.15.0rc1</a><a>Python 3.11.16</a>'
    monkeypatch.setattr(audit, "fetch", fake_fetch)
    rows = audit.runtime_releases(project)
    assert rows[1]["latest"] == "3.14.7"
    assert rows[1]["latest_same_minor"] == "3.11.16"


def test_failed_lookup_overrides_outdated_status():
    report = {"contracts": [], "dependencies": [{"kind": "npm", "declared": "1.0.0", "latest": "2.0.0", "status": "outdated"}],
              "runtimes": [], "local_python_packages": [], "npm_tool": {"latest": "12.0.2"}}
    assert audit.exit_status(report) == 0
    assert audit.exit_status(report, True) == 1
    failed = copy.deepcopy(report)
    failed["runtimes"] = [{"latest": None, "error": "timeout"}]
    assert audit.exit_status(failed, True) == 2


def test_transitive_latest_does_not_fail_update_policy():
    report = {"contracts": [], "dependencies": [{"kind": "npm", "latest": "9.0.0", "status": "outdated"}],
              "runtimes": [], "local_python_packages": [], "npm_tool": {"latest": "12.0.2"}}
    assert audit.exit_status(report, True) == 0


def test_inventory_keeps_nested_locked_versions(project):
    rows, _ = audit.inventory(project)
    lock = audit.read_json(project / "package-lock.json")
    assert len([r for r in rows if r.get("path")]) == len(lock["packages"]) - 1
