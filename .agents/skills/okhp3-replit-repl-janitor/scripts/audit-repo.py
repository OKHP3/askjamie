#!/usr/bin/env python3
"""Read-only-by-default audit for one Replit Git checkout.

Reports local branch facts, naming violations, and nested detritus folders as
JSON. The script never deletes, renames, prunes, merges, or force-pushes.
Network fetch is opt-in with --fetch and still never prunes. Its pre-delete
check only emits deletion commands after the reviewed branch tip matches the
freshly read tip.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable
from urllib.parse import quote, urlparse


ROOT_GOVERNANCE_FILES = {
    "README.md", "LICENSE", "CHANGELOG.md", "CONTRIBUTING.md",
    "CODE_OF_CONDUCT.md", "SECURITY.md", "AGENTS.md", "CLAUDE.md",
    "SKILL.md", "ROADMAP.md", "NOTICE",
}
TOOL_REQUIRED_PATTERNS = re.compile(
    r"^(package(-lock)?\.json|pnpm-lock\.yaml|pnpm-workspace\.yaml|"
    r"tsconfig.*\.json|vite\.config\.\w+|\.gitignore|\.replit|"
    r"\.replitignore|\.npmrc|\.prettierrc.*|Makefile|CNAME|Dockerfile|"
    r"\.env.*|Pipfile.*|requirements.*\.txt|go\.(mod|sum)|Gemfile.*|"
    r"Cargo\.(toml|lock))$"
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
IGNORED_DIRS = {
    ".git", "node_modules", ".cache", ".local", ".config", ".pythonlibs",
    ".upm", "dist", "build", ".next", ".vite", "__pycache__",
}
KEBAB_OK = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
REPLIT_BRANCH_PATTERNS = re.compile(r"^(subrepl-|replit-agent$|agent/)")


class AuditError(RuntimeError):
    """A Git or repository precondition failed."""


def run(args: list[str], cwd: Path) -> str:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if result.returncode:
        command = " ".join(args)
        detail = result.stderr.strip() or result.stdout.strip() or "no output"
        raise AuditError(f"`{command}` failed ({result.returncode}): {detail}")
    return result.stdout.strip()


def ensure_repository(root: Path) -> None:
    if not root.is_dir():
        raise AuditError(f"repository root does not exist: {root}")
    inside = run(["git", "rev-parse", "--is-inside-work-tree"], root)
    if inside != "true":
        raise AuditError(f"not inside a Git work tree: {root}")


def ensure_base(root: Path, base: str) -> None:
    run(["git", "rev-parse", "--verify", f"{base}^{{commit}}"], root)


def prepare_branch_deletion(
    root: Path,
    branch: str,
    reviewed_head: str,
    *,
    remote: str = "origin",
) -> dict[str, object]:
    """Refresh a branch tip and prepare, but never execute, its deletion.

    The reviewed SHA is the approval boundary.  A changed tip produces a
    review hold with no deletion commands; a missing branch or other Git
    failure raises visibly.  When the tip matches, the returned commands
    preserve the required remote-first order.
    """
    if not branch:
        raise AuditError("branch is required for the pre-delete check")
    if not reviewed_head:
        raise AuditError("reviewed branch head is required for the pre-delete check")

    current_head = run(
        ["git", "rev-parse", "--verify", f"refs/heads/{branch}^{{commit}}"],
        root,
    )
    result: dict[str, object] = {
        "branch": branch,
        "reviewed_head": reviewed_head,
        "current_head": current_head,
    }
    if current_head != reviewed_head:
        result.update({
            "bucket": "review",
            "reason": "branch tip changed since review",
            "deletion_commands": [],
        })
        return result

    result.update({
        "bucket": "delete",
        "reason": "branch tip matches reviewed head",
        "deletion_commands": [
            ["git", "push", remote, "--delete", branch],
            ["git", "branch", "-d", branch],
        ],
    })
    return result


def audit_branches(root: Path, base: str) -> tuple[list[dict[str, object]], str]:
    current = run(["git", "branch", "--show-current"], root)
    branches = run(
        ["git", "for-each-ref", "--format=%(refname:short)", "refs/heads/"],
        root,
    ).splitlines()
    merged = set(
        run(
            ["git", "branch", "--merged", base, "--format=%(refname:short)"],
            root,
        ).splitlines()
    )
    ledger: list[dict[str, object]] = []
    for branch in branches:
        if not branch:
            continue
        last = run(
            ["git", "log", "-1", "--format=%ci%x00%an%x00%s", branch],
            root,
        )
        date, author, subject = (last.split("\0", 2) + ["", "", ""])[:3]
        ledger.append({
            "branch": branch,
            "is_current": bool(current) and branch == current,
            "merged_into_base": branch in merged,
            "last_commit_date": date,
            "last_commit_author": author,
            "last_commit_subject": subject,
            "replit_generated_pattern": bool(REPLIT_BRANCH_PATTERNS.match(branch)),
        })
    return ledger, current


def hosted_command(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run a hosted read-only command without allowing interactive auth."""
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    ssh_command = env.get("GIT_SSH_COMMAND", "ssh").strip() or "ssh"
    ssh_command, replaced = re.subn(
        r"(?i)(-o\s*BatchMode)(?:\s+|=)(?:yes|no)\b",
        r"\1=yes",
        ssh_command,
    )
    if not replaced:
        ssh_command = f"{ssh_command} -o BatchMode=yes"
    env["GIT_SSH_COMMAND"] = ssh_command
    return subprocess.run(
        args, cwd=cwd, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, env=env,
    )


