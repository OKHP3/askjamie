"""Read-only recovery snapshots and exact retirement comparison allowances."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path


class RecoveryError(ValueError):
    """Recovery evidence is invalid or incomplete."""


def recovery_snapshot_integrity(snapshot):
    payload = json.dumps(
        snapshot, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return {"algorithm": "sha256", "digest": hashlib.sha256(payload).hexdigest()}


def validate_recovery_snapshot(snapshot):
    fields = {
        "format", "current_branch", "refs", "stashes", "reachable_objects",
        "integrity",
    }
    if not isinstance(snapshot, dict) or set(snapshot) != fields:
        raise RecoveryError("recovery snapshot format failed validation: fields differ")
    if type(snapshot["format"]) is not int or snapshot["format"] != 1:
        raise RecoveryError("recovery snapshot format failed validation: expected format 1")
    if snapshot["current_branch"] is not None and not isinstance(snapshot["current_branch"], str):
        raise RecoveryError("recovery snapshot current branch failed validation")
    refs = snapshot["refs"]
    if not isinstance(refs, dict) or not all(
        isinstance(ref, str) and isinstance(sha, str) for ref, sha in refs.items()
    ):
        raise RecoveryError("recovery snapshot ref inventory failed validation")
    if not isinstance(snapshot["stashes"], list) or not all(
        isinstance(item, str) for item in snapshot["stashes"]
    ):
        raise RecoveryError("recovery snapshot stash list failed validation")
    objects = snapshot["reachable_objects"]
    if not isinstance(objects, list) or not all(isinstance(item, str) for item in objects):
        raise RecoveryError("recovery snapshot reachable-object inventory failed validation")
    if objects != sorted(set(objects)):
        raise RecoveryError("recovery snapshot reachable-object inventory must be unique and sorted")
    integrity = snapshot["integrity"]
    expected = recovery_snapshot_integrity(
        {key: value for key, value in snapshot.items() if key != "integrity"}
    )
    if (
        not isinstance(integrity, dict)
        or set(integrity) != {"algorithm", "digest"}
        or integrity.get("algorithm") != "sha256"
        or not isinstance(integrity.get("digest"), str)
        or not hmac.compare_digest(integrity["digest"], expected["digest"])
    ):
        raise RecoveryError("recovery snapshot integrity marker failed validation")
    return snapshot


def recovery_snapshot(root, run):
    refs = {}
    for line in run(
        ["git", "for-each-ref", "--format=%(refname)%00%(objectname)"], root
    ).splitlines():
        ref, separator, sha = line.partition("\0")
        if not separator or not ref or not sha:
            raise RecoveryError("malformed Git ref record")
        refs[ref] = sha
    snapshot = {
        "format": 1,
        "current_branch": run(["git", "branch", "--show-current"], root) or None,
        "refs": dict(sorted(refs.items())),
        "stashes": run(
            ["git", "stash", "list", "--format=%H%x00%gd%x00%s"], root
        ).splitlines(),
        "reachable_objects": sorted({
            line.split(maxsplit=1)[0]
            for line in run(["git", "rev-list", "--all", "--objects"], root).splitlines()
            if line
        }),
    }
    snapshot["integrity"] = recovery_snapshot_integrity(snapshot)
    return snapshot


def read_recovery_snapshot(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RecoveryError("could not read recovery snapshot") from exc
    return validate_recovery_snapshot(data)


def write_recovery_snapshot(path, snapshot):
    validate_recovery_snapshot(snapshot)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as output:
            output.write(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
    except FileExistsError as exc:
        raise RecoveryError("recovery snapshot already exists; choose a new path") from exc


def parse_recovery_retirement(value):
    ref, separator, evidence = value.partition("=")
    if (
        not separator or not ref.startswith("refs/recovery/")
        or ref == "refs/recovery/" or not evidence.strip()
    ):
        raise RecoveryError(
            "recovery retirement must use refs/recovery/<exact-ref>=<evidence work is no longer needed>"
        )
    return ref, evidence.strip()


def compare_recovery_snapshots(
    before, after, approved_deletions=(), approved_recovery_retirements=(),
):
    validate_recovery_snapshot(before)
    validate_recovery_snapshot(after)
    approved_refs = set()
    for branch in approved_deletions:
        if not branch or branch.startswith("refs/"):
            raise RecoveryError("approved deletion must be a local branch name")
        if branch in {"main", before["current_branch"]}:
            raise RecoveryError("main and the checked-out branch are protected")
        approved_refs.add(f"refs/heads/{branch}")
    decisions = {}
    for value in approved_recovery_retirements:
        ref, evidence = parse_recovery_retirement(value)
        if ref in decisions:
            raise RecoveryError("duplicate recovery retirement approval")
        decisions[ref] = evidence
    before_refs, after_refs = before["refs"], after["refs"]
    removed = sorted(set(before_refs) - set(after_refs))
    changed = sorted(
        ref for ref in set(before_refs) & set(after_refs)
        if before_refs[ref] != after_refs[ref]
    )
    added = sorted(set(after_refs) - set(before_refs))
    tips = {before_refs[ref] for ref in approved_refs if ref in before_refs}
    invalid_added = [
        ref for ref in added
        if not (ref.startswith("refs/recovery/") and after_refs[ref] in tips)
    ]
    missing_recovery = sorted(
        ref for ref in approved_refs if ref in before_refs and not any(
            name.startswith("refs/recovery/") and sha == before_refs[ref]
            for name, sha in after_refs.items()
        )
    )
    unexpected_removed = sorted(set(removed) - approved_refs - set(decisions))
    missing_local = sorted(approved_refs - set(removed))
    missing_retired = sorted(set(decisions) - set(removed))
    lost_objects = sorted(set(before["reachable_objects"]) - set(after["reachable_objects"]))
    stash_changed = before["stashes"] != after["stashes"]
    errors = []
    for label, values in (
        ("unexpected refs removed", unexpected_removed),
        ("approved local refs were not removed", missing_local),
        ("approved recovery refs were not removed", missing_retired),
        ("refs changed", changed),
        ("unexpected refs added", invalid_added),
        ("removed local refs lack recovery refs", missing_recovery),
    ):
        if values:
            errors.append(label + ": " + ", ".join(values))
    if lost_objects:
        errors.append(f"{len(lost_objects)} previously reachable objects were lost")
    if stash_changed:
        errors.append("stash entries changed")
    return {
        "passed": not errors,
        "approved_local_deletions": sorted(approved_refs),
        "approved_recovery_retirements": [
            {"ref": ref, "evidence": decisions[ref]} for ref in sorted(decisions)
        ],
        "removed_refs": removed,
        "changed_refs": changed,
        "added_refs": added,
        "unexpected_removed_refs": unexpected_removed,
        "invalid_added_refs": invalid_added,
        "missing_recovery_refs": missing_recovery,
        "missing_approved_recovery_refs": missing_retired,
        "stashes_unchanged": not stash_changed,
        "unreachable_objects": lost_objects,
        "errors": errors,
    }