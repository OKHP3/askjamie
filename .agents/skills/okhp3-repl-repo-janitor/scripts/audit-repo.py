#!/usr/bin/env python3
"""
audit-repo.py -- read-only Git branch + decision-ledger + file/folder naming audit for a single
local repository (designed for one-repo-per-Replit-workspace checkouts).

Never mutates the repository. Prints a JSON report to stdout.

Usage:
    python3 audit-repo.py [--root PATH] [--base origin/main]
        [--active-line BRANCH]
    python3 audit-repo.py --check-ledger [--root PATH]
        [--decision-ledger PATH]

What it reports:
  1. Branch ledger: every local branch, its last commit date/author,
     whether it is merged into the base branch, and whether it matches a
     known Replit-generated pattern (subrepl-*, replit-agent, agent/*)
     versus a human-named branch.
  2. Decision-ledger consistency: missing non-current branches, tip-SHA
      drift, and stale decision or exclusion rows.
   3. Archive equivalence: named archive tips compared with the active line
      at commit, tree, and file level, including patch promotion status.
      File moves are reported as deterministic add/delete pairs so both
      paths remain visible in the JSON evidence.
      UTF-8 paths remain JSON strings; paths containing invalid UTF-8 bytes
      use {"encoding": "base64", "data": "..."} with standard Base64 of the
      exact path bytes.
      UTF-8 commit text remains a JSON string; commit text containing invalid
      UTF-8 bytes uses the same Base64 object representation of the exact
      text bytes.
  4. Naming violations: files/folders whose names break the kebab-case
     default (PascalCase, camelCase, spaces, uppercase extensions) outside
     the recognized structural exceptions (React components/hooks, root
     governance files, tool-required filenames).
  5. Known detritus folder names present in the tree (attached_assets,
     tmp, temp, _unused, unused, _drafts, _scratch, _old, and hyphen
     variants), with tracked/untracked/gitignored status for each.

This script only reads; it never deletes, renames, or force-pushes anything.
Treat its output as evidence for a plan, not as an execution instruction.
"""
import argparse
import base64
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

ROOT_GOVERNANCE_FILES = {
    "README.md", "LICENSE", "CHANGELOG.md", "CONTRIBUTING.md",
    "CODE_OF_CONDUCT.md", "SECURITY.md", "AGENTS.md", "CLAUDE.md",
    "SKILL.md", "ROADMAP.md", "NOTICE",
}
TOOL_REQUIRED_PATTERNS = re.compile(
    r"^(package(-lock)?\.json|tsconfig.*\.json|vite\.config\.\w+|"
    r"\.gitignore|\.replit|\.replitignore|\.npmrc|\.prettierrc.*|"
    r"Makefile|CNAME|Dockerfile|\.env.*|Pipfile.*|requirements.*\.txt|"
    r"go\.(mod|sum)|Gemfile.*|Cargo\.(toml|lock))$"
)
WEB_STANDARD_FILES = {
    "humans.txt", "robots.txt", "llms.txt", "404.html", "_headers",
    "favicon.ico", "favicon.svg", "site.webmanifest", "sitemap.xml",
    "manifest.json",
}
DETRITUS_FOLDER_NAMES = {
    "attached_assets", "attached-assets", "_unused", "unused",
    "_drafts", "_scratch", "_old", "tmp", "temp",
}
KEBAB_OK = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
REPLIT_BRANCH_PATTERNS = re.compile(r"^(subrepl-|replit-agent$|agent/)")
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
ACTIVE_DECISION_LEDGER = ".agents/branch-decision-ledger.md"
DATED_DECISION_LEDGER_PATTERN = re.compile(
    r"^branch-decision-ledger-(\d{4}-\d{2}-\d{2})\.md$"
)
DEFAULT_DECISION_LEDGER = "auto"
SUPPORTED_RETENTION_DECISIONS = {"keep", "archive"}
SUPPORTED_RECONCILIATION_DISPOSITIONS = {"reconciled", "superseded"}


def select_decision_ledger(root: Path, requested: str | None = None) -> Path:
    """Select the active ledger, or honor an explicit historical override."""
    if requested and requested != DEFAULT_DECISION_LEDGER:
        path = Path(requested)
        return path if path.is_absolute() else root / path

    stable = root / ACTIVE_DECISION_LEDGER
    if stable.is_file():
        return stable

    candidates: list[tuple[str, Path]] = []
    ledger_dir = root / ".agents"
    for path in ledger_dir.glob("branch-decision-ledger-*.md"):
        match = DATED_DECISION_LEDGER_PATTERN.fullmatch(path.name)
        if match and path.is_file():
            candidates.append((match.group(1), path))
    if not candidates:
        raise ValueError(
            "no active decision ledger found; create "
            f"{ACTIVE_DECISION_LEDGER} or a dated "
            ".agents/branch-decision-ledger-YYYY-MM-DD.md, or pass "
            "--decision-ledger PATH"
        )
    return max(candidates, key=lambda item: (item[0], item[1].name))[1]