def parse_hosted_branch(value: str) -> tuple[str, str]:
    """Parse the exact provider/ref pair accepted by the hosted audit."""
    separator = "=" if "=" in value else ":"
    if separator not in value:
        raise AuditError("hosted branch must use PROVIDER=BRANCH (or PROVIDER:BRANCH)")
    provider, branch = value.split(separator, 1)
    if not provider or not branch:
        raise AuditError("hosted branch must include both a provider and an exact branch")
    branch = branch.removeprefix("refs/heads/")
    if (
        not branch or branch.startswith("/") or branch.endswith("/")
        or "\x00" in branch or any(character.isspace() for character in branch)
    ):
        raise AuditError(f"invalid hosted branch name: {branch!r}")
    return provider, branch


def remote_url_for_provider(root: Path, provider: str) -> tuple[str, str | None]:
    """Resolve a configured remote, or accept a URL as an explicit provider."""
    if "://" in provider or provider.startswith("git@"):
        return provider, provider
    result = subprocess.run(
        ["git", "remote", "get-url", provider],
        cwd=root, capture_output=True, text=True,
    )
    if result.returncode:
        return provider, None
    return provider, result.stdout.strip() or None


def github_repository(remote_url: str | None) -> tuple[str, str] | None:
    """Extract owner/repository from common GitHub remote URL forms."""
    if not remote_url:
        return None
    if remote_url.startswith("git@github.com:"):
        path = remote_url.split(":", 1)[1]
    else:
        parsed = urlparse(remote_url)
        if parsed.hostname != "github.com":
            return None
        path = parsed.path.lstrip("/")
    parts = path.removesuffix(".git").strip("/").split("/")
    if len(parts) != 2 or not all(parts):
        return None
    return parts[0], parts[1]


def unknown_hosted_evidence(reason: str) -> dict[str, object]:
    return {"status": "unknown", "reason": reason}


def gh_api_json(root: Path, endpoint: str) -> tuple[object | None, str | None]:
    """Read one GitHub API endpoint, returning an explicit failure reason."""
    if shutil.which("gh") is None:
        return None, "GitHub CLI (`gh`) is not installed"
    result = hosted_command(["gh", "api", endpoint], root)
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or "no output"
        return None, f"GitHub API request failed ({result.returncode}): {detail}"
    try:
        return json.loads(result.stdout), None
    except json.JSONDecodeError as exc:
        return None, f"GitHub API returned invalid JSON: {exc}"


