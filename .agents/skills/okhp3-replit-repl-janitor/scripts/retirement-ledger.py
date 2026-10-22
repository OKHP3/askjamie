"""Validate owner-approved, committed JSON retirement records without writing."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import OrderedDict
from datetime import date
from pathlib import Path
from types import SimpleNamespace


class LedgerError(ValueError):
    """A retained retirement decision is not safe to rely on."""


class RetentionHoldsError(LedgerError):
    """Carry only safe identifiers from a failed migration retention proof."""

    def __init__(self, holds):
        super().__init__("retirement ledger: migration history has retention holds")
        # Refs and field names come from validated records. Do not forward
        # historical paths, free text, or future history-report fields.
        self.retention_holds = [
            {key: hold[key] for key in ("commit", "reason", "ref", "changed_fields")
             if key in hold}
            for hold in holds
        ]


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


def validate_ledger(root, policy_path, before, approvals=(), *,
                    baseline=None, baseline_policy=None, migrations=()):
    """Validate retained records and bind selected decisions to snapshot evidence."""
    root = Path(root).resolve()
    policy_file = project_path(root, policy_path)
    policy = committed_json(root, policy_file)
    validate_policy(policy)
    provenance = None
    migrated = {}
    if baseline or baseline_policy or migrations:
        if not (baseline and baseline_policy and migrations):
            raise LedgerError("retirement ledger: migration proof requires baseline commit, baseline policy, and exact migration approvals")
        provenance = audit_history(root, baseline_policy, baseline, migrations)
        if not provenance["passed"]:
            raise RetentionHoldsError(provenance["holds"])
        if provenance["policy_file"] != policy_file.relative_to(root).as_posix():
            raise LedgerError("retirement ledger: migration proof ends at a different policy")
        historical_policy, _, _ = tree_ledger(root, provenance["head"], provenance["policy_file"])
        if historical_policy != policy:
            raise LedgerError("retirement ledger: migration proof differs from current policy")
        latest = provenance["approved_migrations"][-1]
        parent = history_git(root, "rev-parse", latest["commit"] + "^1").decode("ascii").strip()
        _, migrated, _ = tree_ledger(root, parent, latest["from_policy"])
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
        if (record["decision_date"] < policy["approved_on"]
                and migrated.get(ref) != record):
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
    result = {
        "validated": True,
        "policy_file": policy_file.relative_to(root).as_posix(),
        "record_count": len(records),
        "decisions": sorted(selected, key=lambda item: item["ref"]),
    }
    if provenance is not None:
        result["migration_provenance"] = provenance
    return result


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


def tree_ledger(root, commit, policy_path, *, blobs=None, validated_records=None):
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
        oid = entry[2]
        if blobs is not None and oid in blobs:
            return blobs[oid]
        try:
            data = json.loads(
                history_git(root, "cat-file", "blob", oid).decode("utf-8"),
                object_pairs_hook=unique_fields,
            )
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise LedgerError("retirement history: could not read valid UTF-8 JSON") from exc
        if blobs is not None:
            blobs[oid] = data
        return data

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
        oid = entries[path][2]
        if validated_records is None or oid not in validated_records:
            validate_record(root, record)
            if validated_records is not None:
                validated_records.add(oid)
        ref = record["ref"]
        if ref in records:
            raise LedgerError("retirement history: duplicate recovery ref decisions")
        records[ref], paths[ref] = record, path
    # Git cannot retain an empty directory; an empty ledger can still reveal
    # deleted records when compared with a preceding state.
    return policy, records, paths


HISTORY_REUSE_BYTES = 4 * 1024 * 1024


def history_children(obj):
    """Walk immutable payload edges without retaining a second edge graph."""
    if isinstance(obj, dict):
        yield from obj.keys()
        yield from obj.values()
    elif isinstance(obj, (tuple, list, set, frozenset)):
        yield from obj


class HistoryObjectNode:
    __slots__ = ("obj", "references")

    def __init__(self, obj):
        self.obj = obj
        self.references = 0


class HistoryReuseBudget:
    """Shared identity-weighted LRU budget, never a validation/record-size limit.

    Charge getsizeof for keys and reachable built-in payload containers/scalars.
    Shared objects are charged once across all historical caches. Bookkeeping,
    active working sets and required report evidence are not a process RAM cap.
    """

    def __init__(self, maxbytes=HISTORY_REUSE_BYTES):
        self.maxbytes = maxbytes
        self.used_bytes = 0
        self.entries = OrderedDict()
        self.objects = {}

    def graph(self, key, value):
        objects = {}
        pending = [key, value]
        weight = 0
        while pending:
            obj = pending.pop()
            identity = id(obj)
            if identity in objects:
                continue
            size = sys.getsizeof(obj)
            objects[identity] = obj
            weight += size
            if weight > self.maxbytes:
                return None
            pending.extend(history_children(obj))
        return objects

    def touch(self, cache, key):
        self.entries.move_to_end((cache.identity, key))

    def remove(self, cache, key):
        _, identities = self.entries.pop((cache.identity, key))
        pending = list(identities)
        while pending:
            identity = pending.pop()
            node = self.objects[identity]
            node.references -= 1
            if node.references == 0:
                self.used_bytes -= sys.getsizeof(node.obj)
                pending.extend(map(id, history_children(node.obj)))
                del self.objects[identity]

    def admit(self, cache, key, value):
        objects = self.graph(key, value)
        # Do not flush useful smaller entries for an object that cannot fit.
        if objects is None:
            return False
        while True:
            extra = sum(sys.getsizeof(obj) for identity, obj in objects.items()
                        if identity not in self.objects)
            if self.used_bytes + extra <= self.maxbytes:
                break
            _, (old_cache, _) = next(iter(self.entries.items()))
            old_cache.popitem(last=False)
        # Payloads already retain their edges. Count them once per live unique
        # container, but do not duplicate them as tuples of allocated id ints.
        # JSON and ledger states are immutable and acyclic. References count
        # cache roots plus edges from live unique containers, including repeats.
        new_nodes = []
        for identity, obj in objects.items():
            if identity in self.objects:
                continue
            node = self.objects[identity] = HistoryObjectNode(obj)
            self.used_bytes += sys.getsizeof(obj)
            new_nodes.append(node)
        for node in new_nodes:
            for child in history_children(node.obj):
                self.objects[id(child)].references += 1
        roots = (id(key), id(value))
        for identity in roots:
            self.objects[identity].references += 1
        self.entries[(cache.identity, key)] = (cache, roots)
        return True


class HistoryObjectCache(OrderedDict):
    """Count- and optionally byte-bounded LRU, separate from audit evidence."""

    def __init__(self, maxsize, *, budget=None):
        super().__init__()
        self.maxsize = maxsize
        self.budget = budget
        self.identity = id(self)

    def touch(self, key):
        self.move_to_end(key)
        if self.budget is not None:
            self.budget.touch(self, key)

    def __contains__(self, key):
        present = super().__contains__(key)
        if present:
            self.touch(key)
        return present

    def __getitem__(self, key):
        value = super().__getitem__(key)
        self.touch(key)
        return value

    def __setitem__(self, key, value):
        if super().__contains__(key):
            del self[key]
        if self.maxsize <= 0:
            return
        if self.budget is not None and not self.budget.admit(self, key, value):
            return
        super().__setitem__(key, value)
        self.move_to_end(key)
        while len(self) > self.maxsize:
            self.popitem(last=False)

    def __delitem__(self, key):
        super().__delitem__(key)
        if self.budget is not None:
            self.budget.remove(self, key)

    def popitem(self, last=True):
        key = next(reversed(self) if last else iter(self))
        value = OrderedDict.__getitem__(self, key)
        del self[key]
        return key, value

    def clear(self):
        while self:
            self.popitem()

    def add(self, key):
        self[key] = True


class HistoryStateCache:
    """Callable snapshot cache using the same budget as parsed blobs."""

    def __init__(self, read, budget, maxsize=32):
        self.read = read
        self.cache = HistoryObjectCache(maxsize, budget=budget)
        self.hits = self.misses = 0

    def __call__(self, tree, policy_path):
        key = (tree, policy_path)
        if key in self.cache:
            self.hits += 1
            return self.cache[key]
        self.misses += 1
        state = self.read(tree, policy_path)
        self.cache[key] = state
        return state

    def cache_info(self):
        return SimpleNamespace(hits=self.hits, misses=self.misses,
                               maxsize=self.cache.maxsize, currsize=len(self.cache))


class HistoryScanCache:
    """Reuse successful sets and one failed prefix without scan self-eviction."""

    def __init__(self, previous, history, *, older=None, failed=None, validation=False):
        self.previous = previous
        self.older = older if older is not None else {}
        self.failed = failed if failed is not None else {}
        self.history = history
        self.validation = validation
        self.current = {}

    def __contains__(self, key):
        for working_set in (self.previous, self.older, self.failed):
            if key in working_set:
                self.current[key] = working_set[key]
                return True
        if key in self.history:
            self.current[key] = True if self.validation else self.history[key]
            return True
        return False

    def __getitem__(self, key):
        return self.current[key]

    def __setitem__(self, key, value):
        self.current[key] = value
        self.history[key] = value

    def add(self, key):
        self.current[key] = True
        self.history.add(key)


class HistoryLedgerReader:
    """Audit-local immutable-object reuse; never cache mutable refs or files."""

    def __init__(self, root):
        self.root = root
        self.reuse_budget = HistoryReuseBudget()
        self.blobs = HistoryObjectCache(maxsize=128, budget=self.reuse_budget)
        self.validated_records = HistoryObjectCache(maxsize=1024, budget=self.reuse_budget)
        self.current_blobs = {}
        self.current_validations = {}
        self.previous_blobs = {}
        self.previous_validations = {}
        # One failed scan's immutable prefix, separate from valid snapshots.
        # Replace rather than accumulate it; failed records are never marked
        # validated, and every new tree still runs path/mode/ledger checks.
        self.failed_blobs = {}
        self.failed_validations = {}
        # Bound the number of full ledger snapshots for unrelated tree changes.
        # The two most recent distinct valid object sets share objects even
        # when wider than the LRUs, including alternating A/B ledger scans.
        # Cold objects may be read/validated again; retention anchors and holds
        # are separate. Working sets are replaced, not accumulated.
        # Shared byte accounting covers snapshot references as well as blobs.
        # Working sets/retained evidence remain exempt, even above the budget.
        self.state = HistoryStateCache(self._state, self.reuse_budget)

    def _state(self, tree, policy_path):
        blobs = HistoryScanCache(
            self.current_blobs, self.blobs, older=self.previous_blobs,
            failed=self.failed_blobs,
        )
        validations = HistoryScanCache(
            self.current_validations, self.validated_records, validation=True,
            older=self.previous_validations,
            failed=self.failed_validations,
        )
        try:
            state = tree_ledger(
                self.root, tree, policy_path, blobs=blobs,
                validated_records=validations,
            )
        except LedgerError as exc:
            self.failed_blobs = blobs.current
            self.failed_validations = validations.current
            # Invalid immutable trees can repeat too. Replay the sanitized error
            # at every commit rather than dropping any per-commit hold.
            return str(exc)
        # Unrelated root edits and policy-only migrations must not replace the
        # second decision set with another copy of the first. Record OIDs
        # identify the set independently of policy/path changes; path, mode
        # and policy checks still run in tree_ledger for every root.
        if validations.current.keys() != self.current_validations.keys():
            self.previous_blobs = self.current_blobs
            self.previous_validations = self.current_validations
        self.current_blobs = blobs.current
        self.current_validations = validations.current
        return state

    def read(self, tree, policy_path):
        state = self.state(tree, policy_path)
        if isinstance(state, str):
            raise LedgerError(state)
        return state


def audit_history(root, policy_path, baseline, migrations=()):
    """Audit every first-parent state from an explicit baseline through HEAD."""
    root = Path(root).resolve()
    baseline_id = history_git(
        root, "rev-parse", "--verify", "--end-of-options", baseline + "^{commit}",
    ).decode("ascii").strip()
    head = history_git(root, "rev-parse", "--verify", "HEAD^{commit}").decode("ascii").strip()
    history = history_git(
        root, "log", "--first-parent", "--format=%H %T", head,
    ).decode("ascii").splitlines()
    trees = dict(line.split() for line in history)
    chain = list(trees)
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
    reader = HistoryLedgerReader(root)
    policy, baseline_records, baseline_paths = reader.read(trees[baseline_id], location)
    # Retention grows as new decisions appear. Do not mutate cached snapshots.
    retained, retained_paths = dict(baseline_records), dict(baseline_paths)
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
            current_policy, records, paths = reader.read(trees[commit], location)
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