def sh(args, cwd):
    return subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, check=False
    ).stdout.strip()


def refresh_remote(root: Path) -> dict[str, object]:
    """Refresh remote-tracking refs without allowing an interactive prompt."""
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    ssh_command = env.get("GIT_SSH_COMMAND", "ssh").strip() or "ssh"
    ssh_command, replaced = re.subn(
        r"(?i)(-o\s+BatchMode)(?:\s+|=)(?:yes|no)",
        r"\1=yes",
        ssh_command,
    )
    if not replaced:
        ssh_command = f"{ssh_command} -o BatchMode=yes"
    env["GIT_SSH_COMMAND"] = ssh_command
    try:
        result = subprocess.run(
            ["git", "fetch", "--all"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            env=env,
            stdin=subprocess.DEVNULL,
        )
    except OSError as exc:
        return {
            "status": "unavailable",
            "classification": "remote-unavailable",
            "non_interactive": True,
            "detail": f"{type(exc).__name__}: {exc}",
        }
    if result.returncode == 0:
        return {
            "status": "ok",
            "classification": "refreshed",
            "non_interactive": True,
        }

    detail = (result.stderr or result.stdout).strip()
    return {
        "status": "unavailable",
        "classification": "remote-unavailable",
        "non_interactive": True,
        "exit_code": result.returncode,
        "detail": detail[:500] or "git fetch --all failed without diagnostic output",
    }


class DecisionLedger(NamedTuple):
    decisions: list[dict[str, str]]
    exclusions: list[str]
    archive_reconciliations: list[dict[str, str]]
    malformed_archive_reconciliation_rows: list[dict[str, object]]
    duplicate_archive_reconciliation_rows: list[dict[str, object]]
    malformed_decision_rows: list[dict[str, object]]
    duplicate_decision_rows: list[dict[str, object]]
    unsupported_decision_labels: list[dict[str, object]]
    malformed_exclusion_entries: list[dict[str, object]]
    duplicate_exclusion_entries: list[dict[str, object]]


def audit_branches(root: Path, base: str, *, refresh: bool = True):
    if refresh:
        refresh_remote(root)
    branches = sh(
        ["git", "for-each-ref", "--format=%(refname:short)", "refs/heads/"],
        root,
    ).splitlines()
    merged = set(
        sh(["git", "branch", "--merged", base, "--format=%(refname:short)"], root)
        .splitlines()
    )
    ledger = []
    for b in branches:
        if not b:
            continue
        last = _git_bytes_result(
            root, "log", "-1", "--encoding=none", "--format=%ci|%an|%s", b
        ).stdout.rstrip(b"\n")
        date, author, subject = (last.split(b"|", 2) + [b"", b"", b""])[:3]
        ledger.append({
            "branch": b,
            "is_current": b == sh(["git", "branch", "--show-current"], root),
            "tip_sha": sh(["git", "rev-parse", b], root),
            "merged_into_base": b in merged,
            "last_commit_date": _decode_commit_text(date),
            "last_commit_author": _decode_commit_text(author),
            "last_commit_subject": _decode_commit_text(subject),
            "replit_generated_pattern": bool(REPLIT_BRANCH_PATTERNS.match(b)),
        })
    return ledger


def _table_cells(line: str) -> list[str]:
    """Return markdown table cells without leading/trailing separators."""
    if not line.lstrip().startswith("|"):
        return []
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def parse_decision_ledger(path: Path) -> DecisionLedger:
    """Parse the decision table and explicit holds from a markdown ledger."""
    if not path.is_file():
        raise ValueError(f"decision ledger does not exist: {path}")

    decisions: list[dict[str, str]] = []
    exclusions: list[str] = []
    archive_reconciliations: list[dict[str, str]] = []
    malformed_archive_reconciliation_rows: list[dict[str, object]] = []
    duplicate_archive_reconciliation_rows: list[dict[str, object]] = []
    seen_reconciliations: dict[tuple[str, str], tuple[int, str]] = {}
    malformed_decision_rows: list[dict[str, object]] = []
    duplicate_decision_rows: list[dict[str, object]] = []
    seen_decisions: dict[str, tuple[int, str]] = {}
    unsupported_decision_labels: list[dict[str, object]] = []
    malformed_exclusion_entries: list[dict[str, object]] = []
    duplicate_exclusion_entries: list[dict[str, object]] = []
    seen_exclusions: dict[str, int] = {}
    section = ""
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        heading = re.match(r"^##\s+(.+?)\s*$", line)
        if heading:
            section = heading.group(1).lower()
            continue

        if section == "branch decisions":
            cells = _table_cells(line)
            if not line.strip():
                continue
            normalized_cells = [cell.lower() for cell in cells]
            if normalized_cells[:3] == ["branch", "decision", "tip sha"]:
                continue
            if cells and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
                continue
            if len(cells) < 3:
                malformed_decision_rows.append({
                    "line": line_number,
                    "content": line,
                    "reason": "decision row has fewer than three cells",
                })
                continue
            branch_match = re.fullmatch(r"`([^`]+)`", cells[0])
            if not branch_match:
                malformed_decision_rows.append({
                    "line": line_number,
                    "content": line,
                    "reason": "branch cell must contain one backticked branch name",
                })
                continue
            if not cells[1].strip():
                malformed_decision_rows.append({
                    "line": line_number,
                    "content": line,
                    "reason": "decision cell is empty",
                })
                continue
            if not cells[2].strip():
                malformed_decision_rows.append({
                    "line": line_number,
                    "content": line,
                    "reason": "tip SHA cell is empty",
                })
                continue
            decision = re.sub(
                r"^\*\*|\*\*$", "", cells[1]
            ).strip().lower()
            branch = branch_match.group(1)
            if branch in seen_decisions:
                first_line, first_content = seen_decisions[branch]
                duplicate_decision_rows.append({
                    "line": line_number,
                    "content": line,
                    "branch": branch,
                    "first_line": first_line,
                    "first_content": first_content,
                    "reason": "duplicate branch decision; retain one unambiguous row per branch",
                })
            else:
                seen_decisions[branch] = (line_number, line)
            # Retain all rows as evidence, but consumers must reject ambiguous keys.
            decisions.append({
                "branch": branch,
                "decision": decision,
                "tip_sha": cells[2].strip("`").lower(),
            })
            if decision not in SUPPORTED_RETENTION_DECISIONS:
                unsupported_decision_labels.append({
                    "line": line_number,
                    "branch": branch,
                    "decision": decision,
                    "supported_decisions": sorted(SUPPORTED_RETENTION_DECISIONS),
                })
        elif section == "archive reconciliation evidence":
            cells = _table_cells(line)
            # This section allows introductory prose, but table-like lines
            # (including a missing leading separator) must not disappear.
            if not cells and "|" not in line:
                continue
            normalized_cells = [cell.lower() for cell in cells]
            if normalized_cells[:5] == [
                "branch",
                "archive tip sha",
                "reviewed active tip sha",
                "disposition",
                "active-line evidence",
            ]:
                continue
            if cells and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
                continue
            reason = ""
            if len(cells) != 6:
                reason = "archive reconciliation row must contain exactly six cells"
            else:
                branch_match = re.fullmatch(r"`([^`]+)`", cells[0])
                disposition = re.sub(
                    r"^\*\*|\*\*$", "", cells[3]
                ).strip().lower()
                if not branch_match:
                    reason = "branch cell must contain one backticked branch name"
                else:
                    for index, label in (
                        (1, "archive tip SHA"),
                        (2, "reviewed active tip SHA"),
                    ):
                        if not re.fullmatch(
                            r"(?:[0-9a-fA-F]{40}|`[0-9a-fA-F]{40}`)",
                            cells[index],
                        ):
                            reason = f"{label} cell must contain one full 40-character commit SHA"
                            break
                    if not reason:
                        if disposition not in SUPPORTED_RECONCILIATION_DISPOSITIONS:
                            reason = "disposition must be reconciled or superseded"
                        elif not cells[4]:
                            reason = "active-line evidence cell is empty"
                        elif not cells[5]:
                            reason = "rationale cell is empty"
            if reason:
                malformed_archive_reconciliation_rows.append({
                    "line": line_number,
                    "content": line,
                    "reason": reason,
                })
                continue
            reconciliation = {
                "branch": branch_match.group(1),
                "tip_sha": cells[1].strip("`").lower(),
                "active_tip_sha": cells[2].strip("`").lower(),
                "disposition": disposition,
                "active_line_evidence": cells[4],
                "rationale": cells[5],
            }
            key = (reconciliation["branch"], reconciliation["tip_sha"])
            if key in seen_reconciliations:
                first_line, first_content = seen_reconciliations[key]
                duplicate_archive_reconciliation_rows.append({
                    "line": line_number,
                    "content": line,
                    "branch": key[0],
                    "tip_sha": key[1],
                    "first_line": first_line,
                    "first_content": first_content,
                    "reason": "duplicate archive reconciliation branch and tip SHA",
                })
                continue
            seen_reconciliations[key] = (line_number, line)
            archive_reconciliations.append(reconciliation)
        elif section == "explicit exclusions and holds":
            if not line.strip():
                continue
            if not line.lstrip().startswith("-"):
                continue
            # A bullet may document several refs, e.g. "`main`, `branch-a`,
            # and `branch-b` — active work".
            match = re.fullmatch(
                r"\s*-\s+"
                r"(?P<branches>`[^`]+`"
                r"(?:\s*(?:,\s*(?:and\s+)?|\band\s+)`[^`]+`)*)"
                r"\s+—\s+(?P<reason>\S.*)",
                line,
            )
            if not match:
                malformed_exclusion_entries.append({
                    "line": line_number,
                    "content": line,
                    "reason": (
                        "exclusion entry must list backticked branch names "
                        "followed by an em-dash explanation"
                    ),
                })
                continue
            branch_names = re.findall(r"`([^`]+)`", match.group("branches"))
            for branch in branch_names:
                if branch in seen_exclusions:
                    duplicate_exclusion_entries.append({
                        "line": line_number,
                        "content": line,
                        "branch": branch,
                        "first_line": seen_exclusions[branch],
                    })
                else:
                    seen_exclusions[branch] = line_number
                exclusions.append(branch)

    if not (
        decisions
        or exclusions
        or malformed_decision_rows
        or malformed_exclusion_entries
        or malformed_archive_reconciliation_rows
    ):
        raise ValueError(f"decision ledger has no branch coverage rows: {path}")
    return DecisionLedger(
        decisions=decisions,
        exclusions=sorted(set(exclusions)),
        archive_reconciliations=archive_reconciliations,
        malformed_archive_reconciliation_rows=malformed_archive_reconciliation_rows,
        duplicate_archive_reconciliation_rows=duplicate_archive_reconciliation_rows,
        malformed_decision_rows=malformed_decision_rows,
        duplicate_decision_rows=duplicate_decision_rows,
        unsupported_decision_labels=unsupported_decision_labels,
        malformed_exclusion_entries=malformed_exclusion_entries,
        duplicate_exclusion_entries=duplicate_exclusion_entries,
    )


def validate_decision_ledger_structure(ledger_path: Path) -> dict[str, object]:
    """Validate ledger syntax without reading Git state or contacting a remote."""
    ledger = parse_decision_ledger(ledger_path)
    findings = (
        ledger.malformed_decision_rows
        or ledger.duplicate_decision_rows
        or ledger.unsupported_decision_labels
        or ledger.malformed_exclusion_entries
        or ledger.duplicate_exclusion_entries
        or ledger.malformed_archive_reconciliation_rows
        or ledger.duplicate_archive_reconciliation_rows
    )
    return {
        "ledger_path": str(ledger_path),
        "decision_row_count": len(ledger.decisions),
        "exclusion_branch_count": len(ledger.exclusions),
        "malformed_decision_rows": ledger.malformed_decision_rows,
        "duplicate_decision_rows": ledger.duplicate_decision_rows,
        "unsupported_decision_labels": ledger.unsupported_decision_labels,
        "malformed_exclusion_entries": ledger.malformed_exclusion_entries,
        "duplicate_exclusion_entries": ledger.duplicate_exclusion_entries,
        "malformed_archive_reconciliation_rows": ledger.malformed_archive_reconciliation_rows,
        "duplicate_archive_reconciliation_rows": ledger.duplicate_archive_reconciliation_rows,
        "ok": not findings,
    }


def audit_decision_ledger(
    root: Path,
    branches: list[dict[str, object]],
    current: str,
    ledger_path: Path,
) -> dict[str, object]:
    """Compare non-current local branches with written ledger coverage."""
    ledger = parse_decision_ledger(ledger_path)
    local_by_name = {
        str(branch["branch"]): branch
        for branch in branches
        if str(branch["branch"]) != current
    }
    ambiguous_branches = {row["branch"] for row in ledger.duplicate_decision_rows}
    decision_by_name = {
        row["branch"]: row for row in ledger.decisions
        if row["branch"] not in ambiguous_branches
    }
    covered = set(decision_by_name) | set(ledger.exclusions)

    missing_branches = sorted(set(local_by_name) - covered)
    tip_sha_drift: list[dict[str, str]] = []
    invalid_tip_sha: list[dict[str, str]] = []
    for branch, row in decision_by_name.items():
        if branch not in local_by_name:
            continue
        expected = row["tip_sha"]
        actual = str(local_by_name[branch]["tip_sha"])
        if not SHA_PATTERN.fullmatch(expected):
            invalid_tip_sha.append({
                "branch": branch,
                "expected_tip_sha": expected,
                "actual_tip_sha": actual,
            })
        elif expected != actual:
            tip_sha_drift.append({
                "branch": branch,
                "expected_tip_sha": expected,
                "actual_tip_sha": actual,
            })

    stale_ledger_rows: list[dict[str, str]] = []
    for row in ledger.decisions:
        if row["branch"] not in local_by_name:
            stale_ledger_rows.append({
                "branch": row["branch"],
                "kind": "decision",
                "tip_sha": row["tip_sha"],
            })
    for branch in ledger.exclusions:
        if branch not in local_by_name and branch != current:
            stale_ledger_rows.append({
                "branch": branch,
                "kind": "exclusion",
            })

    return {
        "ledger_path": str(ledger_path),
        "covered_branch_count": len(covered),
        "decision_row_count": len(ledger.decisions),
        "exclusion_branch_count": len(ledger.exclusions),
        "missing_branches": missing_branches,
        "tip_sha_drift": sorted(tip_sha_drift, key=lambda item: item["branch"]),
        "invalid_tip_sha": sorted(invalid_tip_sha, key=lambda item: item["branch"]),
        "stale_ledger_rows": sorted(
            stale_ledger_rows, key=lambda item: (item["branch"], item["kind"])
        ),
        "malformed_decision_rows": ledger.malformed_decision_rows,
        "duplicate_decision_rows": ledger.duplicate_decision_rows,
        "unsupported_decision_labels": ledger.unsupported_decision_labels,
        "malformed_exclusion_entries": ledger.malformed_exclusion_entries,
        "duplicate_exclusion_entries": ledger.duplicate_exclusion_entries,
        "malformed_archive_reconciliation_rows": ledger.malformed_archive_reconciliation_rows,
        "duplicate_archive_reconciliation_rows": ledger.duplicate_archive_reconciliation_rows,
        "ok": not (
            missing_branches
            or tip_sha_drift
            or invalid_tip_sha
            or stale_ledger_rows
            or ledger.malformed_decision_rows
            or ledger.duplicate_decision_rows
            or ledger.unsupported_decision_labels
            or ledger.malformed_exclusion_entries
            or ledger.duplicate_exclusion_entries
            or ledger.malformed_archive_reconciliation_rows
            or ledger.duplicate_archive_reconciliation_rows
        ),
    }


def _git_result(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run a read-only Git command and retain its status for ref validation."""
    return subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )


def _git_bytes_result(
    root: Path, *args: str
) -> subprocess.CompletedProcess[bytes]:
    """Run a Git command without decoding output that may contain file paths."""
    return subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        check=False,
    )


def _verified_commit(root: Path, ref: str) -> str | None:
    """Return a full commit SHA when ref resolves to a commit, otherwise None."""
    result = _git_result(root, "rev-parse", "--verify", f"{ref}^{{commit}}")
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value if SHA_PATTERN.fullmatch(value) else None


def _decode_commit_text(value: bytes) -> str | dict[str, str]:
    """Keep UTF-8 commit text readable and encode invalid bytes losslessly."""
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        return {
            "encoding": "base64",
            "data": base64.b64encode(value).decode("ascii"),
        }


def _as_bytes(output: str | bytes) -> bytes:
    return (
        output.encode("utf-8", errors="surrogateescape")
        if isinstance(output, str)
        else output
    )


def _parse_commit_lines(
    output: str | bytes, *, patch_status: str | None = None
) -> list[dict[str, object]]:
    commits: list[dict[str, object]] = []
    for line in _as_bytes(output).split(b"\n"):
        sha, separator, subject = line.partition(b"\t")
        if not separator:
            continue
        try:
            sha_text = sha.decode("ascii")
        except UnicodeDecodeError:
            continue
        if not SHA_PATTERN.fullmatch(sha_text):
            continue
        item: dict[str, object] = {
            "sha": sha_text,
            "subject": _decode_commit_text(subject),
        }
        if patch_status is not None:
            item["patch_status"] = patch_status
        commits.append(item)
    return commits


def _parse_cherry_lines(output: str | bytes) -> list[dict[str, object]]:
    commits: list[dict[str, object]] = []
    for line in _as_bytes(output).split(b"\n"):
        match = re.match(rb"^([+-])\s+([0-9a-f]{7,40})\s?(.*)$", line)
        if not match:
            continue
        marker, abbreviated_sha, subject = match.groups()
        status = "already-promoted" if marker == b"-" else "unrepresented"
        commits.append({
            "sha": abbreviated_sha.decode("ascii"),
            "subject": _decode_commit_text(subject),
            "patch_status": status,
        })
    return commits


def _parse_file_differences(
    output: str | bytes,
) -> list[dict[str, object]]:
    """Parse NUL-delimited ``--no-renames`` name-status evidence.

    A move is intentionally retained as separate add/delete records rather
    than inferred as a rename. This preserves both paths exactly as Git
    reported them and avoids making similarity-based classifications part of
    the audit contract. NUL delimiters keep spaces, tabs, and newlines inside
    a path from being mistaken for record boundaries. Valid UTF-8 paths remain
    strings. Invalid UTF-8 paths use a JSON object with ``encoding`` set to
    ``base64`` and ``data`` set to the standard Base64 encoding of the exact
    path bytes.
    """
    differences: list[dict[str, object]] = []
    if not output:
        return differences

    raw_output = (
        output.encode("utf-8", errors="surrogateescape")
        if isinstance(output, str)
        else output
    )
    fields = raw_output.split(b"\0")
    if fields[-1] == b"":
        fields.pop()
    if len(fields) % 2:
        raise ValueError("incomplete NUL-delimited Git name-status evidence")
    for index in range(0, len(fields), 2):
        status, path = fields[index:index + 2]
        if not status or not path:
            raise ValueError("empty status or path in Git name-status evidence")
        try:
            path_value: str | dict[str, str] = path.decode("utf-8")
        except UnicodeDecodeError:
            path_value = {
                "encoding": "base64",
                "data": base64.b64encode(path).decode("ascii"),
            }
        differences.append({
            "status": status.decode("ascii"),
            "path": path_value,
        })
    return differences


def audit_archive_equivalents(
    root: Path,
    ledger_path: Path,
    active_line: str,
) -> dict[str, object]:
    """Compare every ledger archive tip with the active line without mutation."""
    ledger = parse_decision_ledger(ledger_path)
    archive_rows = [
        row for row in ledger.decisions if row["decision"] == "archive"
    ]
    ambiguous_branches = {row["branch"] for row in ledger.duplicate_decision_rows}
    ambiguous_tips = {
        (row["branch"], row["tip_sha"])
        for row in ledger.duplicate_archive_reconciliation_rows
    }
    reconciliation_by_tip = {
        (row["branch"], row["tip_sha"]): row
        for row in ledger.archive_reconciliations
        if row["disposition"] in SUPPORTED_RECONCILIATION_DISPOSITIONS
        and SHA_PATTERN.fullmatch(row["active_tip_sha"])
        and row["active_line_evidence"]
        and row["rationale"]
        and (row["branch"], row["tip_sha"]) not in ambiguous_tips
    }
    active_tip = _verified_commit(root, active_line)
    reports: list[dict[str, object]] = []

    for row in archive_rows:
        branch = row["branch"]
        tip_sha = row["tip_sha"]
        branch_tip = _verified_commit(root, branch)
        report: dict[str, object] = {
            "branch": branch,
            "tip_sha": tip_sha,
            "branch_tip_sha": branch_tip,
            "file_difference_direction": "active-line-to-archive-tip",
        }
        if branch in ambiguous_branches:
            report.update({
                "classification": "unverifiable",
                "error": "duplicate branch decisions cannot authorize archive cleanup",
            })
            reports.append(report)
            continue
        if not SHA_PATTERN.fullmatch(tip_sha):
            report.update({
                "classification": "unverifiable",
                "error": "ledger tip SHA is not a full commit SHA",
            })
            reports.append(report)
            continue
        if branch_tip is not None:
            report["tip_sha_matches_branch"] = branch_tip == tip_sha
        if active_tip is None:
            report.update({
                "classification": "unverifiable",
                "error": f"active line does not resolve to a commit: {active_line}",
            })
            reports.append(report)
            continue
        if _verified_commit(root, tip_sha) is None:
            report.update({
                "classification": "unverifiable",
                "error": f"archive tip does not resolve to a commit: {tip_sha}",
            })
            reports.append(report)
            continue

        cherry = _git_bytes_result(root, "cherry", "-v", active_line, tip_sha)
        archive_commits = _parse_cherry_lines(cherry.stdout)
        active_only = _git_bytes_result(
            root,
            "log",
            "--encoding=none",
            "--format=%H%x09%s",
            f"{tip_sha}..{active_line}",
        )
        archive_only = _git_bytes_result(
            root,
            "log",
            "--encoding=none",
            "--format=%H%x09%s",
            f"{active_line}..{tip_sha}",
        )
        tree_result = _git_result(root, "rev-parse", f"{active_line}^{{tree}}")
        archive_tree_result = _git_result(root, "rev-parse", f"{tip_sha}^{{tree}}")
        active_tree = tree_result.stdout.strip()
        archive_tree = archive_tree_result.stdout.strip()
        # Keep rename detection disabled so a moved file remains an explicit
        # add/delete pair and reviewers can see both the old and new paths.
        file_diff = _git_bytes_result(
            root,
            "diff",
            "--no-renames",
            "--name-status",
            "-z",
            active_line,
            tip_sha,
        )
        comparison_results = {
            "git cherry": cherry,
            "active-only log": active_only,
            "archive-only log": archive_only,
            "active tree": tree_result,
            "archive tree": archive_tree_result,
            "file diff": file_diff,
        }
        failed_comparisons = [
            name
            for name, result in comparison_results.items()
            if result.returncode != 0
        ]
        if failed_comparisons:
            report.update({
                "classification": "unverifiable",
                "error": (
                    "Git comparison failed: " + ", ".join(failed_comparisons)
                ),
            })
            reports.append(report)
            continue
        unrepresented = sum(
            item["patch_status"] == "unrepresented"
            for item in archive_commits
        )
        reconciliation = reconciliation_by_tip.get((branch, tip_sha))
        reconciliation_is_active = False
        if reconciliation:
            active_ancestor = _git_result(
                root,
                "merge-base",
                "--is-ancestor",
                reconciliation["active_tip_sha"],
                active_tip,
            )
            if active_ancestor.returncode > 1:
                report.update({
                    "classification": "unverifiable",
                    "error": "Git comparison failed: reconciliation ancestry check",
                })
                reports.append(report)
                continue
            reconciliation_is_active = active_ancestor.returncode == 0
        if not unrepresented:
            classification = "already-promoted"
        elif reconciliation_is_active:
            classification = "confirmed-supersession"
        else:
            classification = "unrepresented-changes"
        report.update({
            "classification": classification,
            "commit_differences": {
                "archive_commits": archive_commits,
                "archive_only_commits": _parse_commit_lines(archive_only.stdout),
                "active_only_commits": _parse_commit_lines(active_only.stdout),
                "unrepresented_commit_count": unrepresented,
                "promoted_commit_count": sum(
                    item["patch_status"] == "already-promoted"
                    for item in archive_commits
                ),
            },
            "tree_difference": {
                "active_line_tree": active_tree,
                "archive_tip_tree": archive_tree,
                "same": bool(active_tree and active_tree == archive_tree),
            },
            "file_differences": _parse_file_differences(file_diff.stdout),
        })
        if reconciliation_is_active and reconciliation:
            report["reconciliation_evidence"] = {
                "reviewed_active_tip_sha": reconciliation["active_tip_sha"],
                "disposition": reconciliation["disposition"],
                "active_line_evidence": reconciliation["active_line_evidence"],
                "rationale": reconciliation["rationale"],
            }
        reports.append(report)

    already_promoted = [
        report["branch"]
        for report in reports
        if report.get("classification") == "already-promoted"
    ]
    unrepresented = [
        report["branch"]
        for report in reports
        if report.get("classification") == "unrepresented-changes"
    ]
    confirmed_supersession = [
        report["branch"]
        for report in reports
        if report.get("classification") == "confirmed-supersession"
    ]
    unverifiable = [
        report["branch"]
        for report in reports
        if report.get("classification") == "unverifiable"
    ]
    return {
        "ledger_path": str(ledger_path),
        "active_line": active_line,
        "active_line_tip_sha": active_tip,
        "archive_count": len(reports),
        "archives": reports,
        "already_promoted": sorted(already_promoted),
        "confirmed_supersession": sorted(confirmed_supersession),
        "unrepresented_changes": sorted(unrepresented),
        "unverifiable": sorted(unverifiable),
        "duplicate_archive_reconciliation_rows": ledger.duplicate_archive_reconciliation_rows,
        "duplicate_decision_rows": ledger.duplicate_decision_rows,
        "ok": not unrepresented and not unverifiable and not ambiguous_tips and not ambiguous_branches,
    }


def is_exception(name: str, path: Path) -> bool:
    if name in ROOT_GOVERNANCE_FILES or name in WEB_STANDARD_FILES:
        return True
    if TOOL_REQUIRED_PATTERNS.match(name):
        return True
    if name.startswith("."):
        return True  # dotfiles follow their own tool convention
    suffix = path.suffix
    if suffix in (".tsx", ".jsx"):
        return True  # PascalCase component convention
    if suffix == ".ts" and re.match(r"^use[A-Z]", path.stem):
        return True  # camelCase hook convention
    return False


def audit_naming(root: Path):
    ignored_dirs = {
        ".git", "node_modules", ".cache", ".local", ".config", ".pythonlibs",
        ".upm", "dist", "build", ".next", ".vite", "__pycache__",
    }
    violations = []
    for p in root.rglob("*"):
        if any(part in ignored_dirs for part in p.parts):
            continue
        if p.is_dir():
            continue
        name = p.name
        stem = p.stem
        if is_exception(name, p):
            continue
        if " " in name:
            violations.append({"path": str(p.relative_to(root)), "reason": "contains spaces"})
            continue
        if re.search(r"[A-Z]", stem) and not stem.isupper():
            violations.append({"path": str(p.relative_to(root)), "reason": "mixed/camel/Pascal case"})
            continue
        if not KEBAB_OK.match(stem.lower()) and not KEBAB_OK.match(stem):
            pass  # avoid false positives on numeric/versioned names; report only clear cases above
    return violations


def audit_detritus(root: Path):
    found = []
    for name in DETRITUS_FOLDER_NAMES:
        p = root / name
        if p.exists():
            tracked = sh(["git", "ls-files", name], root)
            found.append({
                "folder": name,
                "exists": True,
                "tracked_file_count": len(tracked.splitlines()) if tracked else 0,
            })
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--base", default="origin/main")
    ap.add_argument(
        "--decision-ledger",
        "--ledger",
        dest="decision_ledger",
        default=DEFAULT_DECISION_LEDGER,
        help=(
            "markdown branch-decision ledger to compare with local refs; "
            "default selects the active ledger automatically"
        ),
    )
    ap.add_argument(
        "--active-line",
        default="",
        help=(
            "branch or commit to treat as the active line "
            "(default: current branch)"
        ),
    )
    ap.add_argument(
        "--check-ledger",
        action="store_true",
        help=(
            "validate only ledger structure and supported row syntax; "
            "does not run Git commands or access the network"
        ),
    )
    args = ap.parse_args()
    root = Path(args.root).resolve()

    if not args.check_ledger and not (root / ".git").exists():
        print(json.dumps({"error": f"{root} is not a Git repository root"}))
        return 1

    try:
        ledger_path = select_decision_ledger(root, args.decision_ledger)
        if args.check_ledger:
            report = validate_decision_ledger_structure(ledger_path)
            print(json.dumps(report, indent=2))
            return 0 if bool(report["ok"]) else 1
        remote_refresh = refresh_remote(root)
        branches = audit_branches(root, args.base, refresh=False)
        current = next(
            (
                str(branch["branch"])
                for branch in branches
                if bool(branch["is_current"])
            ),
            "",
        )
        active_line = args.active_line or current
        if not active_line:
            raise ValueError(
                "active line could not be determined; pass --active-line"
            )
        decision_ledger = audit_decision_ledger(
            root, branches, current, ledger_path
        )
        archive_equivalents = audit_archive_equivalents(
            root, ledger_path, active_line
        )
        report = {
            "root": str(root),
            "base": args.base,
            "remote_refresh": remote_refresh,
            "branches": branches,
            "decision_ledger": decision_ledger,
            "archive_equivalents": archive_equivalents,
            "naming_violations": audit_naming(root),
            "detritus_folders": audit_detritus(root),
        }
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc), "root": str(root)}))
        return 1

    print(json.dumps(report, indent=2))
    return 0 if (
        remote_refresh["status"] == "ok"
        and bool(decision_ledger["ok"])
        and bool(archive_equivalents["ok"])
    ) else 1


if __name__ == "__main__":
    sys.exit(main())
