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
import hashlib
import importlib.util
import json
import os
import re
import selectors
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import quote, urlparse


def load_helper(name: str):
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).with_name(name + ".py"),
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


recovery_guard = load_helper("recovery-guard")
retirement_ledger = load_helper("retirement-ledger")


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
HOSTED_COMMAND_TIMEOUT_SECONDS = 30
HOSTED_COMMAND_MAX_OUTPUT_BYTES = 4 * 1024 * 1024
HOSTED_HISTORY_MAX_PAGES = 10
# Shared by successful histories across all requested provider/ref pairs.
# Conservative Python-object accounting, not a wire-size or whole-process cap.
HOSTED_HISTORY_MAX_RETAINED_BYTES = 16 * 1024 * 1024


class HostedHistoryBudget:
    """Bound retained JSON trees; count shared objects again rather than undercount."""

    def __init__(self) -> None:
        self.used = 0

    def page_size(self, value: object, pending: int) -> int | None:
        remaining = HOSTED_HISTORY_MAX_RETAINED_BYTES - self.used - pending
        size = 0
        # Iterator stack is depth-bounded, unlike a worklist of every JSON leaf.
        stack = [iter((value,))]
        while stack:
            try:
                node = next(stack[-1])
            except StopIteration:
                stack.pop()
                continue
            size += sys.getsizeof(node)
            if size > remaining:
                return None
            if isinstance(node, dict):
                stack.append(iter(node.values()))
                stack.append(iter(node.keys()))
            elif isinstance(node, list):
                stack.append(iter(node))
        return size


class AuditError(RuntimeError):
    """A Git or repository precondition failed."""


class HostedOutputLimitExceeded(RuntimeError):
    """Hosted output was discarded rather than used as partial evidence."""


def print_json(report: object, *, indent: int | None = None) -> None:
    """Write JSON incrementally, preserving dumps defaults and print's newline."""
    json.dump(report, sys.stdout, indent=indent)
    sys.stdout.write("\n")


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
    """Bound a hosted read-only command and decode its output as strict UTF-8."""
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
    # Drain both pipes concurrently in bounded chunks; communicate() and
    # capture_output=True buffer arbitrarily large responses before returning.
    deadline = time.monotonic() + HOSTED_COMMAND_TIMEOUT_SECONDS
    with subprocess.Popen(
        # POSIX exec accepts bytes: avoid locale-dependent re-encoding of refs.
        [arg.encode("utf-8", errors="strict") for arg in args] if os.name == "posix" else args,
        cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL, env=env, start_new_session=True,
    ) as process:
        try:
            output = [bytearray(), bytearray()]
            total = 0
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ, 0)
                selector.register(process.stderr, selectors.EVENT_READ, 1)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(args, HOSTED_COMMAND_TIMEOUT_SECONDS)
                    for key, _ in selector.select(remaining):
                        chunk = os.read(
                            key.fileobj.fileno(),
                            min(65536, HOSTED_COMMAND_MAX_OUTPUT_BYTES - total + 1),
                        )
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        total += len(chunk)
                        if total > HOSTED_COMMAND_MAX_OUTPUT_BYTES:
                            output.clear()
                            raise HostedOutputLimitExceeded(
                                "hosted command output exceeded "
                                f"{HOSTED_COMMAND_MAX_OUTPUT_BYTES} bytes "
                                "(combined stdout/stderr); output discarded"
                            )
                        output[key.data].extend(chunk)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(args, HOSTED_COMMAND_TIMEOUT_SECONDS)
            try:
                process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                raise subprocess.TimeoutExpired(args, HOSTED_COMMAND_TIMEOUT_SECONDS) from None
            # Hosted Git/GitHub output uses UTF-8 independently of the local
            # locale. Never replace invalid bytes in evidence used for cleanup.
            stdout, stderr = (
                data.decode("utf-8", errors="strict").replace("\r\n", "\n").replace("\r", "\n")
                for data in output
            )
            return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
        except BaseException:
            # Include helper/auth subprocesses which may still hold a pipe open.
            # Never call communicate() here: even discarded output must be bounded.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            raise


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
    try:
        result = hosted_command(["gh", "api", endpoint], root)
    except HostedOutputLimitExceeded as exc:
        return None, f"GitHub API {exc}"
    except UnicodeDecodeError as exc:
        return None, f"GitHub API output could not be decoded: {exc}"
    except subprocess.TimeoutExpired as exc:
        return None, f"GitHub API request timed out after {exc.timeout} seconds"
    except OSError as exc:
        return None, f"GitHub API command could not start: {exc}"
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or "no output"
        return None, f"GitHub API request failed ({result.returncode}): {detail}"
    try:
        return json.loads(result.stdout), None
    except json.JSONDecodeError as exc:
        return None, f"GitHub API returned invalid JSON: {exc}"
    except (MemoryError, RecursionError) as exc:
        return None, (
            f"GitHub API JSON parsing resource failure ({type(exc).__name__}); "
            "output discarded"
        )