def github_hosted_evidence(
    root: Path, remote_url: str | None, branch: str
) -> dict[str, object]:
    """Collect protection, deployment, and PR evidence when GitHub is usable."""
    repository = github_repository(remote_url)
    if repository is None:
        reason = "no supported hosted-provider evidence adapter"
        return {
            "protection": unknown_hosted_evidence(reason),
            "deployments": unknown_hosted_evidence(reason),
            "pull_requests": unknown_hosted_evidence(reason),
        }
    owner, repo = repository
    encoded_repo = f"{quote(owner, safe='')}/{quote(repo, safe='')}"
    encoded_branch = quote(branch, safe="")
    branch_data, branch_error = gh_api_json(
        root, f"repos/{encoded_repo}/branches/{encoded_branch}",
    )
    if branch_error:
        protection: dict[str, object] = unknown_hosted_evidence(branch_error)
    elif isinstance(branch_data, dict):
        protected = branch_data.get("protected")
        if isinstance(protected, bool):
            protection = {
                "status": "protected" if protected else "unprotected",
                "source": "github-api", "repository": f"{owner}/{repo}",
                "ref": branch,
            }
        else:
            protection = unknown_hosted_evidence(
                "GitHub branch response did not include protection status"
            )
    else:
        protection = unknown_hosted_evidence("GitHub branch response was not an object")

    deployments_data, deployments_error = gh_api_json(
        root, f"repos/{encoded_repo}/deployments?ref={encoded_branch}&per_page=100",
    )
    if deployments_error:
        deployments: dict[str, object] = unknown_hosted_evidence(deployments_error)
    elif isinstance(deployments_data, list):
        deployments = {
            "status": "available", "source": "github-api",
            "count": len(deployments_data),
            "items": [
                {key: item.get(key) for key in (
                    "id", "sha", "ref", "environment", "created_at", "updated_at",
                ) if key in item}
                for item in deployments_data if isinstance(item, dict)
            ],
        }
    else:
        deployments = unknown_hosted_evidence("GitHub deployments response was not a list")

    head = quote(f"{owner}:{branch}", safe="")
    pull_requests_data, pull_requests_error = gh_api_json(
        root, f"repos/{encoded_repo}/pulls?state=all&head={head}&per_page=100",
    )
    if pull_requests_error:
        pull_requests: dict[str, object] = unknown_hosted_evidence(pull_requests_error)
    elif isinstance(pull_requests_data, list) and len(pull_requests_data) >= 100:
        pull_requests = unknown_hosted_evidence(
            "GitHub pull-request history reached the 100-result limit; additional history may be missing"
        )
    elif isinstance(pull_requests_data, list):
        pull_requests = {
            "status": "available", "source": "github-api",
            "count": len(pull_requests_data),
            "items": [
                {key: item.get(key) for key in (
                    "number", "state", "title", "merged_at", "html_url", "head", "base",
                ) if key in item}
                for item in pull_requests_data if isinstance(item, dict)
            ],
        }
    else:
        pull_requests = unknown_hosted_evidence("GitHub pull-request response was not a list")
    return {
        "protection": protection, "deployments": deployments,
        "pull_requests": pull_requests,
    }


