#!/usr/bin/env python3
"""Inventory declared/locked technologies and query publisher release metadata.

Standard library only. Never installs packages, changes pins, or writes to
GitHub. --check is an offline manifest contract check suitable for pull requests.
The online report exits 2 for incomplete evidence, or 1 with --fail-on-outdated
when a newer release exists. Neither condition means an upgrade is compatible.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import gzip
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tomllib
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
STABLE = re.compile(r"v?(\d+)\.(\d+)\.(\d+)$")
ACTION = re.compile(r"uses:\s*([\w.-]+/[\w.-]+)@([\w.-]+)")


def version(value):
    match = STABLE.fullmatch(value)
    if not match:
        raise ValueError(f"Not an exact stable release: {value}")
    return tuple(map(int, match.groups()))


def compare(current, latest):
    try:
        left, right = version(current), version(latest)
    except ValueError:
        return "unresolved"
    return "outdated" if left < right else "ahead" if left > right else "current"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def python_pins(root):
    pins = {}
    for line in (root / "requirements-qa.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==(\d+\.\d+\.\d+)", line)
        if not match:
            raise ValueError(f"QA requirement must have an exact stable pin: {line}")
        pins[match[1]] = match[2]
    return pins


def contracts(root):
    """Check the files consumed by CI, without hard-coding old release numbers."""
    errors = []
    package = read_json(root / "package.json")
    lock = read_json(root / "package-lock.json")
    node = (root / ".node-version").read_text().strip()
    version(node)
    if package.get("engines", {}).get("node") != node:
        errors.append("package.json engines.node differs from .node-version")
    if lock["packages"][""].get("engines") != package.get("engines"):
        errors.append("package-lock.json root engines differ from package.json")
    replit = tomllib.loads((root / ".replit").read_text(encoding="utf-8"))
    modules = replit.get("modules", [])
    if f"nodejs-{version(node)[0]}" not in modules:
        errors.append("Replit Node module differs from .node-version major")
    for section in ("dependencies", "devDependencies"):
        declared = package.get(section, {})
        if lock["packages"][""].get(section, {}) != declared:
            errors.append(f"Lockfile root {section} differs from package.json")
        for name, pin in declared.items():
            version(pin)
            resolved = lock["packages"].get(f"node_modules/{name}", {}).get("version")
            if pin != resolved:
                errors.append(f"{name}: declared {pin}, locked {resolved}")
    pins = python_pins(root)
    if not {"beautifulsoup4", "pytest"} <= pins.keys():
        errors.append("requirements-qa.txt must pin Beautiful Soup and pytest")
    ci = (root / ".github/workflows/validate.yml").read_text(encoding="utf-8")
    if "python3 -m pip install -r requirements-qa.txt" not in ci:
        errors.append("Site Validation does not consume requirements-qa.txt")
    python_branches = set(re.findall(r"python-version:\s*['\"](\d+\.\d+)['\"]",
        "\n".join(p.read_text(encoding="utf-8") for p in (root / ".github/workflows").glob("*.y*ml"))))
    if not python_branches or any(f"python-{branch}" not in modules for branch in python_branches):
        errors.append("CI Python selector differs from the Replit Python module")
    version((root / "assets/vendor/mermaid/VERSION").read_text().strip())
    return errors


def fetch(url, as_json=True):
    headers = {"User-Agent": "AskJamie-technology-audit", "Accept": "application/json"}
    # Credentials only go to GitHub's API, never to package registries.
    token = os.environ.get("GITHUB_TOKEN")
    if token and urlparse(url).hostname == "api.github.com":
        headers["Authorization"] = f"Bearer {token}"
    with urlopen(Request(url, headers=headers), timeout=25) as response:
        raw = response.read()
        if raw.startswith(b"\x1f\x8b"):
            raw = gzip.decompress(raw)
        data = raw.decode("utf-8")
    return json.loads(data) if as_json else data


def observe(command):
    executable = shutil.which(command[0])
    if executable is None:
        return "not installed"
    try:
        result = subprocess.run([executable, *command[1:]], capture_output=True,
                                text=True, timeout=15, check=True)
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unavailable"


def local_observations(root, pins, package):
    installed = {}
    for name in package.get("devDependencies", {}):
        path = root / "node_modules" / name / "package.json"
        installed[name] = read_json(path)["version"] if path.exists() else "not installed"
    # Capture the installed dependency closure, not unrelated workstation packages.
    python = {}
    pending = list(pins) + ["pip"]
    seen = set()
    while pending:
        name = pending.pop()
        key = re.sub(r"[-_.]+", "-", name).lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            dist = importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            if name in pins or name == "pip":
                python[key] = "not installed"
            continue
        python[key] = dist.version
        for requirement in dist.requires or []:
            # Optional extras are not evidence that a package is in use.
            if re.search(r"\bextra\s*[=!]=", requirement):
                continue
            match = re.match(r"[A-Za-z0-9_.-]+", requirement)
            if match:
                pending.append(match[0])
    return {
        "node": observe(["node", "--version"]),
        "npm": observe(["npm", "--version"]),
        "python": sys.version.split()[0],
        "git": observe(["git", "--version"]),
        "npm_installed_direct": installed,
        "python_installed_closure": dict(sorted(python.items())),
        "scope": "This process and local installations only; not CI, Replit, or live deployment.",
    }


def inventory(root):
    package = read_json(root / "package.json")
    lock = read_json(root / "package-lock.json")
    pins = python_pins(root)
    direct = {**package.get("dependencies", {}), **package.get("devDependencies", {})}
    rows = []
    for path, entry in sorted(lock["packages"].items()):
        if not path:
            continue
        name = path.split("node_modules/")[-1]
        rows.append({"kind": "npm", "name": name, "current": entry["version"],
                     "declared": direct.get(name) if path == f"node_modules/{name}" else None,
                     "evidence": f"package-lock.json: {path}", "path": path})
    for name, pin in pins.items():
        rows.append({"kind": "pypi", "name": name, "current": pin,
                     "evidence": "requirements-qa.txt", "declared": pin})
    rows.append({"kind": "npm", "name": "mermaid", "declared": "vendored",
                 "current": (root / "assets/vendor/mermaid/VERSION").read_text().strip(),
                 "evidence": "assets/vendor/mermaid/VERSION; validate-site.py checks bundle agreement"})
    actions = {}
    for path in sorted((root / ".github/workflows").glob("*.y*ml")):
        for name, ref in ACTION.findall(path.read_text(encoding="utf-8")):
            actions.setdefault((name, ref), set()).add(path.relative_to(root).as_posix())
    for (name, ref), files in sorted(actions.items()):
        rows.append({"kind": "action", "name": name, "current": ref,
                     "evidence": ", ".join(sorted(files))})
    return rows, local_observations(root, pins, package)


def latest_package(key):
    kind, name = key
    url = (f"https://registry.npmjs.org/{quote(name, safe='')}/latest" if kind == "npm"
           else f"https://pypi.org/pypi/{quote(name, safe='')}/json")
    try:
        data = fetch(url)
        info = data if kind == "npm" else data["info"]
        latest = info["version"]
        note = None
        if kind == "npm":
            if not STABLE.fullmatch(latest):
                tagged_latest = latest
                source = f"https://registry.npmjs.org/{quote(name, safe='')}"
                url = source
                metadata = fetch(url)
                stable = [v for v in metadata.get("versions", {}) if STABLE.fullmatch(v)]
                if not stable:
                    raise ValueError(f"No exact stable release; latest tag is {tagged_latest}")
                latest = max(stable, key=version)
                info = metadata["versions"][latest]
                note = (f"latest tag is {tagged_latest}; showing highest published exact stable "
                        "version instead. This may be an older release line, not a compatible upgrade.")
        elif not re.fullmatch(r"\d+(?:\.\d+)*(?:\.post\d+)?", latest):
            raise ValueError(f"Not a stable Python release: {latest}")
        if kind == "pypi" and info.get("yanked"):
            raise ValueError("Latest release is yanked")
        result = {"latest": latest, "source": url,
                  "requires": info.get("engines", info.get("requires_python"))}
        if note:
            result["release_note"] = note
            result["tagged_latest"] = tagged_latest
        return key, result
    except Exception as exc:
        return key, {"latest": None, "source": url, "error": str(exc)}


def action_releases(name):
    source = f"https://api.github.com/repos/{name}/releases/latest"
    latest = fetch(source)
    if latest.get("draft") or latest.get("prerelease"):
        raise ValueError("Action latest release is not stable")
    version(latest["tag_name"])
    # Bounded tag lookup. Unresolved old SHAs are explicitly unknown, never current.
    tags = fetch(f"https://api.github.com/repos/{name}/tags?per_page=100")
    return latest["tag_name"], tags, source


def resolve_action(ref, tags):
    if STABLE.fullmatch(ref):
        return ref
    sha = next((t["commit"]["sha"] for t in tags if t["name"] == ref), ref)
    matches = [t["name"] for t in tags if t["commit"]["sha"] == sha and STABLE.fullmatch(t["name"])]
    if matches:
        return max(matches, key=version)
    # Floating major tags are not exact versions. Keep that fact in the report.
    return None


def runtime_releases(root):
    rows = []
    source = "https://nodejs.org/dist/index.json"
    pin = (root / ".node-version").read_text().strip()
    try:
        releases = [r for r in fetch(source) if STABLE.fullmatch(r["version"])]
        latest = max(releases, key=lambda r: version(r["version"]))
        lts = max((r for r in releases if r["lts"]), key=lambda r: version(r["version"]))
        same = max((r for r in releases if version(r["version"])[0] == version(pin)[0]),
                   key=lambda r: version(r["version"]))
        rows.append({"name": "Node.js", "current": pin, "latest": latest["version"],
                     "latest_lts": lts["version"], "latest_same_major": same["version"],
                     "npm_bundled_with_pin": next(r["npm"] for r in releases if r["version"] == f"v{pin}"),
                     "status": compare(pin, lts["version"]), "policy": "latest LTS",
                     "source": source})
    except Exception as exc:
        rows.append({"name": "Node.js", "current": pin, "latest": None, "source": source, "error": str(exc)})
    source = "https://www.python.org/downloads/"
    branches = sorted(set(re.findall(r"python-version:\s*['\"](\d+\.\d+)['\"]",
        "\n".join(p.read_text(encoding="utf-8") for p in (root / ".github/workflows").glob("*.y*ml")))))
    try:
        # Require a complete release link label; do not mistake 3.15.0rc1 for 3.15.0.
        releases = re.findall(r">Python (\d+\.\d+\.\d+)</a>", fetch(source, as_json=False))
        latest = max(releases, key=version)
        for branch in branches:
            same = max((v for v in releases if v.startswith(branch + ".")), key=version)
            rows.append({"name": "Python", "current": branch + " (floating patch)",
                         "latest": latest, "latest_same_minor": same,
                         "status": "outdated" if version(branch + ".0")[:2] < version(latest)[:2] else "floating",
                         "source": source})
    except Exception as exc:
        rows.append({"name": "Python", "current": ", ".join(branches), "latest": None, "source": source, "error": str(exc)})
    return rows


def online_report(root):
    rows, observations = inventory(root)
    keys = {(r["kind"], r["name"]) for r in rows if r["kind"] != "action"}
    keys.add(("npm", "npm"))
    keys.update(("pypi", n) for n in observations["python_installed_closure"])
    with ThreadPoolExecutor(max_workers=8) as pool:
        releases = dict(pool.map(latest_package, sorted(keys)))
    actions = {}
    for name in sorted({r["name"] for r in rows if r["kind"] == "action"}):
        try:
            actions[name] = action_releases(name)
        except Exception as exc:
            actions[name] = str(exc)
    for row in rows:
        if row["kind"] != "action":
            row.update(releases[(row["kind"], row["name"])])
            row["status"] = compare(row["current"], row["latest"]) if row["latest"] else "unknown"
        elif isinstance(actions[row["name"]], str):
            row.update(latest=None, status="unknown", error=actions[row["name"]],
                       source=f"https://github.com/{row['name']}/releases")
        else:
            latest, tags, source = actions[row["name"]]
            resolved = resolve_action(row["current"], tags)
            row.update(latest=latest, source=source, resolved=resolved)
            if resolved:
                row["status"] = compare(resolved, latest)
            elif re.fullmatch(r"v\d+", row["current"]):
                row["status"] = "outdated" if int(row["current"][1:]) < version(latest)[0] else "floating"
            else:
                row["status"] = "unknown"
                row["error"] = "Pinned SHA not resolved by the first 100 publisher tags"
    local = []
    for name, current in observations["python_installed_closure"].items():
        local.append({"name": name, "current": current, **releases[("pypi", name)]})
    npm = {"name": "npm", "current": observations["npm"], **releases[("npm", "npm")]}
    return {"schema_version": 1, "checked_at": datetime.now(timezone.utc).isoformat(),
            "source_commit": observe(["git", "-C", str(root), "rev-parse", "HEAD"]),
            "working_tree": "dirty" if observe(["git", "-C", str(root), "status", "--short"]) else "clean",
            "scope": "Repository manifests, every npm lockfile path, workflow actions, runtimes, local QA closure. See technology-review.md for standards, services, and optional tools.",
            "contracts": contracts(root), "runtimes": runtime_releases(root),
            "dependencies": rows, "local_observations": observations,
            "local_python_packages": local, "npm_tool": npm}


def exit_status(report, fail_on_outdated=False):
    rows = report["dependencies"] + report["runtimes"] + report["local_python_packages"] + [report["npm_tool"]]
    if report["contracts"] or any(r.get("error") or r.get("latest") is None for r in rows):
        return 2
    actionable = [r for r in report["dependencies"] if r.get("declared") or r["kind"] == "action"] + report["runtimes"]
    if fail_on_outdated and any(r.get("status") == "outdated" for r in actionable):
        return 1
    return 0


def markdown(report):
    def cell(value):
        return str(value if value is not None else "unknown").replace("|", "\\|").replace("\n", " ")
    lines = ["# Technology version snapshot", "", f"Checked (UTC): {report['checked_at']}",
             f"Source commit: `{report['source_commit']}` plus the recorded working tree in the JSON report.", "",
             "Generated by `scripts/technology-audit.py`. Latest means publisher stable channel, not tested compatibility.",
             "Unknown/floating versions are not exact installed versions. No versions were changed by this audit.",
             "Node status follows latest LTS; its newest Current release is reported separately. Transitive drift is informational.", "",
             "## Runtimes", "", "| Technology | Configured | Latest stable | Supported-line candidate | Status | Source |",
             "| --- | --- | --- | --- | --- | --- |"]
    for row in report["runtimes"]:
        candidate = row.get("latest_lts", row.get("latest_same_minor", "unknown"))
        lines.append(f"| {cell(row['name'])} | {cell(row['current'])} | {cell(row['latest'])} | {candidate} | {row.get('status', 'unknown')} | [Publisher]({row['source']}) |")
    lines.extend(["", "## Direct packages and vendored runtime", "", "| Package | In-place pin | Latest stable | Status | Source |", "| --- | --- | --- | --- | --- |"])
    for row in report["dependencies"]:
        if row.get("declared"):
            lines.append(f"| {row['name']} | {row['current']} | {cell(row['latest'])} | {row['status']} | [Publisher]({row['source']}) |")
    lines.extend(["", "## GitHub Actions", "", "| Action | In-place ref | Resolved release | Latest stable | Status | Source |", "| --- | --- | --- | --- | --- | --- |"])
    for row in report["dependencies"]:
        if row["kind"] == "action":
            lines.append(f"| {row['name']} | {row['current']} | {cell(row.get('resolved'))} | {cell(row['latest'])} | {row['status']} | [Publisher]({row['source']}) |")
    lines.extend(["", "## Every npm lockfile entry", "", "Nested copies remain separate. Transitive versions must update through compatible parent packages.", "", "| Lockfile path | Locked | Latest stable | Status | Source |", "| --- | --- | --- | --- | --- |"])
    for row in report["dependencies"]:
        if row.get("path"):
            lines.append(f"| {row['path']} | {row['current']} | {cell(row['latest'])} | {row['status']} | [Registry]({row['source']}) |")
    lines.extend(["", "## Local observations", "", "These are workstation observations, not CI or Replit evidence.", "", "```json", json.dumps(report["local_observations"], indent=2), "```", "",
                  "| Local QA package/tool | Installed | Latest stable | Source |", "| --- | --- | --- | --- |"])
    for row in report["local_python_packages"] + [report["npm_tool"]]:
        lines.append(f"| {row['name']} | {cell(row['current'])} | {cell(row['latest'])} | [Publisher]({row['source']}) |")
    errors = report["contracts"] + [f"{r['name']}: {r['error']}" for r in report["dependencies"] + report["runtimes"] + report["local_python_packages"] + [report["npm_tool"]] if r.get("error")]
    lines.extend(["", "## Evidence gaps", ""] + ([f"- {cell(e)}" for e in errors] or ["No release lookup or manifest-contract failures."]))
    notes = {(r["name"], r["release_note"]) for r in report["dependencies"] + [report["npm_tool"]] if r.get("release_note")}
    if notes:
        lines.extend(["", "## Publisher channel distinctions", ""] + [f"- {cell(name)}: {cell(note)}" for name, note in sorted(notes)])
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / ".scratch/technology-audit")
    parser.add_argument("--fail-on-outdated", action="store_true")
    args = parser.parse_args()
    try:
        if args.check:
            errors = contracts(ROOT)
            print("\n".join(errors) if errors else "Dependency manifest contracts: PASS")
            return 1 if errors else 0
        report = online_report(ROOT)
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "technology-versions.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        (args.output / "technology-versions.md").write_text(markdown(report), encoding="utf-8")
        status = exit_status(report, args.fail_on_outdated)
        print(f"Report: {args.output / 'technology-versions.md'}; exit={status}")
        return status
    except (OSError, ValueError, KeyError) as exc:
        print(f"Technology audit incomplete: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