def valid_pull_request_classification(record: object) -> bool:
    """Require explicit classification fields; null means unmerged, not missing."""
    if not isinstance(record, dict) or record.get("state") not in ("open", "closed"):
        return False
    if "merged_at" not in record:
        return False
    merged_at = record["merged_at"]
    return merged_at is None or (isinstance(merged_at, str) and bool(merged_at.strip()))


def pull_request_association_error(
    record: dict, repository: str, branch: str,
) -> str | None:
    """Require a verifiable head; the query filter alone is not association proof."""
    head = record.get("head")
    if not isinstance(head, dict):
        return "missing or invalid head association metadata"
    ref = head.get("ref")
    repo = head.get("repo")
    if (
        not isinstance(ref, str) or not ref
        or not isinstance(repo, dict)
        or not isinstance(repo.get("full_name"), str) or not repo["full_name"]
    ):
        return "missing or invalid head association metadata"
    if ref != branch or repo["full_name"].lower() != repository.lower():
        return "head association does not match the requested repository and branch"
    return None


def deployment_association_error(record: dict, branch: str) -> str | None:
    """Require the exact branch ref; a commit alone cannot identify a branch."""
    ref = record.get("ref")
    if not isinstance(ref, str) or not ref.strip():
        return "missing or invalid deployment association metadata"
    if ref != branch:
        return "deployment association does not match the requested branch"
    # Historical deployments need not use today's branch tip. SHA is optional,
    # but a supplied value must be a full object ID, not malformed metadata.
    if "sha" in record and (
        not isinstance(record["sha"], str)
        or re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", record["sha"]) is None
    ):
        return "missing or invalid deployment association metadata"
    return None