def audit_hosted_branches(root: Path, requested: Iterable[str]) -> dict[str, object]:
    """Audit each exact provider/ref pair without collapsing provider state."""
    entries: list[dict[str, object]] = []
    for value in requested:
        provider, branch = parse_hosted_branch(value)
        remote, remote_url = remote_url_for_provider(root, provider)
        entry: dict[str, object] = {
            "provider": provider, "ref": branch, "full_ref": f"refs/heads/{branch}",
            "remote": remote, "remote_url": remote_url,
        }
        probe = None
        if remote_url is not None:
            probe = hosted_command(
                ["git", "ls-remote", "--heads", remote, entry["full_ref"]], root,
            )
        if probe is None or probe.returncode:
            detail = (
                f"configured remote is not available: {provider}" if probe is None
                else probe.stderr.strip() or probe.stdout.strip() or "no output"
            )
            entry.update({
                "classification": "inaccessible", "ref_status": "unknown",
                "reason": detail, "deletion_blocked": True,
                "blocking_reasons": ["hosted-remote-inaccessible"],
                **{key: unknown_hosted_evidence("hosted remote is inaccessible")
                   for key in ("protection", "deployments", "pull_requests")},
            })
            entries.append(entry)
            continue
        matching_lines = [
            line.split()[0] for line in probe.stdout.splitlines()
            if line.split() and line.split()[-1] == entry["full_ref"]
        ]
        if not matching_lines:
            entry.update({
                "classification": "missing", "ref_status": "missing",
                "reason": "hosted branch ref was not returned by the remote",
                "deletion_blocked": True, "blocking_reasons": ["hosted-ref-missing"],
                **{key: unknown_hosted_evidence("hosted ref is missing")
                   for key in ("protection", "deployments", "pull_requests")},
            })
            entries.append(entry)
            continue
        entry.update({
            "classification": "present", "ref_status": "present",
            "tip": matching_lines[0],
        })
        evidence = github_hosted_evidence(root, remote_url, branch)
        entry.update(evidence)
        blocking_reasons: list[str] = []
        protection = evidence["protection"]
        deployments = evidence["deployments"]
        pull_requests = evidence["pull_requests"]
        assert isinstance(protection, dict)
        assert isinstance(deployments, dict)
        assert isinstance(pull_requests, dict)
        if protection.get("status") == "protected":
            blocking_reasons.append("hosted-ref-protected")
        elif protection.get("status") == "unknown":
            blocking_reasons.append("hosted-protection-unknown")
        if deployments.get("status") != "available":
            blocking_reasons.append("hosted-deployment-evidence-unknown")
        elif deployments.get("count", 0):
            blocking_reasons.append("hosted-ref-has-deployments")
        if pull_requests.get("status") != "available":
            blocking_reasons.append("hosted-pull-request-evidence-unknown")
        else:
            for pull_request in pull_requests.get("items", []):
                if not isinstance(pull_request, dict):
                    continue
                if pull_request.get("state") == "open":
                    blocking_reasons.append("hosted-open-pull-request")
                elif pull_request.get("state") == "closed" and not pull_request.get("merged_at"):
                    blocking_reasons.append("hosted-closed-unmerged-pull-request")
        entry["deletion_blocked"] = bool(blocking_reasons)
        entry["blocking_reasons"] = sorted(set(blocking_reasons))
        entries.append(entry)
    blocking_entries = [
        f"{entry['provider']}:{entry['ref']}" for entry in entries
        if entry["deletion_blocked"]
    ]
    return {
        "requested": True, "entries": entries,
        "deletion_blocked": bool(blocking_entries),
        "blocking_entries": blocking_entries,
        "cleanup_plan": hosted_cleanup_plan(entries),
    }


HOSTED_HOLD_EXPLANATIONS = {
    "hosted-evidence-unknown":
        "The hosted deletion hold has no supporting reason; review the source evidence.",
    "hosted-remote-inaccessible":
        "The hosted remote could not be accessed; check access before reviewing deletion.",
    "hosted-ref-missing":
        "The hosted branch was not found; confirm its location before reviewing deletion.",
    "hosted-ref-protected":
        "The hosted branch is protected; keep it.",
    "hosted-ref-has-deployments":
        "The hosted branch has deployment records; keep it until their use is reviewed.",
    "hosted-open-pull-request":
        "The hosted branch has an open pull request; keep it while that work is pending.",
    "hosted-closed-unmerged-pull-request":
        "The hosted branch has a closed pull request that was not merged; review its work before deletion.",
    "hosted-protection-unknown":
        "Branch protection could not be confirmed; check protection before reviewing deletion.",
    "hosted-deployment-evidence-unknown":
        "Deployment evidence is unavailable; check deployment use before reviewing deletion.",
    "hosted-pull-request-evidence-unknown":
        "Pull-request evidence is unavailable; check pull-request history before reviewing deletion.",
}


def hosted_cleanup_plan(
    entries: Iterable[dict[str, object]],
) -> dict[str, list[dict[str, object]]]:
    """Project hosted holds into stable codes and reviewer-facing explanations."""
    plan: dict[str, list[dict[str, object]]] = {
        "keep": [], "merge": [], "delete": [], "review": [],
    }
    keep_reasons = {
        "hosted-ref-protected", "hosted-ref-has-deployments",
        "hosted-open-pull-request",
    }
    for entry in entries:
        if not entry.get("deletion_blocked"):
            continue
        reasons = sorted({str(reason) for reason in entry.get("blocking_reasons", [])})
        if not reasons:
            reasons = ["hosted-evidence-unknown"]
        bucket = "keep" if keep_reasons.intersection(reasons) else "review"
        explanations = [
            {
                "reason_code": reason,
                "explanation": HOSTED_HOLD_EXPLANATIONS.get(
                    reason, "An unrecognized hosted hold blocks deletion; review the source evidence."
                ),
            }
            for reason in reasons
        ]
        plan[bucket].append({
            "provider": entry["provider"], "ref": entry["ref"],
            "blocking_reasons": reasons,
            "blocking_reason_explanations": explanations,
        })
    for bucket in plan:
        plan[bucket].sort(key=lambda item: (str(item["provider"]), str(item["ref"])))
    return plan


