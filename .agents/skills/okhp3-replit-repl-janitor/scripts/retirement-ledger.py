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


def validate_policy(policy):
    fields(policy, {
        "format", "records_directory", "approved_by", "approved_on",
    }, "policy")
    safe_text(policy["approved_by"], "approved_by")
    iso_date(policy["approved_on"], "approved_on")


def validate_record(root, record):
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


def validate_ledger(root, policy_path, before, approvals=()):
    """Validate retained records and bind selected decisions to snapshot evidence."""
    root = Path(root).resolve()
    policy_file = project_path(root, policy_path)
    policy = committed_json(root, policy_file)
    validate_policy(policy)
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
        validate_record(root, record)
        ref = record["ref"]
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


def history_git(root, *args):
    result = subprocess.run(["git", *args], cwd=root, capture_output=True)
    if result.returncode:
        # Never echo Git stderr or historical free text into the report.
        raise LedgerError("retirement history: required Git history is unavailable")
    return result.stdout


def history_path(value):
    """Validate tree paths without consulting the possibly dirty checkout."""
    if not isinstance(value, str) or not value or "\\" in value:
        raise LedgerError("retirement history: invalid repository-relative location")
    path = Path(value)
    if (path.is_absolute() or any(part in {
        "", ".", "..", ".git", ".scratch", ".local", "tmp", "temp", "dist", "build",
    } for part in value.split("/"))):
        raise LedgerError("retirement history: location must be durable inside the project")
    safe_text(value, "location")
    return value


def tree_ledger(root, commit, policy_path):
    """Read only regular blobs from an exact committed tree; no ref lookup."""
    policy_path = history_path(policy_path)
    entries = {}
    for entry in history_git(root, "ls-tree", "-r", "-z", commit).split(b"\0"):
        if not entry:
            continue
        metadata, raw_path = entry.split(b"\t", 1)
        # Unrelated filenames need not be UTF-8.
        path = raw_path.decode("utf-8", errors="surrogateescape")
        mode, kind, oid = metadata.decode("ascii").split()
        entries[path] = (mode, kind, oid)

    def read(path):
        entry = entries.get(path)
        if not entry or entry[0] not in ("100644", "100755") or entry[1] != "blob":
            raise LedgerError("retirement history: policy or record is missing or not a regular blob")
        # Reject redirected parent locations even if a malicious tree has both.
        if any(entries.get(parent.as_posix(), ("",))[0] == "120000"
               for parent in Path(path).parents):
            raise LedgerError("retirement history: symlinked locations are not allowed")
        try:
            return json.loads(
                history_git(root, "cat-file", "blob", entry[2]).decode("utf-8"),
                object_pairs_hook=unique_fields,
            )
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise LedgerError("retirement history: could not read valid UTF-8 JSON") from exc

    policy = read(policy_path)
    validate_policy(policy)
    directory = history_path(policy["records_directory"])
    if directory in entries:
        raise LedgerError("retirement history: records location is not a directory")
    prefix = directory + "/"
    records, paths = {}, {}
    for path in sorted(entries):
        if not path.startswith(prefix) or path == policy_path:
            continue
        filename = path[len(prefix):]
        if "/" in filename or not filename.endswith(".json"):
            raise LedgerError("retirement history: records directory must contain only JSON records")
        record = read(path)
        validate_record(root, record)
        ref = record["ref"]
        if ref in records:
            raise LedgerError("retirement history: duplicate recovery ref decisions")
        records[ref], paths[ref] = record, path
    # Git cannot retain an empty directory; an empty ledger can still reveal
    # deleted records when compared with a preceding state.
    return policy, records, paths


def audit_history(root, policy_path, baseline, migrations=()):
    """Audit every first-parent state from an explicit baseline through HEAD."""
    root = Path(root).resolve()
    baseline_id = history_git(
        root, "rev-parse", "--verify", "--end-of-options", baseline + "^{commit}",
    ).decode("ascii").strip()
    head = history_git(root, "rev-parse", "--verify", "HEAD^{commit}").decode("ascii").strip()
    chain = history_git(root, "rev-list", "--first-parent", head).decode("ascii").splitlines()
    if baseline_id not in chain:
        raise LedgerError("retirement history: baseline must be on HEAD's first-parent history")
    commits = list(reversed(chain[:chain.index(baseline_id)]))
    approvals = {}
    for value in migrations:
        commit, separator, locations = value.partition("=")
        old, comma, new = locations.partition(",")
        if (not separator or not comma or "," in new
                or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit)):
            raise LedgerError("retirement history: migration requires FULL_COMMIT=OLD_POLICY,NEW_POLICY")
        if commit not in commits or commit in approvals:
            raise LedgerError("retirement history: migration commit is outside the audit or duplicated")
        approvals[commit] = (history_path(old), history_path(new))

    location = history_path(policy_path)
    policy, retained, retained_paths = tree_ledger(root, baseline_id, location)
    # Baseline decisions may predate a previously approved location migration.
    # Their original contents are the retention anchor, not new retirements.
    holds, approved, added = [], [], 0
    for commit in commits:
        previous_location = location
        migration = approvals.get(commit)
        if migration:
            if migration[0] != location:
                raise LedgerError("retirement history: migration source differs from active policy")
            location = migration[1]
        try:
            current_policy, records, paths = tree_ledger(root, commit, location)
        except LedgerError as exc:
            holds.append({"commit": commit, "reason": "invalid-ledger", "detail": str(exc)})
            # Keep the last valid state; later restoration cannot erase this hold.
            continue
        policy_changed = current_policy != policy or location != previous_location
        if policy_changed and not migration:
            holds.append({"commit": commit, "reason": "unapproved-policy-change"})
        if migration:
            if not policy_changed:
                raise LedgerError("retirement history: migration approval does not identify a policy change")
            approved.append({
                "commit": commit, "from_policy": previous_location, "to_policy": location,
                "status": "owner-approved-policy-migration",
            })
        for ref, original in retained.items():
            if ref not in records:
                holds.append({"commit": commit, "ref": ref, "reason": "removed-record",
                              "record_file": retained_paths[ref]})
            elif records[ref] != original:
                holds.append({"commit": commit, "ref": ref, "reason": "rewritten-record",
                              "changed_fields": sorted(
                                  key for key in original if original[key] != records[ref][key]
                              )})
        for ref, record in records.items():
            if ref not in retained:
                if record["decision_date"] < current_policy["approved_on"]:
                    holds.append({"commit": commit, "ref": ref,
                                  "reason": "decision-predates-policy"})
                retained[ref], retained_paths[ref] = record, paths[ref]
                added += 1
        policy = current_policy
    return {
        "passed": not holds, "baseline": baseline_id, "head": head,
        "history_scope": "first-parent", "commits_checked": len(commits) + 1,
        "retained_record_count": len(retained), "added_record_count": added,
        "policy_file": location, "approved_migrations": approved, "holds": holds,
    }