def gh_api_list(
    root: Path, endpoint: str, label: str,
    record_validator: Callable[[object], bool] | None = None,
    record_error: Callable[[dict], str | None] | None = None,
    *,
    history_budget: HostedHistoryBudget | None = None,
) -> tuple[list | None, str | None]:
    """Bound retained objects and pages; discard history unless completion is proven."""
    if history_budget is None:
        history_budget = HostedHistoryBudget()
    pending_bytes = 0
    items: list = []
    full_pages: dict[bytes, int] = {}
    for page in range(1, HOSTED_HISTORY_MAX_PAGES + 1):
        data, error = gh_api_json(root, f"{endpoint}&per_page=100&page={page}")
        if error:
            return None, f"GitHub {label} history incomplete at page {page}: {error}"
        if not isinstance(data, list):
            return None, f"GitHub {label} response on page {page} was not a list"
        if len(data) > 100 or any(not isinstance(item, dict) for item in data):
            return None, f"GitHub {label} response on page {page} contained invalid records"
        try:
            page_bytes = history_budget.page_size(data, pending_bytes)
        except (MemoryError, RecursionError) as exc:
            return None, (
                f"GitHub {label} history incomplete at page {page}: "
                f"retained history accounting resource failure ({type(exc).__name__}); "
                "partial history discarded"
            )
        if page_bytes is None:
            return None, (
                f"GitHub {label} history incomplete at page {page}: "
                f"retained history memory budget exhausted "
                f"({HOSTED_HISTORY_MAX_RETAINED_BYTES} bytes); partial history discarded"
            )
        pending_bytes += page_bytes
        if record_validator is not None and any(not record_validator(item) for item in data):
            return None, f"GitHub {label} response on page {page} contained invalid classification fields"
        if record_error is not None:
            for item in data:
                error = record_error(item)
                if error:
                    return None, f"GitHub {label} response on page {page}: {error}"
        if len(data) == 100:
            try:
                digest = hashlib.sha256()
                for chunk in json.JSONEncoder(
                    sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                ).iterencode(data):
                    digest.update(chunk.encode("utf-8", errors="surrogatepass"))
                fingerprint = digest.digest()
            except (MemoryError, RecursionError) as exc:
                return None, (
                    f"GitHub {label} history incomplete at page {page}: "
                    f"page fingerprint resource failure ({type(exc).__name__}); "
                    "partial history discarded"
                )
            if fingerprint in full_pages:
                return None, (
                    f"GitHub {label} history incomplete at page {page}: "
                    f"repeated full page (matches page {full_pages[fingerprint]}); "
                    "no short or empty page confirmed completion"
                )
            full_pages[fingerprint] = page
        items.extend(data)
        if len(data) < 100:
            # Failed histories never reserve capacity needed by later checks.
            history_budget.used += pending_bytes
            return items, None
        data = None  # Do not keep the previous page alive during the next parse.
    return None, (
        f"GitHub {label} history incomplete after page {HOSTED_HISTORY_MAX_PAGES}: "
        f"pagination budget exhausted ({HOSTED_HISTORY_MAX_PAGES} pages); "
        "no short or empty page confirmed completion"
    )


