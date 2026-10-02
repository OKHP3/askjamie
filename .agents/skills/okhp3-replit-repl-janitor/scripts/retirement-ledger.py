"""Validate owner-approved, committed JSON retirement records without writing."""

from __future__ import annotations

import json
import re
import subprocess
from datetime import date
from pathlib import Path


class LedgerError(ValueError):
    """A retained retirement decision is not safe to rely on."""


def safe_text(value, field):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise LedgerError(f"retirement ledger: {field} must be non-empty trimmed text")
    # Defense in depth, not a substitute for the owner's redaction review.
    if re.search(
        r"(?i)(?:bearer\s+\S+|gh[pousr]_[a-z0-9]+|github_pat_[a-z0-9_]+|"
        r"-----BEGIN .*PRIVATE KEY|https?://[^\s/]*@|"
        r"(?:token|password|secret|api[_-]?key|signature|credential)\s*[=:]\s*\S+|"
        r"https?://\S+\?\S+)",
        value,
    ):
        raise LedgerError(f"retirement ledger: {field} contains credential-like text; redact it")
    return value


def iso_date(value, field):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise LedgerError(f"retirement ledger: {field} must use YYYY-MM-DD")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise LedgerError(f"retirement ledger: {field} is not a valid date") from exc


def project_path(root, value):
    if not isinstance(value, str) or not value:
        raise LedgerError("retirement ledger: location must be a repository-relative path")
    relative = Path(value)
    temporary = {".git", ".scratch", ".local", "tmp", "temp", "dist", "build"}
    if relative.is_absolute() or ".." in relative.parts or temporary.intersection(relative.parts):
        raise LedgerError("retirement ledger: location must be durable inside the project outside temporary/generated storage")
    path = root / relative
    # No redirected destinations, including symlinked parent directories.
    if any(item.is_symlink() for item in [path, *path.parents] if item != root.parent):
        raise LedgerError("retirement ledger: symlinked locations are not allowed")
    if not path.resolve().is_relative_to(root):
        raise LedgerError("retirement ledger: location must stay in the project")
    return path


def committed_json(root, path):
    try:
        content = path.read_bytes()
        data = json.loads(content.decode("utf-8"), object_pairs_hook=unique_fields)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LedgerError("retirement ledger: could not read valid UTF-8 JSON") from exc
    relative = path.relative_to(root).as_posix()
    result = subprocess.run(
        ["git", "rev-parse", f"HEAD:{relative}"],
        cwd=root, capture_output=True,
    )
    # Compare the Git-cleaned representation, so an unchanged CRLF checkout
    # has the same identity as its LF blob. Without -w, hashing writes nothing.
    filtered = subprocess.run(
        ["git", "hash-object", f"--path={relative}", "--stdin"],
        cwd=root, input=content, capture_output=True,
    )
    if result.returncode or filtered.returncode or result.stdout.strip() != filtered.stdout.strip():
        raise LedgerError("retirement ledger: policy and records must be committed unchanged in HEAD")
    return data


def unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise LedgerError("retirement ledger: duplicate JSON fields")
        result[key] = value
    return result


def fields(data, expected, label):
    if not isinstance(data, dict) or set(data) != expected:
        raise LedgerError(f"retirement ledger: {label} fields do not match format 1")
    if type(data["format"]) is not int or data["format"] != 1:
        raise LedgerError(f"retirement ledger: {label} requires format 1")


def validate_ledger(root, policy_path, before, approvals=()):
    """Validate retained records and bind selected decisions to snapshot evidence."""
    root = Path(root).resolve()
    policy_file = project_path(root, policy_path)
    policy = committed_json(root, policy_file)
    fields(policy, {
        "format", "records_directory", "approved_by", "approved_on",
    }, "policy")
    safe_text(policy["approved_by"], "approved_by")
    iso_date(policy["approved_on"], "approved_on")
    directory = project_path(root, policy["records_directory"])
    if not directory.is_dir():
        raise LedgerError("retirement ledger: approved records directory is missing")
    records = {}
    paths = {}
    for path in sorted(directory.iterdir()):
        if path == policy_file:
            continue
        if not path.is_file() or path.suffix != ".json":
            raise LedgerError("retirement ledger: records directory must contain only JSON records")
        project_path(root, path.relative_to(root).as_posix())
        record = committed_json(root, path)
        fields(record, {
            "format", "decision", "ref", "protected_commit", "evidence",
            "decision_date", "approver",
        }, "record")
        if record["decision"] != "retire":
            raise LedgerError("retirement ledger: decision must be retire")
        ref = safe_text(record["ref"], "ref")
        check_ref = subprocess.run(
            ["git", "check-ref-format", ref], cwd=root, capture_output=True,
        )
        if not ref.startswith("refs/recovery/") or check_ref.returncode:
            raise LedgerError("retirement ledger: record requires an exact recovery ref")
        sha = record["protected_commit"]
        if not isinstance(sha, str) or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", sha):
            raise LedgerError("retirement ledger: protected_commit requires a full commit ID")
        safe_text(record["evidence"], "evidence")
        safe_text(record["approver"], "approver")
        iso_date(record["decision_date"], "decision_date")
        if record["decision_date"] < policy["approved_on"]:
            raise LedgerError("retirement ledger: decision predates location/format approval")
        if ref in records:
            raise LedgerError("retirement ledger: duplicate recovery ref decisions")
        records[ref] = record
        paths[ref] = path.relative_to(root).as_posix()
    selected = []
    selected_refs = set()
    for ref, evidence in approvals:
        if ref in selected_refs:
            raise LedgerError("retirement ledger: duplicate recovery retirement approval")
        selected_refs.add(ref)
        if ref not in records:
            raise LedgerError("retirement ledger: approved ref has no retained decision")
        record = records[ref]
        if before["refs"].get(ref) != record["protected_commit"]:
            raise LedgerError("retirement ledger: protected commit differs from the pre-removal snapshot")
        commit = subprocess.run(
            ["git", "cat-file", "-t", record["protected_commit"]],
            cwd=root, capture_output=True, text=True,
        )
        if commit.returncode or commit.stdout.strip() != "commit":
            raise LedgerError("retirement ledger: protected object must resolve to a commit")
        if evidence != record["evidence"]:
            raise LedgerError("retirement ledger: evidence differs from the exact approval")
        selected.append({"record_file": paths[ref], **record})
    return {
        "validated": True,
        "policy_file": policy_file.relative_to(root).as_posix(),
        "record_count": len(records),
        "decisions": sorted(selected, key=lambda item: item["ref"]),
    }