def is_exception(path: Path, root: Path) -> bool:
    name = path.name
    if name in WEB_STANDARD_FILES or TOOL_REQUIRED_PATTERNS.match(name):
        return True
    if path.parent == root and name in ROOT_GOVERNANCE_FILES:
        return True
    if name.startswith("."):
        return True
    if path.suffix.lower() in {".tsx", ".jsx"}:
        return True
    if path.suffix.lower() == ".ts" and re.match(r"^use[A-Z]", path.stem):
        return True
    return False


def iter_visible(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if any(part in IGNORED_DIRS for part in relative.parts):
            continue
        yield path


def naming_reason(path: Path, root: Path) -> str | None:
    if is_exception(path, root):
        return None
    name = path.name
    stem = path.stem
    if " " in name:
        return "contains spaces"
    if path.suffix and path.suffix != path.suffix.lower():
        return "uppercase extension"
    if "_" in stem:
        return "uses underscores instead of hyphens"
    if re.search(r"[A-Z]", stem) and not stem.isupper():
        return "mixed/camel/Pascal case"
    if stem.isupper():
        return None  # avoid treating established all-caps docs as clear violations
    if not KEBAB_OK.fullmatch(stem):
        return "not kebab-case"
    return None


def audit_naming(root: Path) -> list[dict[str, str]]:
    violations: list[dict[str, str]] = []
    for path in iter_visible(root):
        if path.is_dir():
            continue
        reason = naming_reason(path, root)
        if reason:
            violations.append({
                "path": path.relative_to(root).as_posix(),
                "reason": reason,
            })
    return sorted(violations, key=lambda item: item["path"])


def audit_detritus(root: Path) -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    for path in iter_visible(root):
        if not path.is_dir() or path.name not in DETRITUS_FOLDER_NAMES:
            continue
        relative = path.relative_to(root).as_posix()
        tracked = run(["git", "ls-files", "--", relative], root)
        found.append({
            "folder": relative,
            "tracked_file_count": len(tracked.splitlines()) if tracked else 0,
        })
    return sorted(found, key=lambda item: str(item["folder"]))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--base", default="origin/main")
    parser.add_argument(
        "--check-delete",
        action="store_true",
        help="refresh one branch tip and emit a safe deletion plan; never deletes",
    )
    parser.add_argument(
        "--branch",
        help="exact local branch to check with --check-delete",
    )
    parser.add_argument(
        "--reviewed-head",
        help="branch SHA recorded during review with --check-delete",
    )
    parser.add_argument(
        "--remote",
        default="origin",
        help="remote to use in the remote-first deletion plan (default: origin)",
    )
    parser.add_argument(
        "--hosted-branch", "--hosted-ref", dest="hosted_branches",
        action="append", default=[], metavar="PROVIDER=BRANCH",
        help="audit an exact hosted branch through a remote; repeat for each provider/ref",
    )
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="run `git fetch --all` before auditing; never prunes",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve()
    try:
        if args.check_delete and args.hosted_branches:
            raise AuditError(
                "--check-delete cannot be combined with --hosted-branch or --hosted-ref; "
                "review hosted holds separately before preparing deletion"
            )
        ensure_repository(root)
        if args.fetch:
            run(["git", "fetch", "--all"], root)
        if args.check_delete:
            if not args.branch:
                raise AuditError("--check-delete requires --branch")
            if not args.reviewed_head:
                raise AuditError("--check-delete requires --reviewed-head")
            print(json.dumps(
                prepare_branch_deletion(
                    root,
                    args.branch,
                    args.reviewed_head,
                    remote=args.remote,
                ),
                indent=2,
            ))
            return 0
        ensure_base(root, args.base)
        branches, current = audit_branches(root, args.base)
        report = {
            "root": str(root),
            "base": args.base,
            "fetch_performed": args.fetch,
            "current_branch": current or None,
            "detached_head": not bool(current),
            "branches": branches,
            "naming_violations": audit_naming(root),
            "detritus_folders": audit_detritus(root),
        }
        if args.hosted_branches:
            report["hosted_lifecycle"] = audit_hosted_branches(root, args.hosted_branches)
        print(json.dumps(report, indent=2))
        return 0
    except (AuditError, OSError) as exc:
        print(json.dumps({"error": str(exc), "root": str(root)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