def github_hosted_evidence(
    root: Path, remote_url: str | None, branch: str,
    *, history_budget: HostedHistoryBudget | None = None,
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
    if history_budget is None:
        history_budget = HostedHistoryBudget()
    encoded_repo = f"{quote(owner, safe='')}/{quote(repo, safe='')}"
    encoded_branch = quote(branch, safe="")
    branch_data, branch_error = gh_api_json(
        root, f"repos/{encoded_repo}/branches/{encoded_branch}",
    )
    if branch_error:
        protection: dict[str, object] = unknown_hosted_evidence(branch_error)
    elif isinstance(branch_data, dict):
        protected = branch_data.get("protected")
        # The branch-specific endpoint supplies association when name is absent.
        # A supplied name must corroborate it, never silently contradict it.
        if "name" in branch_data and (
            not isinstance(branch_data["name"], str) or not branch_data["name"].strip()
        ):
            protection = unknown_hosted_evidence(
                "GitHub branch response: missing or invalid branch association metadata"
            )
        elif "name" in branch_data and branch_data["name"] != branch:
            protection = unknown_hosted_evidence(
                "GitHub branch response: branch association does not match the requested branch"
            )
        elif isinstance(protected, bool):
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
    del branch_data

    deployments_data, deployments_error = gh_api_list(
        root, f"repos/{encoded_repo}/deployments?ref={encoded_branch}", "deployments",
        record_error=lambda item: deployment_association_error(item, branch),
        history_budget=history_budget,
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
    del deployments_data

    head = quote(f"{owner}:{branch}", safe="")
    pull_requests_data, pull_requests_error = gh_api_list(
        root, f"repos/{encoded_repo}/pulls?state=all&head={head}", "pull-request",
        record_validator=valid_pull_request_classification,
        record_error=lambda item: pull_request_association_error(item, f"{owner}/{repo}", branch),
        history_budget=history_budget,
    )
    if pull_requests_error:
        pull_requests: dict[str, object] = unknown_hosted_evidence(pull_requests_error)
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
    history_budget = HostedHistoryBudget()
    for value in requested:
        provider, branch = parse_hosted_branch(value)
        remote, remote_url = remote_url_for_provider(root, provider)
        entry: dict[str, object] = {
            "provider": provider, "ref": branch, "full_ref": f"refs/heads/{branch}",
            "remote": remote, "remote_url": remote_url,
        }
        probe = None
        probe_error = None
        if remote_url is not None:
            try:
                probe = hosted_command(
                    ["git", "ls-remote", "--heads", remote, entry["full_ref"]], root,
                )
            except HostedOutputLimitExceeded as exc:
                probe_error = f"hosted remote {exc}"
            except UnicodeDecodeError as exc:
                probe_error = f"hosted remote output could not be decoded: {exc}"
            except subprocess.TimeoutExpired as exc:
                probe_error = f"hosted remote lookup timed out after {exc.timeout} seconds"
            except OSError as exc:
                probe_error = f"hosted remote command could not start: {exc}"
        if probe is None or probe.returncode:
            detail = probe_error or (
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
        evidence = github_hosted_evidence(
            root, remote_url, branch, history_budget=history_budget,
        )
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
            items = pull_requests.get("items")
            if not isinstance(items, list):
                blocking_reasons.append("hosted-pull-request-evidence-unknown")
                items = []
            for pull_request in items:
                if not valid_pull_request_classification(pull_request):
                    blocking_reasons.append("hosted-pull-request-evidence-unknown")
                    continue
                if pull_request["state"] == "open":
                    blocking_reasons.append("hosted-open-pull-request")
                elif pull_request["merged_at"] is None:
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


def hosted_argument(value: str) -> str:
    """Interpret POSIX hosted argv bytes as UTF-8 without Unicode normalization."""
    if os.name != "posix":
        return value
    try:
        return os.fsencode(value).decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise argparse.ArgumentTypeError(
            "hosted provider/branch arguments must be valid UTF-8"
        ) from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--base", default="origin/main")
    recovery_mode = parser.add_mutually_exclusive_group()
    recovery_mode.add_argument("--snapshot-recovery", metavar="PATH")
    recovery_mode.add_argument("--verify-recovery", metavar="PATH")
    recovery_mode.add_argument(
        "--validate-retirement-ledger", metavar="POLICY",
        help="validate committed decision records before removal; never deletes",
    )
    recovery_mode.add_argument(
        "--audit-retirement-history", metavar="BASELINE_POLICY",
        help="read-only retention audit from --ledger-baseline through HEAD",
    )
    parser.add_argument("--ledger-baseline", metavar="COMMIT")
    parser.add_argument(
        "--ledger-baseline-policy", metavar="POLICY",
        help="policy at owner-selected baseline for migration proof in preflight/verification",
    )
    parser.add_argument(
        "--approve-ledger-migration", action="append", default=[],
        metavar="FULL_COMMIT=OLD_POLICY,NEW_POLICY",
    )
    parser.add_argument("--approve-local-deletion", action="append", default=[])
    parser.add_argument(
        "--approve-recovery-retirement", action="append", default=[],
        metavar="REF=EVIDENCE",
    )
    parser.add_argument(
        "--retirement-ledger", metavar="POLICY",
        help="owner-approved committed policy; required for recovery retirement verification",
    )
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
        action="append", default=[], metavar="PROVIDER=BRANCH", type=hosted_argument,
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
        recovery_mode = (
            args.snapshot_recovery or args.verify_recovery or args.validate_retirement_ledger
            or args.audit_retirement_history
        )
        if recovery_mode and (args.fetch or args.check_delete or args.hosted_branches):
            raise AuditError("recovery modes cannot be combined with fetch, branch deletion checks, or hosted probes")
        if args.approve_local_deletion and not args.verify_recovery:
            raise AuditError("--approve-local-deletion requires --verify-recovery")
        if args.approve_recovery_retirement and not (
            args.verify_recovery or args.validate_retirement_ledger
        ):
            raise AuditError("--approve-recovery-retirement requires recovery verification or ledger preflight")
        if args.retirement_ledger and not args.verify_recovery:
            raise AuditError("--retirement-ledger requires --verify-recovery")
        if args.approve_recovery_retirement and args.verify_recovery and not args.retirement_ledger:
            raise AuditError("recovery retirement requires --retirement-ledger")
        migration_preflight = args.validate_retirement_ledger or (
            args.verify_recovery and args.retirement_ledger
        )
        if args.ledger_baseline_policy and not migration_preflight:
            raise AuditError("--ledger-baseline-policy requires ledger preflight or ledger verification")
        if (args.ledger_baseline or args.approve_ledger_migration) and not (
            args.audit_retirement_history or migration_preflight
        ):
            raise AuditError("--ledger-baseline and --approve-ledger-migration require --audit-retirement-history or ledger preflight/verification")
        if args.audit_retirement_history:
            if not args.ledger_baseline:
                raise AuditError("--audit-retirement-history requires --ledger-baseline")
            result = retirement_ledger.audit_history(
                root, args.audit_retirement_history, args.ledger_baseline,
                args.approve_ledger_migration,
            )
            print_json({"retirement_history": result}, indent=2)
            return 0 if result["passed"] else 1
        if args.snapshot_recovery:
            snapshot = recovery_guard.recovery_snapshot(root, run)
            path = Path(args.snapshot_recovery).resolve()
            recovery_guard.write_recovery_snapshot(path, snapshot)
            print_json({"snapshot_file": str(path), "recovery_snapshot": snapshot}, indent=2)
            return 0
        if args.validate_retirement_ledger:
            approvals = [
                recovery_guard.parse_recovery_retirement(value)
                for value in args.approve_recovery_retirement
            ]
            result = retirement_ledger.validate_ledger(
                root, args.validate_retirement_ledger,
                recovery_guard.recovery_snapshot(root, run), approvals,
                baseline=args.ledger_baseline,
                baseline_policy=args.ledger_baseline_policy,
                migrations=args.approve_ledger_migration,
            )
            print_json({"retirement_ledger": result}, indent=2)
            return 0
        if args.verify_recovery:
            before = recovery_guard.read_recovery_snapshot(Path(args.verify_recovery).resolve())
            ledger_result = None
            if args.retirement_ledger:
                ledger_result = retirement_ledger.validate_ledger(
                    root, args.retirement_ledger, before,
                    [recovery_guard.parse_recovery_retirement(value)
                     for value in args.approve_recovery_retirement],
                    baseline=args.ledger_baseline,
                    baseline_policy=args.ledger_baseline_policy,
                    migrations=args.approve_ledger_migration,
                )
            result = recovery_guard.compare_recovery_snapshots(
                before, recovery_guard.recovery_snapshot(root, run),
                args.approve_local_deletion, args.approve_recovery_retirement,
            )
            if ledger_result is not None:
                result["retirement_ledger"] = ledger_result
            print_json({
                "snapshot_file": str(Path(args.verify_recovery).resolve()),
                "recovery_guard": result,
            }, indent=2)
            return 0 if result["passed"] else 1
        if args.fetch:
            run(["git", "fetch", "--all"], root)
        if args.check_delete:
            if not args.branch:
                raise AuditError("--check-delete requires --branch")
            if not args.reviewed_head:
                raise AuditError("--check-delete requires --reviewed-head")
            print_json(
                prepare_branch_deletion(
                    root,
                    args.branch,
                    args.reviewed_head,
                    remote=args.remote,
                ),
                indent=2,
            )
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
        print_json(report, indent=2)
        return 0
    except (AuditError, OSError, recovery_guard.RecoveryError, retirement_ledger.LedgerError) as exc:
        report = {"error": str(exc), "root": str(root)}
        if isinstance(exc, retirement_ledger.RetentionHoldsError):
            report["retention_holds"] = exc.retention_holds
        print_json(report)
        return 1


if __name__ == "__main__":
    sys.exit(main())
