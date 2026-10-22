from __future__ import annotations

import importlib.util
import errno
import gc
import hashlib
import io
import json
import os
import random
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import tracemalloc
import unicodedata
import unittest
from collections import Counter
from contextlib import ExitStack
from functools import lru_cache
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).parents[1] / "scripts" / "audit-repo.py"
SPEC = importlib.util.spec_from_file_location("retirement_audit", SCRIPT)
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def sample_history_accounting(reader, counts):
    """Separate payload charges from owned accounting storage, without a copy.

    Include identity-map/LRU tables, nodes, entry/root tuples and identity ints.
    Exclude cache storage, shared small refcount ints and allocator overhead;
    traced heap/RSS remain the measurements of the whole audit. The list-node
    branch supports replaying the previous stored-edge implementation.
    """
    budget = reader.reuse_budget
    metadata = sys.getsizeof(budget.objects) + sys.getsizeof(budget.entries)
    empty_edges = False
    for identity, node in budget.objects.items():
        metadata += sys.getsizeof(identity) + sys.getsizeof(node)
        if isinstance(node, list):
            if node[3]:
                metadata += sys.getsizeof(node[3])
            else:
                empty_edges = True
            metadata += sum(map(sys.getsizeof, node[3]))
            if node[1] > 256:
                metadata += sys.getsizeof(node[1])
            references = node[2]
        else:
            references = node.references
        if references > 256:
            metadata += sys.getsizeof(references)
    if empty_edges:
        metadata += sys.getsizeof(())
    for entry_key, entry_value in budget.entries.items():
        metadata += sys.getsizeof(entry_key) + sys.getsizeof(entry_value)
        roots = entry_value[1]
        metadata += sys.getsizeof(roots) + sum(map(sys.getsizeof, roots))
    counts["payload_peak_bytes"] = max(counts["payload_peak_bytes"], budget.used_bytes)
    counts["bookkeeping_peak_bytes"] = max(counts["bookkeeping_peak_bytes"], metadata)


def large_record_memory_probe(root, policy, baseline, approvals, strategy, options=None):
    """Run only in a fresh interpreter; never inherit the fixture's heap/RSS."""
    ledger = audit.retirement_ledger
    original_init = ledger.HistoryLedgerReader.__init__
    original_git = ledger.history_git
    original_read = ledger.HistoryLedgerReader.read
    original_validate = ledger.validate_record
    counts = Counter()
    observations = []
    options = options or {}
    # The uncached wide control still parses/validates every blob every time.
    # Batch transport avoids tens of thousands of process launches; it does
    # not retain raw bytes, parsed objects, validations or ledger snapshots.
    batch = (subprocess.Popen(
        ["git", "cat-file", "--batch"], cwd=root,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    ) if options else None)

    def initialize(reader, root):
        original_init(reader, root)
        if "budget_bytes" in options:
            reader.reuse_budget.maxbytes = options["budget_bytes"]
        if strategy == "unbounded":
            reader.blobs = {}
            reader.validated_records = set()
            reader.state = lru_cache(maxsize=32)(reader._state)
        elif strategy == "entry-bounded":
            reader.blobs = ledger.HistoryObjectCache(maxsize=128)
            reader.validated_records = ledger.HistoryObjectCache(maxsize=1024)
            reader.state = lru_cache(maxsize=32)(reader._state)
        elif strategy == "minimal-history":
            # Experimental control, not a production byte-budget implementation.
            # Keep the current scan and all audit evidence, but no cold blobs
            # and only one full snapshot. Isolate avoidable historical reuse.
            reader.blobs = ledger.HistoryObjectCache(maxsize=0)
            reader.state = lru_cache(maxsize=1)(reader._state)
        elif strategy not in ("byte-bounded", "uncached"):
            raise ValueError(strategy)

    def validate(*args):
        counts["validations"] += 1
        return original_validate(*args)

    def observe(reader, tree, policy_path):
        before = counts.copy()
        state = (ledger.tree_ledger(reader.root, tree, policy_path)
                 if strategy == "uncached" else original_read(reader, tree, policy_path))
        sample_history_accounting(reader, counts)
        if options:
            # Digest complete validated records, not only their count. Keep
            # instrumentation small enough not to retain the measured evidence.
            records = state[1]
            cold_oid = options["cold_oid"]
            observations.append({
                "tree": tree, "policy": policy_path, "records": len(records),
                "records_digest": hashlib.sha256(
                    json.dumps(records, sort_keys=True).encode("utf-8")
                ).hexdigest(),
                "blob_reads": counts["cat-file"] - before["cat-file"],
                "validations": counts["validations"] - before["validations"],
                "budget_used": reader.reuse_budget.used_bytes,
                "historical_snapshots": reader.state.cache_info().currsize,
                "oversized_blobs_in_history": sum(
                    isinstance(record, dict)
                    and sys.getsizeof(record.get("evidence", "")) > options["budget_bytes"]
                    for record in reader.blobs.values()
                ),
                "cold_in_history": cold_oid in reader.blobs,
                "cold_in_working_sets": any(
                    cold_oid in working_set for working_set in
                    (reader.current_blobs, reader.previous_blobs, reader.failed_blobs)
                ),
            })
        return state

    def read(root, *args):
        counts[args[0]] += 1
        if batch is not None and args[0] == "cat-file":
            batch.stdin.write(args[2].encode("ascii") + b"\n")
            batch.stdin.flush()
            oid, kind, size = batch.stdout.readline().split()
            if oid.decode("ascii") != args[2] or kind != b"blob":
                raise AssertionError("unexpected batch blob response")
            data = batch.stdout.read(int(size))
            if len(data) != int(size) or batch.stdout.read(1) != b"\n":
                raise AssertionError("incomplete batch blob response")
            return data
        return original_git(root, *args)

    def rss():
        try:
            import resource
        except ImportError:
            return None
        # ru_maxrss is a process high-water mark, not instantaneous RSS.
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(value * (1 if sys.platform == "darwin" else 1024))

    def current_rss():
        # Linux-only companion to ru_maxrss. A high-water mark may already be
        # elevated at exec and cannot demonstrate allocator release after GC.
        try:
            for line in Path("/proc/self/status").read_text(encoding="ascii").splitlines():
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
        except OSError:
            pass
        return None

    gc.collect()
    rss_before = rss()
    resident_before = current_rss()
    tracemalloc.start()
    start = time.perf_counter()
    try:
        with patch.object(ledger.HistoryLedgerReader, "__init__", initialize), \
                patch.object(ledger.HistoryLedgerReader, "read", observe), \
                patch.object(ledger, "validate_record", validate), \
                patch.object(ledger, "history_git", read):
            result = ledger.audit_history(Path(root), policy, baseline, approvals)
        current, peak = tracemalloc.get_traced_memory()
        resident_after = current_rss()
        gc.collect()
        retained, _ = tracemalloc.get_traced_memory()
        resident_after_gc = current_rss()
    finally:
        tracemalloc.stop()
        if batch is not None:
            batch.stdin.close()
            batch.stdout.close()
            if batch.wait(timeout=10) != 0:
                raise AssertionError("batch blob reader failed")
    return {
        "strategy": strategy, "result": result, "heap_peak_bytes": peak,
        "heap_after_audit_bytes": current, "heap_after_gc_bytes": retained,
        "rss_peak_before_bytes": rss_before, "rss_peak_after_bytes": rss(),
        "rss_current_before_bytes": resident_before,
        "rss_current_after_audit_bytes": resident_after,
        "rss_current_after_gc_bytes": resident_after_gc,
        "blob_reads": counts["cat-file"], "seconds": time.perf_counter() - start,
        "validations": counts["validations"], "observations": observations,
        "payload_peak_bytes": counts["payload_peak_bytes"],
        "bookkeeping_peak_bytes": counts["bookkeeping_peak_bytes"],
        "python": sys.version.split()[0], "platform": sys.platform,
    }


class RetirementLedgerFixture(unittest.TestCase):
    object_format = "sha1"

    def setUp(self):
        # Inherit any existing process Git configuration, then override only
        # automatic maintenance for fixture, CLI and in-process audit commands.
        count = int(os.environ.get("GIT_CONFIG_COUNT", "0"))
        config = {"GIT_CONFIG_COUNT": str(count + 2)}
        for index, (key, value) in enumerate(
            (("gc.auto", "0"), ("maintenance.auto", "false")), start=count,
        ):
            config[f"GIT_CONFIG_KEY_{index}"] = key
            config[f"GIT_CONFIG_VALUE_{index}"] = value
        environment = patch.dict(os.environ, config)
        environment.start()
        self.addCleanup(environment.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        if self.object_format == "sha256":
            result = subprocess.run(
                ["git", "init", "-b", "main", "--object-format=sha256"],
                cwd=self.root, capture_output=True, text=True,
            )
            if result.returncode and (
                "unknown option" in result.stderr
                or "unknown hash algorithm" in result.stderr
                or "unsupported hash algorithm" in result.stderr
                or "not supported" in result.stderr
            ):
                self.skipTest("Git lacks SHA-256 repository support: " + result.stderr.strip())
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.git("init", "-b", "main")
        self.git("config", "user.name", "Fixture Owner")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("commit", "--allow-empty", "-m", "Initial")
        self.sha = self.git("rev-parse", "HEAD")
        self.ref = "refs/recovery/feature-example"
        self.git("update-ref", self.ref, self.sha)
        self.policy_path = "governance/recovery-retirements/policy.json"
        self.records_path = "governance/recovery-retirements/decisions"
        self.record_path = self.records_path + "/feature-example.json"
        self.policy = {
            "format": 1, "records_directory": self.records_path,
            "approved_by": "owner-handle", "approved_on": "2026-10-02",
        }
        self.record = {
            "format": 1, "decision": "retire", "ref": self.ref,
            "protected_commit": self.sha,
            "evidence": "Protected commit is retained in main.",
            "decision_date": "2026-10-02", "approver": "owner-handle",
        }
        self.write(self.policy_path, self.policy)
        self.write(self.record_path, self.record)
        self.commit()
        self.snapshot = self.root / "before.json"
        result, data = self.cli("--snapshot-recovery", str(self.snapshot))
        self.assertEqual(result.returncode, 0, data)

    def git(self, *args):
        result = subprocess.run(
            ["git", *args], cwd=self.root, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def assert_maintenance_is_isolated(self):
        for key, value in (("gc.auto", "0"), ("maintenance.auto", "false")):
            with self.subTest(key=key):
                local = subprocess.run(
                    ["git", "config", "--local", "--get", key],
                    cwd=self.root, capture_output=True, text=True,
                )
                self.assertEqual(local.returncode, 1, local.stderr)
                self.assertEqual(self.git("config", "--get", key), value)
                # A separate process sees the same settings as the CLI audit.
                result = subprocess.run(
                    [sys.executable, "-c",
                     "import subprocess, sys; "
                     "subprocess.run(['git', 'config', '--get', sys.argv[1]], check=True)",
                     key],
                    cwd=self.root, capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), value)

    def repository_bytes(self):
        # Include checkout, index, refs, objects, reflogs and before.json.
        # POSIX mode bits cover executability and access without volatile
        # timestamps. Windows chmod does not provide Unix permission semantics;
        # retain byte/link coverage there and explicitly skip chmod controls.
        def mode(path):
            return stat.S_IMODE(path.stat().st_mode) if os.name == "posix" else None

        entries = {
            path.relative_to(self.root).as_posix(): ("file", mode(path), path.read_bytes())
            for path in self.root.rglob("*")
            if not path.is_symlink() and path.is_file()
        } | {
            path.relative_to(self.root).as_posix(): ("directory", mode(path))
            for path in (self.root, *self.root.rglob("*"))
            if not path.is_symlink() and path.is_dir()
        } | self.repository_links()
        # ACL capture is opt-in for the platform-gated ACL controls. Ordinary
        # snapshots do not claim ACL coverage or require external utilities.
        if getattr(self, "acl_tools", None):
            for name, entry in entries.items():
                if entry[0] != "symlink":
                    entries[name] += (self.read_acl(self.root / name),)
        # Device/inode identify aliases only within this capture. Persist
        # sorted relative names instead, so equivalent copies compare equal
        # even when every file has been allocated a different inode.
        groups = {}
        for name, entry in entries.items():
            if entry[0] == "file":
                info = (self.root / name).lstat()
                if info.st_ino:
                    groups.setdefault((info.st_dev, info.st_ino), []).append(name)
        aliases_by_name = {
            name: tuple(sorted(names))
            for names in groups.values() if len(names) > 1 for name in names
        }
        for name, entry in entries.items():
            if entry[0] == "file":
                entries[name] += (aliases_by_name.get(name, ()),)
        return entries

    def create_probe_hard_link(self, source, destination):
        try:
            os.link(source, destination)
        except (AttributeError, NotImplementedError):
            self.skipTest("platform does not implement hard links")
        except OSError as exc:
            if exc.errno in (
                errno.EPERM, errno.EACCES, errno.ENOSYS, errno.ENOTSUP,
                errno.EXDEV,
            ) or getattr(exc, "winerror", None) in (1, 50, 1314):
                self.skipTest(f"filesystem cannot create hard links: {exc}")
            raise
        info = source.stat()
        if not info.st_ino or not os.path.samefile(source, destination):
            self.skipTest("filesystem does not expose reliable hard-link identity")

    def assert_history_hard_link_changes_detected(self, check_history, *, cli=False):
        ledger = audit.retirement_ledger
        original_audit = ledger.audit_history
        original_cli = self.cli
        stages = ("cached", "uncached", "cli") if cli else ("cached", "uncached")
        initial = self.repository_bytes()
        # Exercise checkout and Git metadata, including aliases spanning their
        # boundary. Never hard-link a loose Git object that Git may protect.
        for relative, alias in (
            (self.record_path, ".git/hard-link-probe"),
            (".git/index", "hard-link-probe"),
        ):
            source, path = self.root / relative, self.root / alias
            replacement = self.root / "hard-link-replacement"
            try:
                self.create_probe_hard_link(source, path)
                before = self.repository_bytes()
                aliases = tuple(sorted((relative, alias)))
                for name in aliases:
                    self.assertEqual(before[name][-1], aliases)
                # Allocate a new inode while the old one is still linked, then
                # rebuild the same alias group. Raw inode snapshots would
                # incorrectly fail this equivalent-layout positive control.
                replacement.write_bytes(source.read_bytes())
                replacement.chmod(stat.S_IMODE(source.stat().st_mode))
                replacement.replace(source)
                self.assertFalse(os.path.samefile(source, path))
                path.unlink()
                os.link(source, path)
                self.assertEqual(self.repository_bytes(), before)
                check_history()  # A genuine read-only audit preserves aliases.
                for stage in stages:
                    with self.subTest(path=relative, stage=stage):
                        calls = 0
                        mutations = []

                        def mutate():
                            content = path.read_bytes()
                            original_mode = stat.S_IMODE(path.stat().st_mode)
                            path.unlink()
                            path.write_bytes(content)
                            path.chmod(original_mode)
                            self.assertFalse(os.path.samefile(source, path))
                            after = self.repository_bytes()
                            # Prove byte/type/mode-only snapshots miss this.
                            def without_aliases(snapshot):
                                return {
                                    name: entry[:-1] if entry[0] == "file" else entry
                                    for name, entry in snapshot.items()
                                }
                            self.assertEqual(without_aliases(after), without_aliases(before))
                            self.assertNotEqual(after, before)
                            mutations.append(stage)

                        def changing_audit(*args, **kwargs):
                            nonlocal calls
                            report = original_audit(*args, **kwargs)
                            calls += 1
                            if calls == (2 if stage == "uncached" else 1):
                                mutate()
                            return report

                        def changing_cli(*args, **kwargs):
                            result = original_cli(*args, **kwargs)
                            mutate()
                            return result

                        owner, attribute, effect = (
                            (self, "cli", changing_cli) if stage == "cli"
                            else (ledger, "audit_history", changing_audit)
                        )
                        try:
                            with patch.object(owner, attribute, side_effect=effect):
                                with self.assertRaises(AssertionError):
                                    check_history()
                            self.assertEqual(mutations, [stage],
                                             "mutation checks failed before the read-only guard")
                            self.assertEqual(calls, 0 if stage == "cli" else
                                             2 if stage == "uncached" else 1)
                            self.assertFalse(os.path.samefile(source, path))
                            self.assertNotEqual(self.repository_bytes(), before)
                        finally:
                            path.unlink(missing_ok=True)
                            os.link(source, path)
                        self.assertEqual(self.repository_bytes(), before)
                        check_history()
            finally:
                replacement.unlink(missing_ok=True)
                path.unlink(missing_ok=True)
            self.assertEqual(self.repository_bytes(), initial)

    def acl_command(self, tool, *args, input=None):
        return subprocess.run(
            [self.acl_tools[tool], *args], input=input, capture_output=True,
            env={**os.environ, "LC_ALL": "C"}, timeout=10,
        )

    def read_acl(self, path):
        result = self.acl_command("getfacl", "-c", "-p", "-n", "--", str(path))
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def restore_acl(self, path, saved):
        result = self.acl_command("setfacl", "--set-file=-", "--", str(path), input=saved)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.read_acl(path), saved)

    def require_linux_acl(self):
        if not sys.platform.startswith("linux"):
            self.skipTest("ACL coverage requires Linux POSIX ACL tooling/semantics")
        tools = {name: shutil.which(name) for name in ("getfacl", "setfacl")}
        missing = [name for name, executable in tools.items() if not executable]
        if missing:
            self.skipTest("ACL coverage unavailable: missing optional " + ", ".join(missing))
        self.acl_tools = tools
        # A non-owner named user is resolved before group/other ACL entries.
        # With a read-enabled mask, --- versus r-- changes effective access,
        # even when the runner is root (whose access() would bypass the ACL).
        self.acl_uid = 65534 if self.root.stat().st_uid != 65534 else 65533
        with tempfile.NamedTemporaryFile(dir=self.root) as probe:
            path = Path(probe.name)
            original = self.read_acl(path)
            try:
                result = self.acl_command(
                    "setfacl", "-n", "-m",
                    f"u:{self.acl_uid}:---,m::r--", "--", str(path),
                )
                if result.returncode:
                    reason = result.stderr.decode("utf-8", errors="replace").strip()
                    if any(message in reason for message in (
                        "Operation not supported", "Permission denied",
                        "Operation not permitted", "Function not implemented",
                    )):
                        self.skipTest("ACL coverage unavailable on fixture filesystem: " + reason)
                    self.fail("ACL probe failed: " + reason)
                self.assertIn(f"user:{self.acl_uid}:---\n".encode(), self.read_acl(path))
            finally:
                # An unsupported setfacl normally leaves the probe unchanged.
                # Do not mask its explicit skip with a second unsupported write.
                if self.read_acl(path) != original:
                    self.restore_acl(path, original)

    def assert_history_acls_detected(self, check_history, *, cli=False):
        self.require_linux_acl()
        ledger = audit.retirement_ledger
        original_audit, original_cli = ledger.audit_history, self.cli
        stages = ("cached", "uncached", "cli") if cli else ("cached", "uncached")
        initial = self.repository_bytes()
        for relative in (self.record_path, ".git/index"):
            path = self.root / relative
            original_acl = self.read_acl(path)
            original_mode = stat.S_IMODE(path.stat().st_mode)
            try:
                # Establish a read-enabled mask first, outside the guard. Its
                # permissions are the mode's group bits; -n prevents recalculation.
                path.chmod(original_mode | stat.S_IRGRP)
                group = (stat.S_IMODE(path.stat().st_mode) >> 3) & 7
                mask = "".join(letter if group & bit else "-"
                               for letter, bit in (("r", 4), ("w", 2), ("x", 1)))
                result = self.acl_command(
                    "setfacl", "-n", "-m",
                    f"u:{self.acl_uid}:---,m::{mask}", "--", str(path),
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                saved = self.read_acl(path)
                mode, content = stat.S_IMODE(path.stat().st_mode), path.read_bytes()
                before = self.repository_bytes()
                check_history()  # An unmodified ACL passes the same guard.
                for stage in stages:
                    with self.subTest(path=relative, stage=stage):
                        calls, mutations = 0, []

                        def mutate():
                            result = self.acl_command(
                                "setfacl", "-n", "-m",
                                f"u:{self.acl_uid}:r--", "--", str(path),
                            )
                            self.assertEqual(result.returncode, 0, result.stderr)
                            self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode)
                            self.assertEqual(path.read_bytes(), content)
                            changed = self.read_acl(path)
                            self.assertIn(f"user:{self.acl_uid}:r--\n".encode(), changed)
                            self.assertEqual(changed, saved.replace(
                                f"user:{self.acl_uid}:---\n".encode(),
                                f"user:{self.acl_uid}:r--\n".encode(),
                            ))
                            after = self.repository_bytes()
                            # Without ACL evidence every entry is identical:
                            # bytes, type, mode and hard-link alias layout.
                            def without_acls(snapshot):
                                return {
                                    name: entry[:3] + entry[4:] if entry[0] == "file"
                                    else entry[:2] if entry[0] == "directory" else entry
                                    for name, entry in snapshot.items()
                                }
                            self.assertEqual(without_acls(after), without_acls(before))
                            self.assertNotEqual(after, before)
                            mutations.append(stage)

                        def changing_audit(*args, **kwargs):
                            nonlocal calls
                            report = original_audit(*args, **kwargs)
                            calls += 1
                            if calls == (2 if stage == "uncached" else 1):
                                mutate()
                            return report

                        def changing_cli(*args, **kwargs):
                            result = original_cli(*args, **kwargs)
                            mutate()
                            return result

                        owner, attribute, effect = (
                            (self, "cli", changing_cli) if stage == "cli"
                            else (ledger, "audit_history", changing_audit)
                        )
                        try:
                            with patch.object(owner, attribute, side_effect=effect):
                                with self.assertRaises(AssertionError):
                                    check_history()
                            self.assertEqual(mutations, [stage],
                                             "mutation failed before the read-only guard")
                            self.assertEqual(calls, 0 if stage == "cli" else
                                             2 if stage == "uncached" else 1)
                            self.assertNotEqual(self.repository_bytes(), before)
                        finally:
                            self.restore_acl(path, saved)
                        self.assertEqual(self.repository_bytes(), before)
                        check_history()  # Exact ACL restoration passes again.
            finally:
                self.restore_acl(path, original_acl)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), original_mode)
            self.assertEqual(self.repository_bytes(), initial)

    def require_unix_permissions(self):
        if os.name != "posix":
            self.skipTest("platform lacks meaningful Unix permission semantics")
        # Some POSIX-mounted filesystems ignore chmod. Probe the filesystem,
        # not just the OS, and never hide unexpected chmod failures.
        with tempfile.NamedTemporaryFile(dir=self.root) as probe:
            path = Path(probe.name)
            original = stat.S_IMODE(path.stat().st_mode)
            changed = original ^ stat.S_IXUSR
            try:
                path.chmod(changed)
                if stat.S_IMODE(path.stat().st_mode) != changed:
                    self.skipTest("filesystem does not preserve Unix permission bits")
            finally:
                path.chmod(original)

    def assert_history_modes_detected(self, check_history, *, cli=False):
        self.require_unix_permissions()
        ledger = audit.retirement_ledger
        original_audit = ledger.audit_history
        original_cli = self.cli
        # Include a real read-only loose object as well as writable metadata,
        # and directories whose permissions are invisible to byte snapshots.
        object_path = f".git/objects/{self.sha[:2]}/{self.sha[2:]}"
        stages = ("cached", "uncached", "cli") if cli else ("cached", "uncached")
        for relative in (
            self.record_path, "before.json", ".git/index",
            ".git/refs/heads/main", ".git/logs/HEAD", object_path,
            "governance", ".git/objects", ".",
        ):
            path = self.root / relative
            original_mode = stat.S_IMODE(path.stat().st_mode)
            bit = stat.S_IWGRP if path.is_dir() else stat.S_IXUSR
            content = path.read_bytes() if path.is_file() else None
            for stage in stages:
                with self.subTest(path=relative, stage=stage):
                    before = self.repository_bytes()
                    calls = 0

                    def mutate():
                        path.chmod(original_mode ^ bit)
                        self.assertEqual(stat.S_IMODE(path.stat().st_mode), original_mode ^ bit)
                        if content is not None:
                            self.assertEqual(path.read_bytes(), content)

                    def changing_audit(*args, **kwargs):
                        nonlocal calls
                        report = original_audit(*args, **kwargs)
                        calls += 1
                        if calls == (2 if stage == "uncached" else 1):
                            mutate()
                        return report

                    def changing_cli(*args, **kwargs):
                        result = original_cli(*args, **kwargs)
                        mutate()
                        return result

                    owner, attribute, effect = (
                        (self, "cli", changing_cli) if stage == "cli"
                        else (ledger, "audit_history", changing_audit)
                    )
                    try:
                        with patch.object(owner, attribute, side_effect=effect):
                            with self.assertRaises(AssertionError):
                                check_history()
                        self.assertNotEqual(self.repository_bytes(), before)
                    finally:
                        path.chmod(original_mode)
                    self.assertEqual(self.repository_bytes(), before)
                    check_history()  # Restored permissions must pass again.

    def repository_links(self):
        # Check the link itself, never its referent: include dangling links and
        # directory links without following them outside the repository.
        return {
            path.relative_to(self.root).as_posix(): ("symlink", os.readlink(path))
            for path in self.root.rglob("*") if path.is_symlink()
        }

    def create_probe_link(self, path, target, *, directory=False):
        try:
            path.symlink_to(target, target_is_directory=directory)
        except NotImplementedError:
            self.skipTest("platform does not implement symbolic links")
        except OSError as exc:
            if exc.errno in (
                errno.EPERM, errno.EACCES, errno.ENOSYS, errno.ENOTSUP,
            ) or getattr(exc, "winerror", None) == 1314:
                self.skipTest(f"platform cannot create symbolic links: {exc}")
            raise

    def assert_snapshot_link_boundary(self, kind):
        before = self.repository_bytes()
        expected_links = {}
        directories = {}
        with tempfile.TemporaryDirectory() as outside:
            outside = Path(outside)
            secret = outside / "outside-only.txt"
            secret.write_bytes(b"must never be read by a repository snapshot\n")
            nested = outside / "nested"
            nested.mkdir()
            (nested / "another-outside-only.txt").write_bytes(b"not repository data\n")
            if kind == "self":
                targets = (("self-link", "self-link", False),)
            elif kind == "cycle":
                for name in ("cycle-a", "cycle-b"):
                    path = self.root / name
                    path.mkdir()
                    directories[name] = (
                        "directory",
                        stat.S_IMODE(path.stat().st_mode) if os.name == "posix" else None,
                    )
                targets = (
                    ("cycle-a/next", "../cycle-b", True),
                    ("cycle-b/next", "../cycle-a", True),
                )
            else:
                self.assertEqual(kind, "external")
                targets = (
                    ("external-directory", str(outside), True),
                    ("external-file", str(secret), False),
                    ("external-relative", os.path.relpath(outside, self.root), True),
                )
            for name, target, directory in targets:
                self.create_probe_link(self.root / name, target, directory=directory)
                expected_links[name] = ("symlink", target)

            # Permit only known real repository files/directories. Do not
            # resolve paths in the guard: resolving the self-link would itself
            # follow the very boundary under test. Alias paths (even aliases
            # back inside the repo) and direct outside paths are both rejected.
            allowed_files = {
                self.root / name for name, entry in before.items() if entry[0] == "file"
            }
            allowed_directories = {
                self.root / name
                for name, entry in (before | directories).items()
                if entry[0] == "directory"
            }
            original_scandir = os.scandir
            scans = Counter()

            def guarded_scan(path):
                path = Path(os.path.abspath(os.fspath(path)))
                self.assertIn(path, allowed_directories, "snapshot traversed a link or outside directory")
                scans[path] += 1
                # Bound repeated visits as well as rejecting linked paths, so
                # a cycle-following regression fails rather than hanging.
                self.assertLessEqual(scans[path], 16, "snapshot traversal did not terminate")
                return original_scandir(path)

            def guard_read(original):
                def guarded(path, *args, **kwargs):
                    path_key = Path(os.path.abspath(os.fspath(path)))
                    self.assertIn(path_key, allowed_files, "snapshot read a link or outside file")
                    return original(path, *args, **kwargs)
                return guarded

            with ExitStack() as stack:
                stack.enter_context(patch("os.scandir", side_effect=guarded_scan))
                for owner, attribute in ((io, "open"), (os, "open")):
                    stack.enter_context(patch.object(
                        owner, attribute, side_effect=guard_read(getattr(owner, attribute)),
                    ))
                stack.enter_context(patch("builtins.open", side_effect=guard_read(open)))
                if kind == "external":
                    # Negative controls prove guards cover direct and aliased
                    # access, not merely absence of outside keys in the result.
                    for path in (secret, self.root / "external-file",
                                 self.root / "external-directory" / secret.name):
                        with self.assertRaisesRegex(AssertionError, "snapshot read"):
                            path.read_bytes()
                    for path in (outside, self.root / "external-directory"):
                        with self.assertRaisesRegex(AssertionError, "snapshot traversed"):
                            os.scandir(path)
                for capture, expected in (
                    (self.repository_links, expected_links),
                    (self.repository_bytes, before | directories | expected_links),
                ):
                    with self.subTest(snapshot=capture.__name__):
                        scans.clear()
                        self.assertEqual(capture(), expected)
                        self.assertTrue(scans, "snapshot did not exercise traversal")

    def assert_history_link_changes_detected(self, check_history, *, cli=False):
        ledger = audit.retirement_ledger
        original = ledger.audit_history
        original_cli = self.cli
        # Equal-content referents make retargeting invisible to byte-only
        # snapshots. Directory and dangling links must not be omitted either.
        for name in ("link-target-a", "link-target-b"):
            (self.root / name).write_bytes(b"identical target bytes\n")
        for name in ("link-directory-a", "link-directory-b"):
            (self.root / name).mkdir()
        kinds = (
            ("file", "link-target-a", "link-target-b"),
            ("directory", "link-directory-a", "link-directory-b"),
            ("dangling", "missing-target-a", "missing-target-b"),
        )
        stages = ("cached", "uncached", "cli") if cli else ("cached", "uncached")
        for kind, target, replacement in kinds:
            path = self.root / f"audit-{kind}-link"
            self.create_probe_link(path, target, directory=kind == "directory")
            self.assertEqual(self.repository_links()[path.name], ("symlink", target))
            check_history()  # Unchanged links must pass.
            path.unlink()
            for stage in stages:
                for operation in ("create", "remove", "retarget"):
                    with self.subTest(kind=kind, stage=stage, operation=operation):
                        if operation != "create":
                            self.create_probe_link(path, target, directory=kind == "directory")
                        before = self.repository_bytes()

                        def mutate():
                            if operation != "create":
                                path.unlink()
                            if operation != "remove":
                                self.create_probe_link(
                                    path, replacement if operation == "retarget" else target,
                                    directory=kind == "directory",
                                )

                        calls = 0

                        def changing_audit(*args, **kwargs):
                            nonlocal calls
                            report = original(*args, **kwargs)
                            calls += 1
                            if calls == (2 if stage == "uncached" else 1):
                                mutate()
                            return report

                        def changing_cli(*args, **kwargs):
                            result = original_cli(*args, **kwargs)
                            mutate()
                            return result

                        owner, attribute, effect = (
                            (self, "cli", changing_cli) if stage == "cli"
                            else (ledger, "audit_history", changing_audit)
                        )
                        try:
                            with patch.object(owner, attribute, side_effect=effect):
                                with self.assertRaises(AssertionError):
                                    check_history()
                        finally:
                            path.unlink(missing_ok=True)
                            if operation != "create":
                                self.create_probe_link(
                                    path, target, directory=kind == "directory",
                                )
                        self.assertEqual(self.repository_bytes(), before)
                        check_history()
                        path.unlink(missing_ok=True)

    def assert_history_link_type_changes_detected(self, check_history, *, cli=False):
        ledger = audit.retirement_ledger
        original_audit = ledger.audit_history
        original_cli = self.cli
        content = b"identical type-change bytes\n"
        target = self.root / "type-change-target"
        target.write_bytes(content)
        path = self.root / "audit-type-probe"
        # Probe link support before replacing any regular file. Unexpected
        # errors still fail through the existing narrowly scoped link helper.
        self.create_probe_link(path, target.name)
        path.unlink()
        mode = stat.S_IMODE(target.stat().st_mode)
        stages = ("cached", "uncached", "cli") if cli else ("cached", "uncached")
        for original_type in ("file", "symlink"):
            replacement_type = "symlink" if original_type == "file" else "file"
            for stage in stages:
                with self.subTest(original=original_type, replacement=replacement_type,
                                  stage=stage):
                    def install(kind):
                        if kind == "symlink":
                            self.create_probe_link(path, target.name)
                        else:
                            path.write_bytes(content)
                            path.chmod(mode)

                    install(original_type)
                    before = self.repository_bytes()
                    self.assertEqual(before[path.name][0], original_type)
                    check_history()  # Both unchanged entry types must pass.
                    calls = 0

                    def mutate():
                        path.unlink()
                        install(replacement_type)

                    def changing_audit(*args, **kwargs):
                        nonlocal calls
                        report = original_audit(*args, **kwargs)
                        calls += 1
                        if calls == (2 if stage == "uncached" else 1):
                            mutate()
                        return report

                    def changing_cli(*args, **kwargs):
                        result = original_cli(*args, **kwargs)
                        mutate()
                        return result

                    owner, attribute, effect = (
                        (self, "cli", changing_cli) if stage == "cli"
                        else (ledger, "audit_history", changing_audit)
                    )
                    try:
                        with patch.object(owner, attribute, side_effect=effect):
                            with self.assertRaises(AssertionError):
                                check_history()
                        self.assertEqual(
                            self.repository_bytes()[path.name][0], replacement_type,
                        )
                        self.assertEqual(path.read_bytes(), content)
                        self.assertEqual(target.read_bytes(), content)
                        self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode)
                        self.assertNotEqual(self.repository_bytes(), before)
                    finally:
                        path.unlink(missing_ok=True)
                        install(original_type)
                    self.assertEqual(self.repository_bytes(), before)
                    check_history()  # Restored types must pass the full check.
                    path.unlink()

    def assert_read_only_retirement_check(self, check):
        before = self.repository_bytes()
        recovery_before = self.snapshot.read_bytes()
        result = check()
        self.assertEqual(self.snapshot.read_bytes(), recovery_before,
                         "retirement check changed the before-snapshot")
        self.assertEqual(self.repository_bytes(), before,
                         "retirement check changed repository bytes or permissions")
        return result

    def assert_history_writes_detected(self, check_history):
        ledger = audit.retirement_ledger
        original = ledger.audit_history
        for relative in (
            self.record_path, ".git/index", ".git/refs/heads/main",
            ".git/logs/HEAD", ".git/objects/audit-write-probe",
        ):
            with self.subTest(path=relative):
                path = self.root / relative
                previous = path.read_bytes() if path.exists() else None

                def writing_audit(*args, **kwargs):
                    report = original(*args, **kwargs)
                    path.write_bytes((previous or b"") + b"\naudit write probe\n")
                    return report

                try:
                    with patch.object(ledger, "audit_history", side_effect=writing_audit):
                        with self.assertRaises(AssertionError):
                            check_history()
                finally:
                    if previous is None:
                        path.unlink(missing_ok=True)
                    else:
                        path.write_bytes(previous)
        # Restoring the injected writes must restore the passing assertion.
        check_history()

    def write(self, relative, data):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def commit(self):
        self.git("add", "governance")
        self.git("commit", "--allow-empty", "-m", "Owner-approved fixture records")

    def cli(self, *args):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(self.root), *args],
            capture_output=True, text=True,
        )
        return result, json.loads(result.stdout)

    def allowance(self):
        return self.ref + "=" + self.record["evidence"]

    def verify(self, allowance=None):
        return self.cli(
            "--verify-recovery", str(self.snapshot),
            "--retirement-ledger", self.policy_path,
            "--approve-recovery-retirement", allowance or self.allowance(),
        )

    def preflight(self):
        return self.cli(
            "--validate-retirement-ledger", self.policy_path,
            "--approve-recovery-retirement", self.allowance(),
        )

    def migrate(self):
        new_policy = "governance/retained/policy.json"
        new_directory = "governance/retained/decisions"
        new_record = new_directory + "/old.json"
        self.git("rm", self.policy_path, self.record_path)
        self.write(new_policy, {
            **self.policy, "records_directory": new_directory,
            "approved_on": "2026-10-03",
        })
        self.write(new_record, self.record)
        self.commit()
        migration = self.git("rev-parse", "HEAD")
        return migration, new_policy, new_record

    def new_retirement_after_migration(self):
        baseline = self.git("rev-parse", "HEAD")
        old_policy = self.policy_path
        self.git("update-ref", "-d", self.ref, self.sha)
        migration, new_policy, old_record_path = self.migrate()
        new_record = {
            **self.record, "ref": "refs/recovery/new-work",
            "decision_date": "2026-10-03",
        }
        new_record_path = "governance/retained/decisions/new.json"
        self.write(new_record_path, new_record)
        self.git("update-ref", new_record["ref"], self.sha)
        self.commit()
        proof = (
            "--ledger-baseline", baseline,
            "--ledger-baseline-policy", old_policy,
            "--approve-ledger-migration", f"{migration}={old_policy},{new_policy}",
        )
        self.policy_path = new_policy
        self.record_path = new_record_path
        self.ref = new_record["ref"]
        self.record = new_record
        return proof, old_record_path

    def proven_preflight(self, proof, allowance=None):
        return self.cli(
            "--validate-retirement-ledger", self.policy_path, *proof,
            "--approve-recovery-retirement", allowance or self.allowance(),
        )


class ACLProbeTests(unittest.TestCase):
    def test_non_linux_platform_explicitly_skips_acl_coverage(self):
        fixture = RetirementLedgerFixture()
        with patch.object(sys, "platform", "darwin"), \
                patch.object(shutil, "which") as lookup:
            with self.assertRaisesRegex(unittest.SkipTest, "Linux POSIX ACL"):
                fixture.require_linux_acl()
            lookup.assert_not_called()

    def test_missing_optional_tools_explicitly_skip_acl_coverage(self):
        for missing in ("getfacl", "setfacl"):
            with self.subTest(missing=missing):
                fixture = RetirementLedgerFixture()
                with patch.object(sys, "platform", "linux"), \
                        patch.object(shutil, "which", side_effect=lambda name:
                                     None if name == missing else "/optional/" + name):
                    with self.assertRaisesRegex(unittest.SkipTest, "missing optional " + missing):
                        fixture.require_linux_acl()

    def test_unsupported_filesystem_skips_without_attempting_another_write(self):
        for reason in (
            b"Operation not supported", b"Permission denied",
            b"Operation not permitted", b"Function not implemented",
        ):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as root:
                fixture = RetirementLedgerFixture()
                fixture.root = Path(root)
                with patch.object(sys, "platform", "linux"), \
                        patch.object(shutil, "which", side_effect=lambda name: "/optional/" + name), \
                        patch.object(fixture, "read_acl", return_value=b"unchanged ACL"), \
                        patch.object(fixture, "restore_acl") as restore, \
                        patch.object(fixture, "acl_command", return_value=subprocess.CompletedProcess(
                            [], 1, stdout=b"", stderr=reason,
                        )):
                    with self.assertRaisesRegex(unittest.SkipTest, "fixture filesystem"):
                        fixture.require_linux_acl()
                    restore.assert_not_called()

    def test_unexpected_acl_failure_is_not_a_skip(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = RetirementLedgerFixture()
            fixture.root = Path(root)
            with patch.object(sys, "platform", "linux"), \
                    patch.object(shutil, "which", side_effect=lambda name: "/optional/" + name), \
                    patch.object(fixture, "read_acl", return_value=b"unchanged ACL"), \
                    patch.object(fixture, "acl_command", return_value=subprocess.CompletedProcess(
                        [], 1, stdout=b"", stderr=b"unexpected tool failure",
                    )):
                with self.assertRaisesRegex(AssertionError, "ACL probe failed"):
                    fixture.require_linux_acl()


class HardLinkProbeTests(unittest.TestCase):
    def test_unsupported_hard_links_skip_explicitly(self):
        fixture = RetirementLedgerFixture()
        for error in (
            NotImplementedError("unavailable"),
            OSError(errno.ENOTSUP, "unsupported filesystem"),
            OSError(errno.EPERM, "unavailable privilege"),
            OSError(errno.EXDEV, "cross-device link"),
        ):
            with self.subTest(error=type(error).__name__, errno=getattr(error, "errno", None)), \
                    patch("os.link", side_effect=error):
                with self.assertRaisesRegex(unittest.SkipTest, "hard links"):
                    fixture.create_probe_hard_link(Path("source"), Path("alias"))

    def test_unexpected_hard_link_errors_are_not_skipped(self):
        fixture = RetirementLedgerFixture()
        error = OSError(errno.EIO, "unexpected I/O error")
        with patch("os.link", side_effect=error):
            with self.assertRaises(OSError) as raised:
                fixture.create_probe_hard_link(Path("source"), Path("alias"))
        self.assertIs(raised.exception, error)


class RetirementLedgerEnvironmentTests(unittest.TestCase):
    def test_nested_fixtures_restore_each_inherited_environment(self):
        inherited = {
            "GIT_CONFIG_COUNT": "3",
            "GIT_CONFIG_KEY_0": "gc.auto",
            "GIT_CONFIG_VALUE_0": "17",
            "GIT_CONFIG_KEY_1": "maintenance.auto",
            "GIT_CONFIG_VALUE_1": "true",
            "GIT_CONFIG_KEY_2": "test.inherited",
            "GIT_CONFIG_VALUE_2": "unrelated value with spaces",
        }
        base = {
            key: value for key, value in os.environ.items()
            if key != "GIT_CONFIG_COUNT"
            and not key.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))
        }
        with patch.dict(os.environ, {**base, **inherited}, clear=True):
            original = dict(os.environ)
            outer = RetirementLedgerFixture()
            try:
                outer.setUp()
                outer.assert_maintenance_is_isolated()
                outer_environment = dict(os.environ)
                self.assertEqual(outer_environment["GIT_CONFIG_COUNT"], "5")
                for key, value in inherited.items():
                    if key != "GIT_CONFIG_COUNT":
                        self.assertEqual(outer_environment[key], value)
                for index, (key, value) in enumerate(
                    (("gc.auto", "0"), ("maintenance.auto", "false")), start=3,
                ):
                    self.assertEqual(outer_environment[f"GIT_CONFIG_KEY_{index}"], key)
                    self.assertEqual(outer_environment[f"GIT_CONFIG_VALUE_{index}"], value)

                inner = RetirementLedgerFixture()
                try:
                    inner.setUp()
                    inner.assert_maintenance_is_isolated()
                    expected_inner = {
                        **outer_environment,
                        "GIT_CONFIG_COUNT": "7",
                        "GIT_CONFIG_KEY_5": "gc.auto",
                        "GIT_CONFIG_VALUE_5": "0",
                        "GIT_CONFIG_KEY_6": "maintenance.auto",
                        "GIT_CONFIG_VALUE_6": "false",
                    }
                    # Avoid exposing runner secrets in full-environment diffs.
                    self.assertTrue(
                        dict(os.environ) == expected_inner,
                        "Inner fixture did not preserve and extend the outer environment",
                    )
                    for fixture in (outer, inner):
                        self.assertEqual(
                            fixture.git("config", "--get", "test.inherited"),
                            inherited["GIT_CONFIG_VALUE_2"],
                        )
                finally:
                    inner.doCleanups()
                    self.assertTrue(
                        dict(os.environ) == outer_environment,
                        "Inner cleanup did not restore the exact outer environment",
                    )
                self.assertFalse(inner.root.exists())
                for index in (5, 6):
                    self.assertNotIn(f"GIT_CONFIG_KEY_{index}", os.environ)
                    self.assertNotIn(f"GIT_CONFIG_VALUE_{index}", os.environ)
                outer.assert_maintenance_is_isolated()
                self.assertEqual(
                    outer.git("config", "--get", "test.inherited"),
                    inherited["GIT_CONFIG_VALUE_2"],
                )
            finally:
                outer.doCleanups()
                self.assertTrue(
                    dict(os.environ) == original,
                    "Outer cleanup did not restore the original environment",
                )
            self.assertFalse(outer.root.exists())
            for index in range(3, 7):
                self.assertNotIn(f"GIT_CONFIG_KEY_{index}", os.environ)
                self.assertNotIn(f"GIT_CONFIG_VALUE_{index}", os.environ)

    def test_fixture_restores_environment_when_setup_fails(self):
        inherited = {
            "GIT_CONFIG_COUNT": "3",
            "GIT_CONFIG_KEY_0": "gc.auto",
            "GIT_CONFIG_VALUE_0": "17",
            "GIT_CONFIG_KEY_1": "maintenance.auto",
            "GIT_CONFIG_VALUE_1": "true",
            "GIT_CONFIG_KEY_2": "test.inherited",
            "GIT_CONFIG_VALUE_2": "unrelated value with spaces",
        }
        base = {
            key: value for key, value in os.environ.items()
            if key != "GIT_CONFIG_COUNT"
            and not key.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))
        }
        reached = []
        installed = {}

        class FailingSetupFixture(RetirementLedgerFixture):
            def runTest(self):
                reached.append("test body")

            def tearDown(self):
                reached.append("tearDown")

        def fail_after_environment_patch():
            installed.update(os.environ)
            raise RuntimeError("controlled fixture setup failure")

        with patch.dict(os.environ, {**base, **inherited}, clear=True):
            original = dict(os.environ)
            fixture = FailingSetupFixture()
            with patch.object(
                tempfile, "TemporaryDirectory", side_effect=fail_after_environment_patch,
            ) as create_temp:
                result = unittest.TextTestRunner(stream=io.StringIO()).run(
                    unittest.TestSuite([fixture]),
                )
            # Do not call doCleanups ourselves: the normal runner must restore
            # everything even though setUp never returned and tearDown was skipped.
            self.assertTrue(
                dict(os.environ) == original,
                "Failed fixture setup did not restore the original environment",
            )
            create_temp.assert_called_once_with()
            self.assertEqual(installed["GIT_CONFIG_COUNT"], "5")
            for key, value in inherited.items():
                if key != "GIT_CONFIG_COUNT":
                    self.assertEqual(installed[key], value)
            for index, (key, value) in enumerate(
                (("gc.auto", "0"), ("maintenance.auto", "false")), start=3,
            ):
                self.assertEqual(installed[f"GIT_CONFIG_KEY_{index}"], key)
                self.assertEqual(installed[f"GIT_CONFIG_VALUE_{index}"], value)
            self.assertEqual(reached, [])
            self.assertEqual(result.testsRun, 1)
            self.assertEqual(result.failures, [])
            self.assertEqual(result.skipped, [])
            self.assertEqual(len(result.errors), 1)
            failed_fixture, traceback = result.errors[0]
            self.assertIs(failed_fixture, fixture)
            self.assertIn(
                "RuntimeError: controlled fixture setup failure", traceback,
            )
            self.assertFalse(result.wasSuccessful())

    def test_fixture_preserves_inherited_config_and_restores_environment(self):
        inherited = {
            "GIT_CONFIG_COUNT": "3",
            "GIT_CONFIG_KEY_0": "gc.auto",
            "GIT_CONFIG_VALUE_0": "17",
            "GIT_CONFIG_KEY_1": "maintenance.auto",
            "GIT_CONFIG_VALUE_1": "true",
            "GIT_CONFIG_KEY_2": "test.inherited",
            "GIT_CONFIG_VALUE_2": "unrelated value with spaces",
        }
        # Keep other environment settings, but make these cases independent of
        # the runner's own counted Git configuration.
        base = {
            key: value for key, value in os.environ.items()
            if key != "GIT_CONFIG_COUNT"
            and not key.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))
        }
        for config in (inherited, {}):
            with self.subTest(inherited_config=bool(config)), \
                    patch.dict(os.environ, {**base, **config}, clear=True):
                original = dict(os.environ)
                fixture = RetirementLedgerFixture()
                try:
                    fixture.setUp()
                    fixture.assert_maintenance_is_isolated()
                    self.assertEqual(
                        os.environ["GIT_CONFIG_COUNT"],
                        str(int(config.get("GIT_CONFIG_COUNT", "0")) + 2),
                    )
                    for key, value in config.items():
                        if key != "GIT_CONFIG_COUNT":
                            self.assertEqual(os.environ[key], value)
                    if config:
                        self.assertEqual(
                            fixture.git("config", "--get", "test.inherited"),
                            inherited["GIT_CONFIG_VALUE_2"],
                        )
                        child = subprocess.run(
                            [sys.executable, "-c",
                             "import subprocess; "
                             "subprocess.run(['git', 'config', '--get', "
                             "'test.inherited'], check=True)"],
                            cwd=fixture.root, capture_output=True, text=True,
                        )
                        self.assertEqual(child.returncode, 0, child.stderr)
                        self.assertEqual(
                            child.stdout.strip(), inherited["GIT_CONFIG_VALUE_2"],
                        )
                finally:
                    fixture.doCleanups()
                    # Compare the complete environment without exposing runner
                    # secrets in an assertion diff if restoration regresses.
                    self.assertTrue(
                        dict(os.environ) == original,
                        "Fixture cleanup did not restore the original environment",
                    )
                self.assertFalse(fixture.root.exists())


class RetirementLedgerTests(RetirementLedgerFixture):
    def test_history_snapshot_detects_checkout_and_git_acl_changes(self):
        baseline = self.git("rev-parse", "HEAD")
        self.assert_history_acls_detected(
            lambda: self.assert_cached_history_equivalent(baseline),
        )

    def test_history_snapshot_detects_identical_hard_link_replacement(self):
        baseline = self.git("rev-parse", "HEAD")
        self.assert_history_hard_link_changes_detected(
            lambda: self.assert_cached_history_equivalent(baseline),
        )

    def test_snapshot_preserves_self_referential_link_without_following(self):
        self.assert_snapshot_link_boundary("self")

    def test_snapshot_preserves_directory_link_cycle_without_following(self):
        self.assert_snapshot_link_boundary("cycle")

    def test_snapshot_never_traverses_or_reads_external_link_targets(self):
        self.assert_snapshot_link_boundary("external")

    def test_history_snapshot_detects_file_and_symlink_replacements(self):
        baseline = self.git("rev-parse", "HEAD")
        self.assert_history_link_type_changes_detected(
            lambda: self.assert_cached_history_equivalent(baseline),
        )

    def test_history_snapshot_detects_checkout_and_git_permission_changes(self):
        baseline = self.git("rev-parse", "HEAD")
        self.assert_history_modes_detected(
            lambda: self.assert_cached_history_equivalent(baseline),
        )

    def test_history_snapshot_detects_link_creation_removal_and_retargeting(self):
        baseline = self.git("rev-parse", "HEAD")
        self.assert_history_link_changes_detected(
            lambda: self.assert_cached_history_equivalent(baseline),
        )

    def test_fixture_disables_only_process_scoped_automatic_maintenance(self):
        self.assert_maintenance_is_isolated()

    def test_history_snapshot_detects_checkout_and_git_writes(self):
        baseline = self.git("rev-parse", "HEAD")
        self.assert_history_writes_detected(
            lambda: self.assert_cached_history_equivalent(baseline),
        )

    def test_complete_retirement_retains_exact_committed_decision(self):
        result, data = self.assert_read_only_retirement_check(self.preflight)
        self.assertEqual(result.returncode, 0, data)
        self.git("update-ref", "-d", self.ref, self.sha)
        result, data = self.assert_read_only_retirement_check(self.verify)
        self.assertEqual(result.returncode, 0, data)
        guard = data["recovery_guard"]
        self.assertTrue(guard["passed"])
        self.assertEqual(
            guard["retirement_ledger"]["decisions"],
            [{"record_file": self.record_path, **self.record}],
        )
        self.snapshot.unlink()
        self.assertEqual(json.loads((self.root / self.record_path).read_text()), self.record)
        # Old records remain valid even after their ref has been retired.
        result, data = self.cli("--validate-retirement-ledger", self.policy_path)
        self.assertEqual(result.returncode, 0, data)

    def test_git_clean_filters_accept_unchanged_crlf_records(self):
        self.git("config", "core.autocrlf", "true")
        for relative, data in ((self.policy_path, self.policy), (self.record_path, self.record)):
            (self.root / relative).write_bytes((json.dumps(data, indent=2) + "\n").encode("utf-8"))
        self.commit()
        for relative in (self.policy_path, self.record_path):
            path = self.root / relative
            path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
        result, data = self.preflight()
        self.assertEqual(result.returncode, 0, data)
        self.record["approver"] = "uncommitted-owner"
        self.write(self.record_path, self.record)
        result, data = self.assert_read_only_retirement_check(self.preflight)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("committed unchanged", data["error"])

    def test_flag_without_ledger_cannot_complete_retirement(self):
        self.git("update-ref", "-d", self.ref, self.sha)
        result, data = self.assert_read_only_retirement_check(lambda: self.cli(
            "--verify-recovery", str(self.snapshot),
            "--approve-recovery-retirement", self.allowance(),
        ))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--retirement-ledger", data["error"])

    def test_missing_ledger_blocks_verification(self):
        (self.root / self.policy_path).unlink()
        result, data = self.assert_read_only_retirement_check(self.verify)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("error", data)

    def test_missing_record_blocks_completion(self):
        (self.root / self.record_path).unlink()
        self.commit()
        result, data = self.assert_read_only_retirement_check(self.preflight)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no retained decision", data["error"])

    def test_modified_or_uncommitted_record_is_not_retained_evidence(self):
        self.record["approver"] = "different-owner"
        self.write(self.record_path, self.record)
        result, data = self.assert_read_only_retirement_check(self.preflight)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("committed unchanged", data["error"])
        self.git("rm", "--cached", self.record_path)
        self.git("commit", "-m", "Untrack record")
        result, data = self.assert_read_only_retirement_check(self.preflight)
        self.assertNotEqual(result.returncode, 0)

    def test_bad_record_fields_fail_without_echoing_unsafe_content(self):
        original = dict(self.record)
        changes = [
            {"protected_commit": "abc123"},
            {"protected_commit": "f" * 40},
            {"ref": "refs/heads/main"},
            {"ref": "refs/recovery/invalid ref"},
            {"evidence": ""},
            {"decision": "delete"},
            {"decision_date": "2026-02-30"},
            {"decision_date": "2026-10-01"},
            {"approver": ""},
            {"format": True},
            {"evidence": "token=synthetic-private-value"},
            {"evidence": "https://example.invalid/path?signature=synthetic-value"},
            {"password": "synthetic-private-value"},
        ]
        for change in changes:
            with self.subTest(change=change):
                self.record = {**original, **change}
                self.write(self.record_path, self.record)
                self.commit()
                result, data = self.assert_read_only_retirement_check(self.preflight)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("error", data)
                self.assertNotIn("synthetic-private-value", result.stdout)

    def test_invalid_policy_dates_and_unapproved_format_fail(self):
        for change in (
            {"approved_on": "October 2"}, {"approved_by": ""},
            {"format": 2}, {"records_directory": "../outside"},
            {"records_directory": "/tmp/decisions"},
            {"records_directory": ".git/decisions"},
            {"records_directory": ".scratch/decisions"},
        ):
            with self.subTest(change=change):
                self.write(self.policy_path, {**self.policy, **change})
                self.commit()
                result, data = self.assert_read_only_retirement_check(self.preflight)
                self.assertNotEqual(result.returncode, 0)

    def test_duplicate_records_and_duplicate_json_keys_fail(self):
        self.write(self.records_path + "/duplicate.json", self.record)
        self.commit()
        result, data = self.assert_read_only_retirement_check(self.preflight)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("duplicate", data["error"])
        (self.root / (self.records_path + "/duplicate.json")).unlink()
        path = self.root / self.record_path
        path.write_text('{"format": 1, "format": 1}', encoding="utf-8")
        self.commit()
        result, data = self.assert_read_only_retirement_check(self.preflight)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("duplicate", data["error"])

    def test_malformed_utf8_and_json_fail_visibly(self):
        for content in (b"{invalid}", b"\xff"):
            (self.root / self.record_path).write_bytes(content)
            self.commit()
            result, data = self.assert_read_only_retirement_check(self.preflight)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("UTF-8 JSON", data["error"])

    def test_symlinked_record_is_not_retained_evidence(self):
        record = self.root / self.record_path
        copy = self.root / "copy.json"
        record.rename(copy)
        try:
            record.symlink_to(copy)
        except OSError as exc:
            if exc.errno in (1, 13) or getattr(exc, "winerror", None) == 1314:
                self.skipTest("symlink creation requires unavailable privileges")
            raise
        self.commit()
        result, data = self.assert_read_only_retirement_check(self.preflight)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("symlink", data["error"])

    def test_mismatched_evidence_blocks_completion(self):
        self.git("update-ref", "-d", self.ref, self.sha)
        result, data = self.assert_read_only_retirement_check(
            lambda: self.verify(self.ref + "=different evidence"),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("evidence differs", data["error"])

    def test_guard_still_blocks_other_removed_refs_and_still_present_ref(self):
        result, data = self.assert_read_only_retirement_check(self.verify)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(data["recovery_guard"]["passed"])
        self.git("update-ref", "refs/remotes/origin/retained", self.sha)
        self.snapshot.unlink()
        self.cli("--snapshot-recovery", str(self.snapshot))
        self.git("update-ref", "-d", self.ref, self.sha)
        self.git("update-ref", "-d", "refs/remotes/origin/retained")
        result, data = self.assert_read_only_retirement_check(self.verify)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refs/remotes/origin/retained", data["recovery_guard"]["unexpected_removed_refs"])

    def test_duplicate_allowances_fail_and_snapshot_is_not_overwritten(self):
        self.git("update-ref", "-d", self.ref, self.sha)
        result, data = self.assert_read_only_retirement_check(lambda: self.cli(
            "--verify-recovery", str(self.snapshot), "--retirement-ledger", self.policy_path,
            "--approve-recovery-retirement", self.allowance(),
            "--approve-recovery-retirement", self.allowance(),
        ))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("duplicate", data["error"])
        old = self.snapshot.read_bytes()
        result, data = self.assert_read_only_retirement_check(
            lambda: self.cli("--snapshot-recovery", str(self.snapshot)),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(old, self.snapshot.read_bytes())

    def test_ledger_does_not_excuse_lost_reachable_objects(self):
        self.git("checkout", "-b", "unique")
        self.git("commit", "--allow-empty", "-m", "Unique protected work")
        unique = self.git("rev-parse", "HEAD")
        self.git("checkout", "main")
        self.git("branch", "-D", "unique")
        self.git("update-ref", self.ref, unique)
        self.record["protected_commit"] = unique
        self.write(self.record_path, self.record)
        self.commit()
        self.snapshot.unlink()
        self.cli("--snapshot-recovery", str(self.snapshot))
        self.git("update-ref", "-d", self.ref, unique)
        result, data = self.assert_read_only_retirement_check(self.verify)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(unique, data["recovery_guard"]["unreachable_objects"])

    def test_recovery_verification_is_read_only(self):
        before = self.git("show-ref")
        result, data = self.assert_read_only_retirement_check(self.preflight)
        self.assertEqual(result.returncode, 0, data)
        self.assertEqual(before, self.git("show-ref"))

        self.git("update-ref", "-d", self.ref, self.sha)
        before = self.git("show-ref")
        result, data = self.assert_read_only_retirement_check(self.verify)
        self.assertEqual(result.returncode, 0, data)
        self.assertTrue(data["recovery_guard"]["passed"])
        self.assertEqual(before, self.git("show-ref"))

    def history(self, baseline, *args):
        return self.cli(
            "--audit-retirement-history", self.policy_path,
            "--ledger-baseline", baseline, *args,
        )

    def assert_cached_history_equivalent(self, baseline, migrations=()):
        ledger = audit.retirement_ledger

        before = self.repository_bytes()
        cached = ledger.audit_history(self.root, self.policy_path, baseline, migrations)
        self.assertEqual(self.repository_bytes(), before)

        def uncached(reader, tree, policy_path):
            return ledger.tree_ledger(reader.root, tree, policy_path)

        with patch.object(ledger.HistoryLedgerReader, "read", uncached):
            reference = ledger.audit_history(self.root, self.policy_path, baseline, migrations)
        self.assertEqual(self.repository_bytes(), before)
        self.assertEqual(json.dumps(cached, sort_keys=True),
                         json.dumps(reference, sort_keys=True))
        return cached

    def test_long_history_reads_each_immutable_blob_once(self):
        # Representative benchmark: 81 decisions and 1,000 unrelated files,
        # followed by 120 commits changing only unrelated content.
        for index in range(80):
            self.write(self.records_path + f"/retained-{index}.json", {
                **self.record, "ref": f"refs/recovery/retained-{index}",
            })
        directory = self.root / "content"
        directory.mkdir()
        for index in range(1000):
            (directory / f"page-{index}.txt").write_text("Content\n", encoding="utf-8")
        self.git("add", "content")
        self.commit()
        baseline = self.git("rev-parse", "HEAD")
        for index in range(120):
            (directory / "page-0.txt").write_text(f"Unrelated edit {index}\n", encoding="utf-8")
            self.git("add", "content")
            self.commit()
        ledger = audit.retirement_ledger
        refs, status = self.git("show-ref"), self.git("status", "--porcelain")
        index_bytes = (self.root / ".git/index").read_bytes()
        with patch.object(ledger, "history_git", wraps=ledger.history_git) as reads, \
                patch.object(ledger, "validate_record", wraps=ledger.validate_record) as validations:
            start = time.perf_counter()
            result = ledger.audit_history(self.root, self.policy_path, baseline)
            elapsed = time.perf_counter() - start
        counts = Counter(call.args[1] for call in reads.call_args_list)
        self.assertEqual(counts["cat-file"], 82)
        self.assertEqual(counts["ls-tree"], 121)
        self.assertEqual(validations.call_count, 81)
        self.assertEqual(result, {
            "passed": True, "baseline": baseline, "head": self.git("rev-parse", "HEAD"),
            "history_scope": "first-parent", "commits_checked": 121,
            "retained_record_count": 81, "added_record_count": 0,
            "policy_file": self.policy_path, "approved_migrations": [], "holds": [],
        })
        self.assertEqual((self.root / ".git/index").read_bytes(), index_bytes)
        self.assertEqual(self.git("show-ref"), refs)
        self.assertEqual(self.git("status", "--porcelain"), status)
        print(f"\nRetirement history benchmark: {elapsed:.3f}s; "
              f"{counts['cat-file']} blob reads; {validations.call_count} record validations")

    def test_wide_stable_history_avoids_cyclic_cache_eviction(self):
        ledger = audit.retirement_ledger
        # Replaying the old cyclic eviction costs several minutes. Keep that
        # real-Git baseline reproducible without exhausting routine QA budgets.
        lru_baseline = os.environ.get("RETIREMENT_LEDGER_WIDE_LRU_BASELINE") == "1"
        original_init = ledger.HistoryLedgerReader.__init__

        def reference_init(reader, root):
            original_init(reader, root)
            if lru_baseline:
                reader.blobs = ledger.HistoryObjectCache(maxsize=128)
                reader.validated_records = ledger.HistoryObjectCache(maxsize=1024)
                reader.state = lru_cache(maxsize=32)(reader._state)
            else:
                reader.blobs = {}
                reader.validated_records = set()

        limits = ledger.HistoryLedgerReader(self.root)
        record_count = max(limits.blobs.maxsize, limits.validated_records.maxsize) + 1
        for number in range(record_count - 1):
            self.write(f"{self.records_path}/wide-{number:04d}.json", {
                **self.record, "ref": f"refs/recovery/wide-{number:04d}",
            })
        self.commit()
        baseline = self.git("rev-parse", "HEAD")
        # Exceed the snapshot cache too: every state has a distinct root tree,
        # but all decision blobs remain unchanged throughout the review.
        for revision in range(40):
            (self.root / "unrelated.txt").write_text(f"Edit {revision}\n", encoding="utf-8")
            self.git("add", "unrelated.txt")
            self.commit()
        (self.root / self.policy_path).write_text("Dirty checkout policy\n", encoding="utf-8")

        def fingerprint():
            return {
                path.relative_to(self.root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in self.root.rglob("*") if path.is_file() and not path.is_symlink()
            } | self.repository_links()

        def lru_only_state(reader, tree, policy_path):
            # Without current-ledger reuse: either the slow pre-fix LRUs or
            # unbounded immutable reuse as a fast full-JSON control.
            try:
                return ledger.tree_ledger(
                    reader.root, tree, policy_path, blobs=reader.blobs,
                    validated_records=reader.validated_records,
                )
            except ledger.LedgerError as exc:
                return str(exc)

        def measure(state, init):
            counts = Counter()
            original_git, original_validate = ledger.history_git, ledger.validate_record
            original_read = ledger.HistoryLedgerReader.read

            def observe(reader, *args):
                result = original_read(reader, *args)
                sample_history_accounting(reader, counts)
                return result

            def read(root, *args):
                counts[args[0]] += 1
                return original_git(root, *args)

            def validate(*args):
                counts["validations"] += 1
                return original_validate(*args)

            gc.collect()
            tracemalloc.start()
            start = time.perf_counter()
            try:
                with patch.object(ledger.HistoryLedgerReader, "__init__", init), \
                        patch.object(ledger.HistoryLedgerReader, "_state", state), \
                        patch.object(ledger.HistoryLedgerReader, "read", observe), \
                        patch.object(ledger, "history_git", read), \
                        patch.object(ledger, "validate_record", validate):
                    result = ledger.audit_history(self.root, self.policy_path, baseline)
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            elapsed = time.perf_counter() - start
            gc.collect()
            return result, peak, counts, elapsed

        before = fingerprint()
        reference, old_peak, old_counts, old_time = measure(lru_only_state, reference_init)
        self.assertEqual(fingerprint(), before)
        result, peak, counts, elapsed = measure(ledger.HistoryLedgerReader._state, original_init)
        self.assertEqual(fingerprint(), before)
        self.assertEqual(json.dumps(result, sort_keys=True), json.dumps(reference, sort_keys=True))
        self.assertEqual(result, {
            "passed": True, "baseline": baseline, "head": self.git("rev-parse", "HEAD"),
            "history_scope": "first-parent", "commits_checked": 41,
            "retained_record_count": record_count, "added_record_count": 0,
            "policy_file": self.policy_path, "approved_migrations": [], "holds": [],
        })
        for label, memory, reads, seconds in (
            ("LRU-only" if lru_baseline else "unbounded control",
             old_peak, old_counts, old_time),
            ("current-ledger", peak, counts, elapsed),
        ):
            print(f"\nWide stable {label}: {record_count} records, 41 states; "
                  f"{memory / 1024**2:.2f} MiB peak Python heap; "
                  f"{reads['payload_peak_bytes'] / 1024**2:.2f} MiB payload; "
                  f"{reads['bookkeeping_peak_bytes'] / 1024**2:.2f} MiB bookkeeping; "
                  f"{reads['cat-file']} blob reads "
                  f"({reads['cat-file'] / (record_count + 1):.2f}x unique blobs); "
                  f"{reads['validations']} validations "
                  f"({reads['validations'] / record_count:.2f}x unique records); "
                  f"{seconds:.3f}s")
        amplification = 41 if lru_baseline else 1
        self.assertEqual(old_counts["cat-file"], (record_count + 1) * amplification)
        self.assertEqual(old_counts["validations"], record_count * amplification)
        self.assertEqual(counts["cat-file"], record_count + 1)
        self.assertEqual(counts["validations"], record_count)
        self.assertEqual(counts["ls-tree"], 41)
        if lru_baseline:
            self.assertLess(peak, old_peak * 0.5)

    def test_alternating_wide_ledgers_avoid_scan_amplification(self):
        ledger = audit.retirement_ledger
        self.assertEqual(self.git("rev-parse", "--show-object-format"), self.object_format)
        oid_pattern = r"^[0-9a-f]{%d}$" % (64 if self.object_format == "sha256" else 40)
        self.assertRegex(self.sha, oid_pattern)
        # Like the stable-wide case, make the several-minute LRU-only replay
        # opt-in. Routine QA still compares complete JSON with a reference
        # reader that has no working-set reuse, using unbounded object caches.
        lru_baseline = os.environ.get("RETIREMENT_LEDGER_ALTERNATING_LRU_BASELINE") == "1"
        original_init = ledger.HistoryLedgerReader.__init__

        def reference_init(reader, root):
            original_init(reader, root)
            if lru_baseline:
                reader.blobs = ledger.HistoryObjectCache(maxsize=128)
                reader.validated_records = ledger.HistoryObjectCache(maxsize=1024)
                reader.state = lru_cache(maxsize=32)(reader._state)
            else:
                reader.blobs = {}
                reader.validated_records = set()

        limits = ledger.HistoryLedgerReader(self.root)
        record_count = max(limits.blobs.maxsize, limits.validated_records.maxsize) + 1
        state_count = 34  # Both sets recur after the 32 root-snapshot slots fill.
        self.assertGreater(record_count, limits.blobs.maxsize)
        self.assertGreater(record_count, limits.validated_records.maxsize)
        self.assertGreater(state_count, limits.state.cache_info().maxsize)
        records = {
            self.record_path: self.record,
            **{
                f"{self.records_path}/alternate-{number:04d}.json": {
                    **self.record, "ref": f"refs/recovery/alternate-{number:04d}",
                }
                for number in range(record_count - 1)
            },
        }
        versions = []
        for label in ("A", "B"):
            for path, record in records.items():
                self.write(path, {**record, "evidence": f"Decision set {label}."})
            self.commit()
            versions.append(self.git("rev-parse", "HEAD"))
        # Reuse the decision subtrees, but never the root tree, at each commit.
        # This avoids making fixture-writing cost part of the measured audit.
        self.git("read-tree", versions[0])
        self.git("commit", "-m", "Start alternating ledger review")
        baseline = self.git("rev-parse", "HEAD")
        alternate_commits = []
        trees = {self.git("rev-parse", "HEAD^{tree}")}
        for revision in range(1, state_count):
            self.git("read-tree", versions[revision % 2])
            (self.root / "unrelated.txt").write_text(f"Edit {revision}\n", encoding="utf-8")
            self.git("add", "unrelated.txt")
            self.git("commit", "-m", f"Alternate ledger {revision}")
            trees.add(self.git("rev-parse", "HEAD^{tree}"))
            if revision % 2:
                alternate_commits.append(self.git("rev-parse", "HEAD"))
        self.assertEqual(len(trees), state_count)
        for oid in (baseline, *trees, *alternate_commits):
            self.assertRegex(oid, oid_pattern)
        # Historical reads must ignore dirty checkout bytes, including policy.
        (self.root / self.policy_path).write_text("Dirty policy\n", encoding="utf-8")

        def fingerprint():
            # Hash all checkout and Git metadata bytes, and retain modes, empty
            # directories and symlinks from the full immutability helper.
            return {
                path: (value[0], value[1], hashlib.sha256(value[2]).hexdigest())
                if value[0] == "file" else value
                for path, value in self.repository_bytes().items()
            }

        def lru_only_state(reader, tree, policy_path):
            try:
                return ledger.tree_ledger(
                    reader.root, tree, policy_path, blobs=reader.blobs,
                    validated_records=reader.validated_records,
                )
            except ledger.LedgerError as exc:
                return str(exc)

        def measure(state, init, *, bounded=False):
            counts = Counter()
            original_git, original_validate = ledger.history_git, ledger.validate_record
            original_read = ledger.HistoryLedgerReader.read

            def read(root, *args):
                counts[args[0]] += 1
                if args[0] == "cat-file":
                    self.assertRegex(args[2], oid_pattern)
                return original_git(root, *args)

            def validate(root, record):
                counts["validations"] += 1
                self.assertEqual(record["protected_commit"], self.sha)
                return original_validate(root, record)

            def observe(reader, tree, policy_path):
                self.assertRegex(tree, oid_pattern)
                result = original_read(reader, tree, policy_path)
                sample_history_accounting(reader, counts)
                counts["states"] += 1
                if bounded:
                    # Check every scan, not just the final cache sizes: neither
                    # historical reuse nor the two live sets may accumulate.
                    self.assertLessEqual(len(reader.blobs), limits.blobs.maxsize)
                    self.assertLessEqual(len(reader.validated_records),
                                         limits.validated_records.maxsize)
                    self.assertLessEqual(reader.state.cache_info().currsize,
                                         limits.state.cache_info().maxsize)
                    self.assertGreaterEqual(reader.reuse_budget.used_bytes, 0)
                    self.assertLessEqual(reader.reuse_budget.used_bytes,
                                         reader.reuse_budget.maxbytes)
                    self.assertEqual(len(reader.current_blobs), record_count + 1)
                    self.assertEqual(len(reader.current_validations), record_count)
                    self.assertEqual(len(reader.previous_blobs),
                                     record_count + 1 if counts["states"] > 1 else 0)
                    self.assertEqual(len(reader.previous_validations),
                                     record_count if counts["states"] > 1 else 0)
                    self.assertFalse(reader.failed_blobs)
                    self.assertFalse(reader.failed_validations)
                return result

            gc.collect()
            tracemalloc.start()
            start = time.perf_counter()
            try:
                with patch.object(ledger.HistoryLedgerReader, "__init__", init), \
                        patch.object(ledger.HistoryLedgerReader, "_state", state), \
                        patch.object(ledger.HistoryLedgerReader, "read", observe), \
                        patch.object(ledger, "history_git", read), \
                        patch.object(ledger, "validate_record", validate):
                    result = ledger.audit_history(self.root, self.policy_path, baseline)
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            gc.collect()
            return result, peak, counts, time.perf_counter() - start

        before = fingerprint()
        reference, old_peak, old_counts, old_time = measure(lru_only_state, reference_init)
        self.assertEqual(fingerprint(), before)
        result, peak, counts, elapsed = measure(
            ledger.HistoryLedgerReader._state, original_init, bounded=True,
        )
        self.assertEqual(fingerprint(), before)
        self.assertEqual(json.dumps(result, sort_keys=True), json.dumps(reference, sort_keys=True))
        self.assertEqual(result, {
            "passed": False, "baseline": baseline, "head": self.git("rev-parse", "HEAD"),
            "history_scope": "first-parent", "commits_checked": state_count,
            "retained_record_count": record_count, "added_record_count": 0,
            "policy_file": self.policy_path, "approved_migrations": [],
            "holds": [
                {"commit": commit, "ref": records[path]["ref"],
                 "reason": "rewritten-record", "changed_fields": ["evidence"]}
                for commit in alternate_commits for path in sorted(records)
            ],
        })
        unique_blobs, unique_records = 2 * record_count + 1, 2 * record_count
        for label, memory, reads, seconds in (
            ("LRU-only" if lru_baseline else "unbounded control",
             old_peak, old_counts, old_time),
            ("two-scan", peak, counts, elapsed),
        ):
            print(f"\nAlternating wide {self.object_format} {label}: 2 x {record_count} records, "
                  f"{state_count} distinct root trees; "
                  f"{memory / 1024**2:.2f} MiB peak Python heap; "
                  f"{reads['payload_peak_bytes'] / 1024**2:.2f} MiB payload; "
                  f"{reads['bookkeeping_peak_bytes'] / 1024**2:.2f} MiB bookkeeping; "
                  f"{reads['cat-file']} blob reads "
                  f"({reads['cat-file'] / unique_blobs:.2f}x unique blobs); "
                  f"{reads['validations']} validations "
                  f"({reads['validations'] / unique_records:.2f}x unique records); "
                  f"{seconds:.3f}s")
        self.assertEqual(old_counts["cat-file"],
                         state_count * (record_count + 1) if lru_baseline else unique_blobs)
        self.assertEqual(old_counts["validations"],
                         state_count * record_count if lru_baseline else unique_records)
        self.assertEqual(counts["cat-file"], unique_blobs)
        self.assertEqual(counts["validations"], unique_records)
        self.assertEqual(counts["ls-tree"], state_count)
        self.assertEqual(old_counts["ls-tree"], state_count)
        self.assertEqual(counts["states"], state_count)
        self.assertEqual(old_counts["states"], state_count)
        if lru_baseline:
            self.assertLess(peak, old_peak * 0.8)

    def test_recurring_wide_ledgers_reuse_blobs_across_approved_moves(self):
        ledger = audit.retirement_ledger
        limits = ledger.HistoryLedgerReader(self.root)
        width = max(limits.blobs.maxsize, limits.validated_records.maxsize) + 1
        original_init = ledger.HistoryLedgerReader.__init__
        original_read = ledger.HistoryLedgerReader.read
        original_git, original_validate = ledger.history_git, ledger.validate_record
        moved_policy = "governance/retained/policy.json"
        moved_directory = "governance/retained/decisions"
        locations = (
            (self.policy_path, self.records_path),
            (moved_policy, self.records_path),  # Same immutable policy blob.
            (moved_policy, moved_directory),  # New policy, identical decisions.
        )
        records = [
            {**self.record, "ref": f"refs/recovery/move-{number:04d}"}
            for number in range(width)
        ]
        versions = {}
        self.git("rm", self.record_path)
        for stage, (policy_path, directory) in enumerate(locations):
            if stage:
                self.git("rm", "-r", "governance")
            for label in ("A", "B"):
                self.write(policy_path, {**self.policy, "records_directory": directory})
                for number, record in enumerate(records):
                    self.write(f"{directory}/{number:04d}.json", {
                        **record, "evidence": f"Decision set {label}.",
                    })
                self.commit()
                versions[stage, label] = self.git("rev-parse", "HEAD")
        # Small third/fourth sets let us prove replacement, not merely a
        # bounded length while the same two wide sets recur indefinitely.
        cold = []
        for label in ("C", "D"):
            self.git("rm", "-r", moved_directory)
            self.write(f"{moved_directory}/0000.json", {
                **records[0], "evidence": f"Decision set {label}.",
            })
            self.commit()
            cold.append(self.git("rev-parse", "HEAD^{tree}"))

        expected_holds, approved, allowances, scans = [], [], [], []

        def append(stage, label, *, migration=False, invalid=None, executable=False):
            policy_path, directory = locations[stage]
            self.git("read-tree", versions[stage, label])
            if invalid:
                mode, path, target = invalid
                oid = self.git("rev-parse", f":{path}")
                self.git("update-index", "--force-remove", path)
                self.git("update-index", "--add", "--cacheinfo", f"{mode},{oid},{target}")
            if executable:
                # Both regular-file modes must remain accepted after warming.
                for path in (policy_path, f"{directory}/0000.json"):
                    oid = self.git("rev-parse", f":{path}")
                    self.git("update-index", "--cacheinfo", f"100755,{oid},{path}")
            (self.root / "unrelated.txt").write_text(f"State {len(scans)}\n", encoding="utf-8")
            self.git("add", "unrelated.txt")
            self.git("commit", "-m", f"Recurring migration state {len(scans)}")
            commit = self.git("rev-parse", "HEAD")
            scans.append((self.git("rev-parse", "HEAD^{tree}"), policy_path))
            if migration:
                previous_policy = locations[stage - 1][0]
                allowances.append(f"{commit}={previous_policy},{policy_path}")
                approved.append({
                    "commit": commit, "from_policy": previous_policy,
                    "to_policy": policy_path, "status": "owner-approved-policy-migration",
                })
            if invalid:
                detail = (
                    "retirement history: records directory must contain only JSON records"
                    if invalid[1] != invalid[2] else
                    "retirement history: policy or record is missing or not a regular blob"
                )
                expected_holds.append({
                    "commit": commit, "reason": "invalid-ledger", "detail": detail,
                })
            elif label == "B" and len(scans) > 1:
                expected_holds.extend({
                    "commit": commit, "ref": record["ref"], "reason": "rewritten-record",
                    "changed_fields": ["evidence"],
                } for record in records)
            return commit

        baseline = append(0, "A")
        # Force distinct root trees past the snapshot entry limit before moving.
        for revision in range(1, limits.state.cache_info().maxsize + 2):
            append(0, "B" if revision % 2 else "A")
        append(1, "B", migration=True)  # Move without changing the warmed B set.
        append(1, "A")
        append(1, "B")
        append(2, "B", migration=True)  # Change only the policy/directory.
        append(2, "A")
        append(2, "B", executable=True)
        for invalid in (
            ("120000", f"{moved_directory}/0000.json", f"{moved_directory}/0000.json"),
            ("100644", f"{moved_directory}/0000.json", f"{moved_directory}/nested/0000.json"),
            ("120000", moved_policy, moved_policy),
        ):
            append(2, "B", invalid=invalid)
            append(2, "B")
        head = self.git("rev-parse", "HEAD")
        self.assertEqual(len({tree for tree, _ in scans}), len(scans))
        # All audit readers must ignore these checkout bytes and leave the
        # saved recovery snapshot, index, refs, modes and metadata untouched.
        self.write(moved_policy, {"dirty": True})
        self.write(f"{moved_directory}/0000.json", {"dirty": True})

        def fingerprint():
            return {
                path: (value[0], value[1], hashlib.sha256(value[2]).hexdigest(), *value[3:])
                if value[0] == "file" else value
                for path, value in self.repository_bytes().items()
            }

        def measure(reference, approvals):
            counts, readers = Counter(), []

            def initialize(reader, root):
                original_init(reader, root)
                readers.append(reader)
                if reference:
                    reader.blobs, reader.validated_records = {}, set()

            def read(root, *args):
                counts[args[0]] += 1
                return original_git(root, *args)

            def validate(*args):
                counts["validations"] += 1
                return original_validate(*args)

            def observe(reader, tree, policy_path):
                counts["states"] += 1
                if reference:
                    # Independent of both snapshot and working-set reuse.
                    return ledger.tree_ledger(
                        reader.root, tree, policy_path, blobs=reader.blobs,
                        validated_records=reader.validated_records,
                    )
                try:
                    return original_read(reader, tree, policy_path)
                finally:
                    for name in ("current", "previous", "failed"):
                        self.assertLessEqual(len(getattr(reader, name + "_blobs")), width + 1)
                        self.assertLessEqual(len(getattr(reader, name + "_validations")), width)
                    self.assertLessEqual(len(reader.blobs), limits.blobs.maxsize)
                    self.assertLessEqual(len(reader.validated_records),
                                         limits.validated_records.maxsize)
                    self.assertLessEqual(reader.state.cache_info().currsize,
                                         limits.state.cache_info().maxsize)
                    self.assertGreaterEqual(reader.reuse_budget.used_bytes, 0)
                    self.assertLessEqual(reader.reuse_budget.used_bytes,
                                         reader.reuse_budget.maxbytes)

            start = time.perf_counter()
            with patch.object(ledger.HistoryLedgerReader, "__init__", initialize), \
                    patch.object(ledger.HistoryLedgerReader, "read", observe), \
                    patch.object(ledger, "history_git", read), \
                    patch.object(ledger, "validate_record", validate):
                try:
                    result = ledger.audit_history(self.root, self.policy_path, baseline, approvals)
                except ledger.LedgerError as exc:
                    result = str(exc)
            print(f"\nRecurring wide moves {self.object_format} "
                  f"{'reference' if reference else 'two-scan'}: "
                  f"2 x {width} records; {counts['states']} states; "
                  f"{counts['cat-file']} blob reads; {counts['validations']} validations; "
                  f"{time.perf_counter() - start:.3f}s")
            return result, counts, readers[0]

        before = fingerprint()
        reference, reference_counts, _ = measure(True, allowances)
        self.assertEqual(fingerprint(), before)
        result, counts, reader = measure(False, allowances)
        self.assertEqual(fingerprint(), before)
        self.assertEqual(json.dumps(result, sort_keys=True), json.dumps(reference, sort_keys=True))
        self.assertEqual(result, {
            "passed": False, "baseline": baseline, "head": head,
            "history_scope": "first-parent", "commits_checked": len(scans),
            "retained_record_count": width, "added_record_count": 0,
            "policy_file": moved_policy, "approved_migrations": approved,
            "holds": expected_holds,
        })
        for measured in (reference_counts, counts):
            self.assertEqual(measured["cat-file"], 2 * width + 2)
            self.assertEqual(measured["validations"], 2 * width)
            self.assertEqual(measured["ls-tree"], len(scans))
            self.assertEqual(measured["states"], len(scans))

        # The final restored state is warm, but an extra approval for that
        # unchanged policy must still fail rather than being accepted on a hit.
        stale = allowances + [f"{head}={moved_policy},{moved_policy}"]
        for control in (True, False):
            failure, _, _ = measure(control, stale)
            self.assertEqual(failure,
                             "retirement history: migration approval does not identify a policy change")
            self.assertEqual(fingerprint(), before)

        # Eliminate optional historical reuse to inspect the live-set contract
        # directly. A and B must become cold after C and D, not accumulate.
        reader.blobs = ledger.HistoryObjectCache(maxsize=0)
        reader.validated_records = ledger.HistoryObjectCache(maxsize=0)
        reader.failed_blobs, reader.failed_validations = {}, {}
        with patch.object(ledger, "history_git", wraps=original_git) as reads, \
                patch.object(ledger, "validate_record", wraps=original_validate) as validations:
            for tree in cold:
                self.assertNotIsInstance(reader._state(tree, moved_policy), str)
            self.assertEqual(len(reader.current_validations), 1)
            self.assertEqual(len(reader.previous_validations), 1)
            self.assertEqual(len(reader.current_blobs), 2)
            self.assertEqual(len(reader.previous_blobs), 2)
            reads.reset_mock()
            validations.reset_mock()
            tree = self.git("rev-parse", f"{versions[2, 'A']}^{{tree}}")
            self.assertNotIsInstance(reader._state(tree, moved_policy), str)
            self.assertEqual(validations.call_count, width)
            self.assertEqual(Counter(call.args[1] for call in reads.call_args_list)["cat-file"], width)
        self.assertEqual(fingerprint(), before)

    def test_wide_invalid_ledger_reuses_valid_prefix_without_losing_holds(self):
        ledger = audit.retirement_ledger
        original_init = ledger.HistoryLedgerReader.__init__
        original_state = ledger.HistoryLedgerReader._state
        limits = ledger.HistoryLedgerReader(self.root)
        # The *new valid prefix alone* must exceed both limits, excluding the
        # original successful record and the final failing record.
        record_count = max(limits.blobs.maxsize, limits.validated_records.maxsize) + 2
        baseline = self.git("rev-parse", "HEAD")
        # Newly introduced records cannot already belong to a successful set.
        # Sort the schema error last so both historical LRUs thrash on replay.
        for number in range(record_count - 1):
            self.write(f"{self.records_path}/wide-{number:04d}.json", {
                **self.record, "ref": f"refs/recovery/wide-{number:04d}",
            })
        self.write(f"{self.records_path}/zz-invalid.json", {
            **self.record, "ref": "refs/recovery/zz-invalid", "decision_date": "invalid",
        })
        self.commit()
        affected = [self.git("rev-parse", "HEAD")]
        for revision in range(39):
            self.write("unrelated.txt", f"Unrelated edit {revision}\n")
            self.git("add", "unrelated.txt")
            self.commit()
            affected.append(self.git("rev-parse", "HEAD"))
        # More unique roots than the full-state cache, and a dirty checkout.
        self.assertGreater(len(affected), limits.state.cache_info().maxsize)
        self.assertEqual(len({
            self.git("rev-parse", commit + "^{tree}") for commit in affected
        }), len(affected))
        (self.root / self.policy_path).write_text("Dirty policy\n", encoding="utf-8")
        (self.root / "before.json").write_text("Saved recovery evidence\n", encoding="utf-8")

        def fingerprint():
            return {
                path: (entry[0], entry[1], hashlib.sha256(entry[2]).hexdigest())
                if entry[0] == "file" else entry
                for path, entry in self.repository_bytes().items()
            }

        # Optional real-Git pre-fix replay is expensive; routine QA uses an
        # unbounded object-cache control with failed-prefix reuse disabled.
        lru_baseline = os.environ.get("RETIREMENT_LEDGER_WIDE_INVALID_LRU_BASELINE") == "1"

        def legacy_state(reader, tree, policy_path):
            reader.failed_blobs = {}
            reader.failed_validations = {}
            return original_state(reader, tree, policy_path)

        def measure(reference):
            counts, readers = Counter(), []
            original_git, original_validate = ledger.history_git, ledger.validate_record

            def initialize(reader, root):
                original_init(reader, root)
                readers.append(reader)
                if reference:
                    if lru_baseline:
                        reader.blobs = ledger.HistoryObjectCache(maxsize=128)
                        reader.validated_records = ledger.HistoryObjectCache(maxsize=1024)
                        reader.state = lru_cache(maxsize=32)(reader._state)
                    else:
                        reader.blobs = {}
                        reader.validated_records = set()

            def read(root, *args):
                counts[args[0]] += 1
                return original_git(root, *args)

            def validate(root, record):
                counts["validations"] += 1
                counts["invalid" if record["decision_date"] == "invalid" else "valid"] += 1
                return original_validate(root, record)

            gc.collect()
            tracemalloc.start()
            start = time.perf_counter()
            try:
                with patch.object(ledger.HistoryLedgerReader, "__init__", initialize), \
                        patch.object(ledger.HistoryLedgerReader, "_state",
                                     legacy_state if reference else original_state), \
                        patch.object(ledger, "history_git", read), \
                        patch.object(ledger, "validate_record", validate):
                    result = ledger.audit_history(self.root, self.policy_path, baseline)
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            elapsed = time.perf_counter() - start
            reader = readers[0]
            if not reference:
                self.assertLessEqual(len(reader.blobs), reader.blobs.maxsize)
                self.assertLessEqual(len(reader.validated_records),
                                     reader.validated_records.maxsize)
                self.assertLessEqual(reader.state.cache_info().currsize, 32)
                # Failed scans never become successful snapshots or anchors.
                self.assertEqual(len(reader.current_blobs), 2)
                self.assertEqual(len(reader.current_validations), 1)
                self.assertEqual(reader.previous_blobs, {})
                self.assertEqual(reader.previous_validations, {})
                self.assertEqual(len(reader.failed_blobs), record_count + 2)
                self.assertEqual(len(reader.failed_validations), record_count)
            return result, peak, counts, elapsed

        before = fingerprint()
        reference, old_peak, old_counts, old_time = measure(True)
        self.assertEqual(fingerprint(), before)
        result, peak, counts, elapsed = measure(False)
        self.assertEqual(fingerprint(), before)
        self.assertEqual(json.dumps(result, sort_keys=True), json.dumps(reference, sort_keys=True))
        self.assertEqual(result, {
            "passed": False, "baseline": baseline, "head": affected[-1],
            "history_scope": "first-parent", "commits_checked": len(affected) + 1,
            "retained_record_count": 1, "added_record_count": 0,
            "policy_file": self.policy_path, "approved_migrations": [],
            "holds": [{"commit": commit, "reason": "invalid-ledger",
                       "detail": "retirement ledger: decision_date must use YYYY-MM-DD"}
                      for commit in affected],
        })
        self.assertEqual(counts["cat-file"], record_count + 2)
        self.assertEqual(counts["valid"], record_count)
        self.assertEqual(counts["invalid"], len(affected))
        self.assertEqual(counts["ls-tree"], len(affected) + 1)
        self.assertEqual(old_counts["cat-file"],
                         2 + len(affected) * record_count
                         if lru_baseline else record_count + 2)
        self.assertEqual(old_counts["valid"],
                         1 + len(affected) * (record_count - 1)
                         if lru_baseline else record_count)
        self.assertEqual(old_counts["invalid"], len(affected))
        self.assertEqual(old_counts["ls-tree"], counts["ls-tree"])
        for label, memory, reads, seconds in (
            ("pre-fix LRUs" if lru_baseline else "unbounded control",
             old_peak, old_counts, old_time),
            ("failed-prefix reuse", peak, counts, elapsed),
        ):
            print(f"\nWide invalid {label}: {record_count} new records, "
                  f"{len(affected)} invalid roots; {memory / 1024**2:.2f} MiB peak Python heap; "
                  f"{reads['cat-file']} blob reads; {reads['valid']} valid-record validations; "
                  f"{reads['invalid']} failing validations; {seconds:.3f}s")

    def test_oversized_wide_invalid_prefix_reuses_evidence_through_repair(self):
        ledger = audit.retirement_ledger
        limits = ledger.HistoryLedgerReader(self.root)
        budget_bytes = 4096
        prefix_count = max(limits.blobs.maxsize, limits.validated_records.maxsize) + 1
        baseline = self.git("rev-parse", "HEAD")
        expected_records = {self.ref: self.record}
        prefix_refs = set()
        for number in range(prefix_count):
            record = {
                **self.record, "ref": f"refs/recovery/oversized-{number:04d}",
                "evidence": f"Reviewed decision {number}: " + "x" * (budget_bytes + 2048),
            }
            # Byte limits govern optional reuse, not valid record acceptance.
            self.assertGreater(sys.getsizeof(record["evidence"]), budget_bytes)
            self.write(f"{self.records_path}/oversized-{number:04d}.json", record)
            expected_records[record["ref"]] = record
            prefix_refs.add(record["ref"])
        suffix_path = f"{self.records_path}/zz-invalid.json"
        repaired_record = {**self.record, "ref": "refs/recovery/zz-invalid"}
        self.write(suffix_path, {**repaired_record, "decision_date": "invalid"})
        self.commit()
        invalid_oid = self.git("rev-parse", f"HEAD:{suffix_path}")
        affected = [self.git("rev-parse", "HEAD")]
        # Oversized prefixes cannot survive in historical snapshots. Several
        # distinct unrelated roots expose cyclic rereads without a long replay.
        for revision in range(3):
            self.write("unrelated.json", {"revision": revision})
            self.git("add", "unrelated.json")
            self.commit()
            affected.append(self.git("rev-parse", "HEAD"))
        self.write(suffix_path, repaired_record)
        self.commit()
        repaired = [self.git("rev-parse", "HEAD")]
        for revision in range(3, 5):
            self.write("unrelated.json", {"revision": revision})
            self.git("add", "unrelated.json")
            self.commit()
            repaired.append(self.git("rev-parse", "HEAD"))
        expected_records[repaired_record["ref"]] = repaired_record
        self.assertEqual(len({
            self.git("rev-parse", commit + "^{tree}") for commit in affected + repaired
        }), len(affected + repaired))
        prefix_oids = {
            line.split()[2]
            for line in self.git("ls-tree", "-r", "HEAD", "--", self.records_path).splitlines()
            if "/oversized-" in line
        }
        self.assertEqual(len(prefix_oids), prefix_count)
        baseline_blobs = {
            self.git("rev-parse", f"{baseline}:{path}")
            for path in (self.policy_path, self.record_path)
        }
        baseline_record = self.git("rev-parse", f"{baseline}:{self.record_path}")
        # Dirty local data and saved recovery evidence must remain untouched.
        self.write(self.policy_path, {"dirty": True})
        self.write(suffix_path, {"dirty": True})
        self.snapshot.write_text("Saved recovery evidence\n", encoding="utf-8")

        def fingerprint():
            return {
                path: (entry[0], entry[1], hashlib.sha256(entry[2]).hexdigest(), *entry[3:])
                if entry[0] == "file" else entry
                for path, entry in self.repository_bytes().items()
            }

        original_init = ledger.HistoryLedgerReader.__init__
        original_read = ledger.HistoryLedgerReader.read
        original_git, original_validate = ledger.history_git, ledger.validate_record

        def measure(uncached):
            reads, validations, scans = Counter(), Counter(), []

            def initialize(reader, root):
                original_init(reader, root)
                # Keep production entry limits; scale only optional reuse bytes.
                reader.reuse_budget.maxbytes = budget_bytes

            def read(root, *args):
                if args[0] == "cat-file":
                    reads[args[2]] += 1
                return original_git(root, *args)

            def validate(root, record):
                validations[record["ref"]] += 1
                return original_validate(root, record)

            def observe(reader, tree, policy_path):
                index = len(scans)
                before_reads, before_validations = sum(reads.values()), sum(validations.values())
                try:
                    state = (ledger.tree_ledger(reader.root, tree, policy_path)
                             if uncached else original_read(reader, tree, policy_path))
                    if index > len(affected):
                        # Compare every complete repaired decision, including
                        # evidence, rather than only a final retained count.
                        self.assertEqual(state[1], expected_records)
                    return state
                finally:
                    scans.append((sum(reads.values()) - before_reads,
                                  sum(validations.values()) - before_validations))
                    if not uncached:
                        self.assertLessEqual(reader.reuse_budget.used_bytes, budget_bytes)
                        self.assertLessEqual(len(reader.blobs), reader.blobs.maxsize)
                        self.assertLessEqual(len(reader.validated_records),
                                             reader.validated_records.maxsize)
                        self.assertLessEqual(reader.state.cache_info().currsize,
                                             reader.state.cache_info().maxsize)
                        self.assertTrue(prefix_oids.isdisjoint(reader.blobs))
                        if 0 < index <= len(affected):
                            # Failed scans retain the new prefix, but may not
                            # promote it to a successful snapshot or anchor.
                            self.assertEqual(set(reader.current_blobs), baseline_blobs)
                            self.assertEqual(set(reader.current_validations), {baseline_record})
                            self.assertEqual(reader.previous_blobs, {})
                            self.assertEqual(reader.previous_validations, {})
                            self.assertEqual(set(reader.failed_blobs),
                                             baseline_blobs | prefix_oids | {invalid_oid})
                            self.assertEqual(set(reader.failed_validations),
                                             prefix_oids | {baseline_record})
                            self.assertNotIn(invalid_oid, reader.failed_validations)
                        elif index > len(affected):
                            self.assertEqual(len(reader.current_validations), len(expected_records))
                            self.assertTrue(prefix_oids.issubset(reader.current_validations))
                            # A wide successful snapshot must bypass the byte
                            # cache, while the active working set remains usable.
                            self.assertFalse(any(
                                isinstance(state, tuple) and len(state[1]) == len(expected_records)
                                for state in reader.state.cache.values()
                            ))

            with patch.object(ledger.HistoryLedgerReader, "__init__", initialize), \
                    patch.object(ledger.HistoryLedgerReader, "read", observe), \
                    patch.object(ledger, "history_git", read), \
                    patch.object(ledger, "validate_record", validate):
                result = ledger.audit_history(self.root, self.policy_path, baseline)
            return result, reads, validations, scans

        before = fingerprint()
        reference, cold_reads, cold_validations, cold_scans = measure(True)
        self.assertEqual(fingerprint(), before)
        result, reads, validations, scans = measure(False)
        self.assertEqual(fingerprint(), before)
        self.assertEqual(result, reference)
        self.assertEqual(result, {
            "passed": False, "baseline": baseline, "head": repaired[-1],
            "history_scope": "first-parent",
            "commits_checked": 1 + len(affected) + len(repaired),
            "retained_record_count": len(expected_records),
            "added_record_count": prefix_count + 1,
            "policy_file": self.policy_path, "approved_migrations": [],
            "holds": [{"commit": commit, "reason": "invalid-ledger",
                       "detail": "retirement ledger: decision_date must use YYYY-MM-DD"}
                      for commit in affected],
        })
        self.assertEqual(scans, [(2, 1), (prefix_count + 1, prefix_count + 1)]
                         + [(0, 1)] * (len(affected) - 1)
                         + [(1, 1)] + [(0, 0)] * (len(repaired) - 1))
        self.assertEqual(cold_scans, [(2, 1)]
                         + [(prefix_count + 3, prefix_count + 2)] * (len(affected) + len(repaired)))
        self.assertEqual(set(reads), set(cold_reads))
        self.assertTrue(all(count == 1 for count in reads.values()))
        self.assertEqual(set(validations), set(cold_validations))
        for oid in prefix_oids:
            self.assertEqual(reads[oid], 1)
            self.assertEqual(cold_reads[oid], len(affected) + len(repaired))
        for ref in prefix_refs:
            self.assertEqual(validations[ref], 1)
            self.assertEqual(cold_validations[ref], len(affected) + len(repaired))
        self.assertEqual(validations[repaired_record["ref"]], len(affected) + 1)

    def test_wide_failed_prefix_survives_changing_invalid_suffix_errors(self):
        ledger = audit.retirement_ledger
        limits = ledger.HistoryLedgerReader(self.root)
        baseline = self.git("rev-parse", "HEAD")
        prefix_count = max(limits.blobs.maxsize, limits.validated_records.maxsize) + 1
        for number in range(prefix_count):
            path = f"{self.records_path}/wide-{number:04d}.json"
            self.write(path, {
                **self.record, "ref": f"refs/recovery/wide-{number:04d}",
            })
        suffix_path = f"{self.records_path}/zz-invalid.json"
        versions = (
            ("decision_date", "invalid", "decision_date must use YYYY-MM-DD"),
            ("protected_commit", "short", "protected_commit requires a full commit ID"),
            ("decision", "keep", "decision must be retire"),
            ("format", 2, "record requires format 1"),
        )
        affected, trees, suffix_oids, details = [], [], [], []
        for field, value, error in versions:
            # Rebuild from the valid template: the prior error must not mask
            # this commit's different failure. Every suffix blob is distinct.
            self.write(suffix_path, {
                **self.record, "ref": "refs/recovery/zz-invalid", field: value,
            })
            self.commit()
            affected.append(self.git("rev-parse", "HEAD"))
            trees.append(self.git("rev-parse", "HEAD^{tree}"))
            suffix_oids.append(self.git("rev-parse", f"HEAD:{suffix_path}"))
            details.append("retirement ledger: " + error)
        self.assertEqual(len(set(trees)), len(versions))
        self.assertEqual(len(set(suffix_oids)), len(versions))
        entries = self.git("ls-tree", "-r", "HEAD", "--", self.records_path)
        prefix_oids = {
            line.split()[2] for line in entries.splitlines()
            if "/wide-" in line
        }
        self.assertEqual(len(prefix_oids), prefix_count)
        self.assertGreater(prefix_count, limits.blobs.maxsize)
        self.assertGreater(prefix_count, limits.validated_records.maxsize)
        baseline_blobs = {
            self.git("rev-parse", f"{baseline}:{path}")
            for path in (self.policy_path, self.record_path)
        }
        baseline_record = self.git("rev-parse", f"{baseline}:{self.record_path}")
        # Committed data, not either dirty file or the recovery snapshot, is
        # authoritative. Fingerprint all checkout and Git metadata as well.
        self.write(self.policy_path, {"dirty": True})
        self.write(suffix_path, {"dirty": True})
        self.snapshot.write_text("Saved recovery evidence\n", encoding="utf-8")

        def fingerprint():
            return {
                path: (entry[0], entry[1], hashlib.sha256(entry[2]).hexdigest(), *entry[3:])
                if entry[0] == "file" else entry
                for path, entry in self.repository_bytes().items()
            }

        original_read = ledger.HistoryLedgerReader.read
        original_git, original_validate = ledger.history_git, ledger.validate_record

        def measure(uncached):
            reads, validations, scans = Counter(), Counter(), []

            def read(root, *args):
                if args[0] == "cat-file":
                    reads[args[2]] += 1
                return original_git(root, *args)

            def validate(root, record):
                # Count attempts, including errors, without replacing any real
                # schema or Git ref validation in the uncached control.
                validations[json.dumps(record, sort_keys=True)] += 1
                return original_validate(root, record)

            def observe(reader, tree, policy_path):
                before_reads, before_validations = sum(reads.values()), sum(validations.values())
                try:
                    if uncached:
                        return ledger.tree_ledger(reader.root, tree, policy_path)
                    return original_read(reader, tree, policy_path)
                finally:
                    index = len(scans)
                    scans.append((sum(reads.values()) - before_reads,
                                  sum(validations.values()) - before_validations))
                    if not uncached:
                        self.assertLessEqual(len(reader.blobs), reader.blobs.maxsize)
                        self.assertLessEqual(len(reader.validated_records),
                                             reader.validated_records.maxsize)
                        self.assertLessEqual(reader.state.cache_info().currsize,
                                             reader.state.cache_info().maxsize)
                        self.assertGreaterEqual(reader.reuse_budget.used_bytes, 0)
                        self.assertLessEqual(reader.reuse_budget.used_bytes,
                                             reader.reuse_budget.maxbytes)
                        self.assertEqual(set(reader.current_blobs), baseline_blobs)
                        self.assertEqual(set(reader.current_validations), {baseline_record})
                        self.assertEqual(reader.previous_blobs, {})
                        self.assertEqual(reader.previous_validations, {})
                        if index:
                            # Exact membership proves replacement, not a growing
                            # union of old invalid suffixes or their validations.
                            self.assertEqual(set(reader.failed_blobs),
                                             baseline_blobs | prefix_oids | {suffix_oids[index - 1]})
                            self.assertEqual(set(reader.failed_validations),
                                             prefix_oids | {baseline_record})
                            self.assertTrue(set(suffix_oids).isdisjoint(
                                reader.failed_validations))
                        else:
                            self.assertEqual(reader.failed_blobs, {})
                            self.assertEqual(reader.failed_validations, {})

            with patch.object(ledger.HistoryLedgerReader, "read", observe), \
                    patch.object(ledger, "history_git", read), \
                    patch.object(ledger, "validate_record", validate):
                result = ledger.audit_history(self.root, self.policy_path, baseline)
            return result, reads, validations, scans

        before = fingerprint()
        reference, cold_reads, cold_validations, cold_scans = measure(True)
        self.assertEqual(fingerprint(), before)
        result, reads, validations, scans = measure(False)
        self.assertEqual(fingerprint(), before)
        self.assertEqual(json.dumps(result, sort_keys=True), json.dumps(reference, sort_keys=True))
        self.assertEqual(result, {
            "passed": False, "baseline": baseline, "head": affected[-1],
            "history_scope": "first-parent", "commits_checked": len(affected) + 1,
            "retained_record_count": 1, "added_record_count": 0,
            "policy_file": self.policy_path, "approved_migrations": [],
            "holds": [{"commit": commit, "reason": "invalid-ledger", "detail": detail}
                      for commit, detail in zip(affected, details)],
        })
        self.assertEqual(scans, [(2, 1), (prefix_count + 1, prefix_count + 1)]
                         + [(1, 1)] * (len(affected) - 1))
        self.assertEqual(cold_scans, [(2, 1)]
                         + [(prefix_count + 3, prefix_count + 2)] * len(affected))
        self.assertEqual(set(reads), baseline_blobs | prefix_oids | set(suffix_oids))
        self.assertTrue(all(count == 1 for count in reads.values()))
        self.assertTrue(all(count == 1 for count in validations.values()))
        self.assertEqual(set(validations), set(cold_validations))
        for oid in prefix_oids:
            self.assertEqual(cold_reads[oid], len(affected))
        for oid in suffix_oids:
            self.assertEqual(cold_reads[oid], 1)

    def test_failed_prefix_rechecks_late_duplicate_refs_for_every_distinct_tree(self):
        ledger = audit.retirement_ledger
        baseline = self.git("rev-parse", "HEAD")
        prefix_count = 6
        for number in range(prefix_count):
            self.write(f"{self.records_path}/prefix-{number:04d}.json", {
                **self.record, "ref": f"refs/recovery/prefix-{number:04d}",
            })
        # A different blob with a valid schema: only the ledger-wide ref check
        # can reject it, after all earlier records have validated successfully.
        self.write(f"{self.records_path}/zz-duplicate.json", {
            **self.record, "ref": "refs/recovery/prefix-0000",
            "evidence": "A second individually valid decision for the same ref.",
        })
        self.commit()
        affected = [self.git("rev-parse", "HEAD")]
        for revision in range(3):
            self.write("unrelated.txt", f"Unrelated edit {revision}\n")
            self.git("add", "unrelated.txt")
            self.commit()
            affected.append(self.git("rev-parse", "HEAD"))
        self.assertEqual(len({
            self.git("rev-parse", commit + "^{tree}") for commit in affected
        }), len(affected))
        self.write(self.policy_path, {"dirty": True})
        self.write(self.record_path, {"dirty": True})
        self.snapshot.write_text("Saved recovery evidence\n", encoding="utf-8")

        def fingerprint():
            return {
                path: (entry[0], entry[1], hashlib.sha256(entry[2]).hexdigest(), *entry[3:])
                if entry[0] == "file" else entry
                for path, entry in self.repository_bytes().items()
            }

        original_init = ledger.HistoryLedgerReader.__init__
        original_read = ledger.HistoryLedgerReader.read
        original_git = ledger.history_git
        original_validate = ledger.validate_record

        def measure(uncached):
            counts, scans = Counter(), []

            def initialize(reader, root):
                original_init(reader, root)
                # No historical object cache can mask missing failed-prefix
                # reuse; successful working sets contain only the baseline.
                reader.blobs = ledger.HistoryObjectCache(maxsize=0)
                reader.validated_records = ledger.HistoryObjectCache(maxsize=0)

            def read(root, *args):
                counts[args[0]] += 1
                return original_git(root, *args)

            def validate(root, record):
                result = original_validate(root, record)
                counts["successful-validations"] += 1
                return result

            def observe(reader, tree, policy_path):
                before = counts.copy()
                try:
                    # No blob, validation, snapshot, or working-set reuse.
                    if uncached:
                        return ledger.tree_ledger(reader.root, tree, policy_path)
                    return original_read(reader, tree, policy_path)
                finally:
                    scans.append(counts - before)
                    if not uncached and len(scans) > 1:
                        self.assertEqual(len(reader.failed_blobs), prefix_count + 3)
                        self.assertEqual(len(reader.failed_validations), prefix_count + 2)
                        self.assertEqual(len(reader.current_blobs), 2)
                        self.assertEqual(len(reader.current_validations), 1)
                        self.assertEqual(reader.previous_blobs, {})
                        self.assertEqual(reader.previous_validations, {})

            with patch.object(ledger.HistoryLedgerReader, "__init__", initialize), \
                    patch.object(ledger.HistoryLedgerReader, "read", observe), \
                    patch.object(ledger, "history_git", read), \
                    patch.object(ledger, "validate_record", validate):
                result = ledger.audit_history(self.root, self.policy_path, baseline)
            return result, scans

        before = fingerprint()
        reference, cold_scans = measure(True)
        self.assertEqual(fingerprint(), before)
        result, warm_scans = measure(False)
        self.assertEqual(fingerprint(), before)
        self.assertEqual(json.dumps(result, sort_keys=True), json.dumps(reference, sort_keys=True))
        self.assertEqual(result, {
            "passed": False, "baseline": baseline, "head": affected[-1],
            "history_scope": "first-parent", "commits_checked": len(affected) + 1,
            "retained_record_count": 1, "added_record_count": 0,
            "policy_file": self.policy_path, "approved_migrations": [],
            "holds": [{"commit": commit, "reason": "invalid-ledger",
                       "detail": "retirement history: duplicate recovery ref decisions"}
                      for commit in affected],
        })
        self.assertEqual(len(warm_scans), len(affected) + 1)
        self.assertEqual(len(cold_scans), len(warm_scans))
        for index, (cold, warm) in enumerate(zip(cold_scans, warm_scans)):
            with self.subTest(scan=index):
                self.assertEqual(cold["ls-tree"], 1)
                self.assertEqual(warm["ls-tree"], 1)
                self.assertEqual(cold["cat-file"], 2 if index == 0 else prefix_count + 3)
                self.assertEqual(cold["successful-validations"],
                                 1 if index == 0 else prefix_count + 2)
                self.assertEqual(warm["cat-file"],
                                 2 if index == 0 else prefix_count + 1 if index == 1 else 0)
                self.assertEqual(warm["successful-validations"],
                                 1 if index == 0 else prefix_count + 1 if index == 1 else 0)

    def test_failed_prefix_is_replaced_without_promoting_or_accumulating_scans(self):
        ledger = audit.retirement_ledger
        reader = ledger.HistoryLedgerReader(self.root)
        reader.blobs = ledger.HistoryObjectCache(maxsize=0)
        reader.validated_records = ledger.HistoryObjectCache(maxsize=0)
        reader._state(self.git("rev-parse", "HEAD^{tree}"), self.policy_path)
        successful = (reader.current_blobs, reader.current_validations,
                      reader.previous_blobs, reader.previous_validations)
        baseline = self.git("rev-parse", "HEAD")
        failed_sets = []
        for label in ("A", "B", "C"):
            self.write(f"{self.records_path}/prefix.json", {
                **self.record, "ref": "refs/recovery/prefix", "evidence": f"Prefix {label}.",
            })
            self.write(f"{self.records_path}/zz-invalid.json", {
                **self.record, "ref": "refs/recovery/invalid", "decision_date": "invalid",
            })
            self.commit()
            tree = self.git("rev-parse", "HEAD^{tree}")
            self.assertEqual(reader._state(tree, self.policy_path),
                             "retirement ledger: decision_date must use YYYY-MM-DD")
            self.assertEqual((reader.current_blobs, reader.current_validations,
                              reader.previous_blobs, reader.previous_validations), successful)
            self.assertEqual(len(reader.failed_blobs), 4)
            self.assertEqual(len(reader.failed_validations), 2)
            failed_sets.append(set(reader.failed_blobs))
        # Exactly one failed prefix remains: neither earlier distinct prefix
        # survives in it, even when all historical reuse is disabled.
        for earlier in failed_sets[:-1]:
            self.assertEqual(len(earlier - failed_sets[-1]), 1)
        self.assertEqual(len(reader.blobs), 0)
        self.assertEqual(len(reader.validated_records), 0)
        # Repair can reuse immutable prefix objects but must actually validate
        # the corrected record and produce the same full history as no reuse.
        self.write(f"{self.records_path}/zz-invalid.json", {
            **self.record, "ref": "refs/recovery/invalid",
        })
        self.commit()
        state = reader._state(self.git("rev-parse", "HEAD^{tree}"), self.policy_path)
        self.assertNotIsInstance(state, str)
        self.assertEqual(len(reader.current_validations), 3)
        result = self.assert_cached_history_equivalent(baseline)
        self.assertEqual(len(result["holds"]), 3)
        self.assertEqual(result["added_record_count"], 2)

    def test_two_working_sets_replace_cold_scans_and_survive_repeated_or_invalid_scans(self):
        ledger = audit.retirement_ledger
        reader = ledger.HistoryLedgerReader(self.root)
        # Disable historical reuse so only the two working sets can save reads.
        reader.blobs = ledger.HistoryObjectCache(maxsize=0)
        reader.validated_records = ledger.HistoryObjectCache(maxsize=0)
        trees = {}
        for label in ("A", "B", "C", "D"):
            self.write(self.record_path, {**self.record, "evidence": f"Set {label}."})
            self.commit()
            trees[label] = self.git("rev-parse", "HEAD^{tree}")
        self.write(self.record_path, "{invalid}")
        self.commit()
        invalid = self.git("rev-parse", "HEAD^{tree}")
        original_git = ledger.history_git
        reads = Counter()

        def read(root, *args):
            reads[args[0]] += 1
            return original_git(root, *args)

        def scan(label):
            state = reader._state(trees[label], self.policy_path)
            self.assertNotIsInstance(state, str)
            self.assertEqual(state[1][self.ref]["evidence"], f"Set {label}.")

        with patch.object(ledger, "history_git", read):
            for label in ("A", "A", "B", "B", "A"):
                scan(label)
            self.assertEqual(reads["cat-file"], 3)  # Shared policy, A, B.
            working_sets = (reader.current_blobs, reader.previous_blobs,
                            reader.current_validations, reader.previous_validations)
            self.assertIsInstance(reader._state(invalid, self.policy_path), str)
            self.assertEqual(reads["cat-file"], 4)
            self.assertEqual(
                (reader.current_blobs, reader.previous_blobs,
                 reader.current_validations, reader.previous_validations),
                working_sets,
            )
            scan("B")
            self.assertEqual(reads["cat-file"], 4)
            scan("C")
            scan("D")
            self.assertTrue(set(working_sets[0]) - set(reader.current_blobs)
                            - set(reader.previous_blobs))
            # A is cold now: two sets are retained, never every historical set.
            scan("A")
            self.assertEqual(reads["cat-file"], 7)
            self.assertEqual(len(reader.current_blobs), 2)
            self.assertEqual(len(reader.previous_blobs), 2)
            self.assertEqual(len(reader.current_validations), 1)
            self.assertEqual(len(reader.previous_validations), 1)
            self.assertEqual(len(reader.blobs), 0)
            self.assertEqual(len(reader.validated_records), 0)

    def high_churn_history(self):
        """Distinct large blobs, a hot unchanged record, and cold restorations."""
        self.git("config", "gc.auto", "0")
        self.git("config", "maintenance.auto", "false")
        baseline = self.git("rev-parse", "HEAD")
        for revision in range(192):
            for number in range(6):
                self.write(f"{self.records_path}/churn-{number}.json", {
                    **self.record, "ref": f"refs/recovery/churn-{number}",
                    "evidence": f"Revision {revision}, decision {number}. " + "x" * 16384,
                })
            self.commit()
            if revision == 0:
                first_churn = self.git("rev-parse", "HEAD")
            if revision % 48 == 47:
                # Return to an old tree after both snapshot and blob eviction.
                if revision == 191:
                    self.git("read-tree", first_churn)
                    self.git("commit", "-m", "Restore original churn decisions")
                self.git("read-tree", baseline)
                self.git("commit", "-m", "Restore baseline ledger")
        self.write(self.record_path, {**self.record, "decision_date": "invalid"})
        self.commit()
        invalid = self.git("rev-parse", "HEAD")
        self.git("read-tree", baseline)
        self.git("commit", "-m", "Restore valid ledger")
        # read-tree left the checkout dirty; reset fixture files, not audit state.
        for number in range(6):
            (self.root / f"{self.records_path}/churn-{number}.json").unlink()
        self.write(self.record_path, self.record)
        migration, new_policy, new_record = self.migrate()
        self.git("rm", new_record)
        self.commit()
        removed = self.git("rev-parse", "HEAD")
        self.write(new_record, self.record)
        self.commit()
        return baseline, [f"{migration}={self.policy_path},{new_policy}"], invalid, removed

    def test_high_churn_history_memory_and_read_tradeoffs(self):
        ledger = audit.retirement_ledger
        baseline, approvals, invalid, removed = self.high_churn_history()
        original_init = ledger.HistoryLedgerReader.__init__

        def fingerprint():
            return {
                path.relative_to(self.root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in self.root.rglob("*") if path.is_file() and not path.is_symlink()
            } | self.repository_links()

        before = fingerprint()

        def unbounded_init(reader, root):
            original_init(reader, root)
            reader.blobs = {}
            reader.validated_records = set()
            reader.state = lru_cache(maxsize=32)(reader._state)

        def measure(init):
            counts = Counter()
            original_git = ledger.history_git
            original_validate = ledger.validate_record
            original_read = ledger.HistoryLedgerReader.read

            def observe(reader, *args):
                result = original_read(reader, *args)
                sample_history_accounting(reader, counts)
                return result

            def read(root, *args):
                counts[args[0]] += 1
                return original_git(root, *args)

            def validate(*args):
                counts["validations"] += 1
                return original_validate(*args)

            gc.collect()
            tracemalloc.start()
            start = time.perf_counter()
            try:
                with patch.object(ledger.HistoryLedgerReader, "__init__", init), \
                        patch.object(ledger.HistoryLedgerReader, "read", observe), \
                        patch.object(ledger, "history_git", read), \
                        patch.object(ledger, "validate_record", validate):
                    result = ledger.audit_history(self.root, self.policy_path, baseline, approvals)
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            elapsed = time.perf_counter() - start
            gc.collect()
            return result, peak, counts, elapsed

        unbounded, old_peak, old_counts, old_time = measure(unbounded_init)
        self.assertEqual(fingerprint(), before)
        bounded, peak, counts, elapsed = measure(original_init)
        self.assertEqual(fingerprint(), before)
        self.assertEqual(json.dumps(bounded, sort_keys=True),
                         json.dumps(unbounded, sort_keys=True))
        self.assertEqual(len(bounded["approved_migrations"]), 1)
        self.assertIn({"commit": invalid, "reason": "invalid-ledger",
                       "detail": "retirement ledger: decision_date must use YYYY-MM-DD"},
                      bounded["holds"])
        self.assertIn({"commit": removed, "ref": self.ref, "reason": "removed-record",
                       "record_file": self.record_path}, bounded["holds"])
        self.assertLess(peak, old_peak * 0.65)
        self.assertGreater(counts["cat-file"], old_counts["cat-file"])
        self.assertGreater(counts["validations"], old_counts["validations"])
        # Eviction must not turn a high-churn audit into an uncached reread of
        # every record. In particular the hot policy and stable record survive.
        self.assertLessEqual(counts["cat-file"], old_counts["cat-file"] + 24)
        self.assertEqual(counts["ls-tree"], old_counts["ls-tree"])
        for label, memory, reads, seconds in (
            ("unbounded", old_peak, old_counts, old_time),
            ("bounded", peak, counts, elapsed),
        ):
            print(f"\nHigh-churn {label}: {memory / 1024**2:.2f} MiB peak Python heap; "
                  f"{reads['payload_peak_bytes'] / 1024**2:.2f} MiB payload; "
                  f"{reads['bookkeeping_peak_bytes'] / 1024**2:.2f} MiB bookkeeping; "
                  f"{reads['cat-file']} blob reads; {reads['validations']} validations; "
                  f"{seconds:.3f}s")

    def test_large_heterogeneous_records_in_separate_processes(self):
        # Far larger and more varied than the 16 KiB churn fixture. A small
        # entry count can retain tens of MiB, including in full-state snapshots.
        sizes = (64 * 1024, 384 * 1024, 1536 * 1024)
        baseline = self.git("rev-parse", "HEAD")
        versions = []
        originals = []
        for revision in range(40):
            for number, size in enumerate(sizes):
                # Distinct text, lengths and Unicode storage widths. No giant
                # payload is held in the parent during the measured audit.
                unit = f"Reviewed revision {revision} record {number}: " + ("A", "é", "界")[number]
                record = {
                    **self.record, "ref": f"refs/recovery/large-{number}",
                    "evidence": (unit * (size // len(unit) + 1))[:size],
                }
                self.write(f"{self.records_path}/large-{number}.json", record)
                if revision == 0:
                    originals.append(record)
            self.commit()
            versions.append(self.git("rev-parse", "HEAD"))

        # Invalid intermediate state and restoration must not erase its hold.
        self.write(self.record_path, {**self.record, "decision_date": "invalid"})
        self.commit()
        invalid = self.git("rev-parse", "HEAD")
        self.write(self.record_path, self.record)
        for number, record in enumerate(originals):
            self.write(f"{self.records_path}/large-{number}.json", record)
        self.commit()
        # Move every decision, not just the small baseline record.
        new_policy = "governance/retained/policy.json"
        self.git("rm", self.policy_path, self.record_path)
        self.write(new_policy, {
            **self.policy, "records_directory": "governance/retained/decisions",
            "approved_on": "2026-10-03",
        })
        self.write("governance/retained/decisions/old.json", self.record)
        for number, record in enumerate(originals):
            self.git("rm", f"{self.records_path}/large-{number}.json")
            self.write(f"governance/retained/decisions/large-{number}.json", record)
        self.commit()
        migration = self.git("rev-parse", "HEAD")
        approvals = [f"{migration}={self.policy_path},{new_policy}"]
        self.git("rm", "governance/retained/decisions/large-2.json")
        self.commit()
        removed = self.git("rev-parse", "HEAD")
        self.write("governance/retained/decisions/large-2.json", originals[2])
        self.commit()
        del originals, record

        def fingerprint():
            return {
                path.relative_to(self.root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in self.root.rglob("*") if path.is_file() and not path.is_symlink()
            } | self.repository_links()

        before = fingerprint()
        measurements = {}
        for strategy in ("unbounded", "entry-bounded", "byte-bounded", "minimal-history"):
            with self.subTest(strategy=strategy):
                child = subprocess.run(
                    [sys.executable, "-c",
                     "import json, runpy, sys; "
                     "probe = runpy.run_path(sys.argv[1])['large_record_memory_probe']; "
                     "print(json.dumps(probe(*json.loads(sys.argv[2]))))",
                     str(Path(__file__).resolve()),
                     json.dumps([str(self.root), self.policy_path, baseline, approvals, strategy])],
                    capture_output=True, text=True, encoding="utf-8", timeout=180,
                )
                self.assertEqual(child.returncode, 0, child.stderr)
                measured = measurements[strategy] = json.loads(child.stdout)
                self.assertEqual(fingerprint(), before)
                self.assertGreater(measured["heap_peak_bytes"], 0)
                print("\nLarge-record memory probe: " + json.dumps({
                    key: value for key, value in measured.items() if key != "result"
                }, sort_keys=True))

        result = measurements["byte-bounded"]["result"]
        for measured in measurements.values():
            self.assertEqual(measured["result"], result)
        self.assertFalse(result["passed"])
        self.assertEqual(result["retained_record_count"], 4)
        self.assertEqual(result["added_record_count"], 3)
        self.assertEqual(result["approved_migrations"], [{
            "commit": migration, "from_policy": self.policy_path, "to_policy": new_policy,
            "status": "owner-approved-policy-migration",
        }])
        self.assertEqual(result["commits_checked"], 46)
        self.assertEqual(len(result["holds"]), 39 * 3 + 2)
        expected_rewrites = {
            (commit, f"refs/recovery/large-{number}")
            for commit in versions[1:] for number in range(3)
        }
        self.assertEqual({
            (hold["commit"], hold["ref"]) for hold in result["holds"]
            if hold["reason"] == "rewritten-record"
        }, expected_rewrites)
        self.assertTrue(all(
            hold["changed_fields"] == ["evidence"] for hold in result["holds"]
            if hold["reason"] == "rewritten-record"
        ))
        self.assertIn({"commit": invalid, "reason": "invalid-ledger",
                       "detail": "retirement ledger: decision_date must use YYYY-MM-DD"},
                      result["holds"])
        self.assertIn({"commit": removed, "ref": "refs/recovery/large-2",
                       "reason": "removed-record",
                       "record_file": f"{self.records_path}/large-2.json"}, result["holds"])
        # Heap-only relative assertion: RSS includes allocator arenas, native
        # allocations and tracing overhead, and is diagnostic on every host.
        self.assertLess(measurements["minimal-history"]["heap_peak_bytes"],
                        measurements["entry-bounded"]["heap_peak_bytes"] * 0.65)
        self.assertLess(measurements["byte-bounded"]["heap_peak_bytes"],
                        measurements["entry-bounded"]["heap_peak_bytes"] * 0.35)
        self.assertLessEqual(measurements["byte-bounded"]["blob_reads"],
                             measurements["entry-bounded"]["blob_reads"] + 12)

    def test_oversized_wide_stable_ledger_in_separate_processes(self):
        self.assert_oversized_wide_stable_ledger_in_separate_processes(multilingual=False)

    def test_oversized_wide_multilingual_stable_ledger_in_separate_processes(self):
        self.assert_oversized_wide_stable_ledger_in_separate_processes(multilingual=True)

    def assert_oversized_wide_stable_ledger_in_separate_processes(self, *, multilingual):
        ledger = audit.retirement_ledger
        limits = ledger.HistoryLedgerReader(self.root)
        # Scale only bytes, not production entry limits: every generated record
        # exceeds optional historical reuse, and the active set also exceeds
        # both entry LRUs. This is not a record-size or whole-process RAM limit.
        budget_bytes = 4096
        record_count = max(limits.blobs.maxsize, limits.validated_records.maxsize) + 1
        # Put non-BMP evidence in the cold record too, so the restore path must
        # reread it rather than accidentally reusing an undercharged string.
        variants = (("😀", 4), ("x", 1), ("é", 1), ("界", 2)) if multilingual else (("x", 1),)
        for number in range(record_count):
            unit, width = variants[number % len(variants)]
            evidence_size = (budget_bytes + 2048) // width
            evidence = f"Reviewed decision {number}: " + unit * evidence_size
            # Python's decoded string storage, not len() or JSON's escaped
            # on-disk size, determines whether this evidence alone is oversized.
            self.assertGreater(sys.getsizeof(evidence), budget_bytes)
            if width > 1:
                self.assertLess(len(evidence), budget_bytes)
            self.write(f"{self.records_path}/oversized-{number:04d}.json", {
                **self.record, "ref": f"refs/recovery/oversized-{number:04d}",
                "evidence": evidence,
            })
        del evidence
        self.commit()
        baseline = self.git("rev-parse", "HEAD")
        cold_path = f"{self.records_path}/oversized-0000.json"
        cold = json.loads((self.root / cold_path).read_text(encoding="utf-8"))
        self.assertGreater(sys.getsizeof(cold["evidence"]), budget_bytes)
        self.assertGreater((self.root / cold_path).stat().st_size, budget_bytes)
        cold_oid = self.git("rev-parse", f"HEAD:{cold_path}")

        def unrelated_edits(count):
            for revision in range(count):
                self.write("unrelated.json", {"revision": revision})
                self.git("add", "unrelated.json")
                self.commit()

        # Every full snapshot exceeds the byte budget and must bypass reuse,
        # so a handful of unrelated roots is enough to expose cyclic rescans.
        # Keep the fully uncached (including Git ref validation) control cheap.
        stable_commits = 6
        unrelated_edits(stable_commits)
        rewrites = []
        for revision in ("second", "third"):
            self.write(cold_path, {**cold, "evidence": revision + cold["evidence"]})
            self.commit()
            rewrites.append(self.git("rev-parse", "HEAD"))
        # Original cold blob is now absent from both rolling working sets and,
        # being oversized, cannot survive in any historical snapshot/blob cache.
        self.write(cold_path, cold)
        self.commit()
        revisit_tree = self.git("rev-parse", "HEAD^{tree}")
        unrelated_edits(2)

        new_directory = "governance/retained/decisions"
        new_policy = "governance/retained/policy.json"
        (self.root / new_policy).parent.mkdir(parents=True)
        self.git("mv", self.records_path, new_directory)
        self.git("rm", self.policy_path)
        self.write(new_policy, {
            **self.policy, "records_directory": new_directory,
            "approved_on": "2026-10-03",
        })
        self.commit()
        migration = self.git("rev-parse", "HEAD")
        approvals = [f"{migration}={self.policy_path},{new_policy}"]
        migrated_cold = f"{new_directory}/oversized-0000.json"
        self.git("rm", migrated_cold)
        self.commit()
        removed = self.git("rev-parse", "HEAD")
        self.write(migrated_cold, cold)
        self.commit()
        unrelated_edits(2)
        # Committed history, not the checkout, remains authoritative.
        (self.root / new_policy).write_text("Dirty checkout policy\n", encoding="utf-8")
        del cold

        def fingerprint():
            return {
                path.relative_to(self.root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in self.root.rglob("*") if path.is_file() and not path.is_symlink()
            } | self.repository_links()

        before = fingerprint()
        measurements = {}
        for strategy in ("uncached", "byte-bounded"):
            with self.subTest(strategy=strategy):
                child = subprocess.run(
                    [sys.executable, "-c",
                     "import json, runpy, sys; "
                     "probe = runpy.run_path(sys.argv[1])['large_record_memory_probe']; "
                     "print(json.dumps(probe(*json.loads(sys.argv[2]))))",
                     str(Path(__file__).resolve()),
                     json.dumps([str(self.root), self.policy_path, baseline, approvals, strategy,
                                 {"budget_bytes": budget_bytes, "cold_oid": cold_oid}])],
                    capture_output=True, text=True, encoding="utf-8", timeout=180,
                )
                self.assertEqual(child.returncode, 0, child.stderr)
                measurements[strategy] = json.loads(child.stdout)
                self.assertEqual(fingerprint(), before)
                print(f"\nOversized wide-stable probe (multilingual={multilingual}): " + json.dumps({
                    key: value for key, value in measurements[strategy].items()
                    if key not in ("result", "observations")
                }, sort_keys=True))

        reference, bounded = measurements["uncached"], measurements["byte-bounded"]
        total_records = record_count + 1  # Includes the original small decision.
        expected_holds = [{
            "commit": commit, "ref": "refs/recovery/oversized-0000",
            "reason": "rewritten-record",
            "changed_fields": ["evidence"],
        } for commit in rewrites] + [{
            "commit": removed, "ref": "refs/recovery/oversized-0000",
            "reason": "removed-record", "record_file": cold_path,
        }]
        self.assertEqual(bounded["result"], reference["result"])
        self.assertEqual(bounded["result"], {
            "passed": False, "baseline": baseline, "head": self.git("rev-parse", "HEAD"),
            "history_scope": "first-parent", "commits_checked": stable_commits + 11,
            "retained_record_count": total_records, "added_record_count": 0,
            "policy_file": new_policy, "approved_migrations": [{
                "commit": migration, "from_policy": self.policy_path, "to_policy": new_policy,
                "status": "owner-approved-policy-migration",
            }], "holds": expected_holds,
        })
        # Full per-tree validated evidence must match, including both rewritten
        # versions and the restored original, not merely the final report.
        evidence_keys = ("tree", "policy", "records", "records_digest")
        self.assertEqual(
            [[state[key] for key in evidence_keys] for state in bounded["observations"]],
            [[state[key] for key in evidence_keys] for state in reference["observations"]],
        )
        for observed in bounded["observations"]:
            self.assertLessEqual(observed["budget_used"], budget_bytes)
            self.assertEqual(observed["historical_snapshots"], 0)
            self.assertEqual(observed["oversized_blobs_in_history"], 0)
        states = bounded["observations"]
        self.assertEqual(len(states), bounded["result"]["commits_checked"])
        self.assertEqual(states[0]["records"], total_records)
        for state in states[1:stable_commits + 1]:
            self.assertEqual(state["records"], total_records)
            self.assertEqual(state["blob_reads"], 0)
            self.assertEqual(state["validations"], 0)
        # Third version has displaced the original cold blob; returning to the
        # original requires exactly one reread/revalidation, not a wide rescan.
        self.assertFalse(states[stable_commits + 2]["cold_in_working_sets"])
        self.assertTrue(all(not state["cold_in_history"] for state in states))
        revisit = states[stable_commits + 3]
        self.assertEqual(revisit["tree"], revisit_tree)
        self.assertEqual(revisit["blob_reads"], 1)
        self.assertEqual(revisit["validations"], 1)
        self.assertEqual(revisit["records_digest"], states[0]["records_digest"])
        # All other roots, including post-restore and post-migration roots,
        # reuse the active evidence without a cyclic wide reread/revalidation.
        changed_states = {0, stable_commits + 1, stable_commits + 2,
                          stable_commits + 3, stable_commits + 6}
        for index, state in enumerate(states):
            if index not in changed_states:
                self.assertEqual(state["blob_reads"], 0, index)
                self.assertEqual(state["validations"], 0, index)
        # Only initial records, two new versions, the cold revisit and the new
        # policy need reading. All stable/migrated decisions retain validation.
        self.assertEqual(bounded["blob_reads"], total_records + 5)
        self.assertEqual(bounded["validations"], total_records + 3)
        for measured in measurements.values():
            self.assertGreater(measured["heap_peak_bytes"], 0)
        # RSS (including allocator/tracing overhead) is diagnostic only.

    def test_history_byte_accounting_counts_shared_snapshot_objects_once(self):
        ledger = audit.retirement_ledger
        budget = ledger.HistoryReuseBudget(64 * 1024)
        blobs = ledger.HistoryObjectCache(8, budget=budget)
        snapshots = ledger.HistoryObjectCache(8, budget=budget)
        record = {"evidence": "界" * 4096}
        key = "blob-identity"
        blobs[key] = record
        first_weight = sys.getsizeof(key) + sys.getsizeof(record)
        first_weight += sum(sys.getsizeof(item) for item in
                            ("evidence", record["evidence"]))
        self.assertEqual(budget.used_bytes, first_weight)
        state = ({"format": 1}, {"ref": record}, {"ref": "decisions/old.json"})
        state_key = ("tree", "policy")
        snapshots[state_key] = state
        # Independent union of reachable identities, including shared keys.
        objects = {}

        def visit(obj):
            if id(obj) in objects:
                return
            objects[id(obj)] = obj
            if isinstance(obj, dict):
                for key, value in obj.items():
                    visit(key)
                    visit(value)
            elif isinstance(obj, tuple):
                for item in obj:
                    visit(item)

        for item in (key, record, state_key, state):
            visit(item)
        self.assertEqual(budget.used_bytes, sum(map(sys.getsizeof, objects.values())))
        self.assertEqual(budget.objects[id(record)].references, 2)
        del blobs[key]
        self.assertIn(id(record), budget.objects)
        self.assertEqual(budget.objects[id(record)].references, 1)
        snapshots.clear()
        self.assertEqual(budget.used_bytes, 0)
        self.assertFalse(budget.objects)
        self.assertFalse(budget.entries)

    def test_history_accounting_reuses_payload_edges_for_wide_snapshots(self):
        ledger = audit.retirement_ledger
        for allowance in (4, 8):
            with self.subTest(allowance_mib=allowance):
                budget = ledger.HistoryReuseBudget(allowance * 1024**2)
                snapshots = ledger.HistoryObjectCache(32, budget=budget)
                records = {
                    f"refs/recovery/{number}": {
                        "evidence": f"Reviewed decision {number}",
                        "approved_by": "Owner",
                    } for number in range(1025)
                }
                paths = {ref: f"decisions/{number}.json"
                         for number, ref in enumerate(records)}
                for revision in range(32):
                    snapshots[(f"tree-{revision}", "policy")] = (
                        {"format": 1}, dict(records), dict(paths),
                    )
                # Independently total every reachable payload identity.
                payload = {}
                pending = [item for key, value in snapshots.items() for item in (key, value)]
                while pending:
                    obj = pending.pop()
                    if id(obj) in payload:
                        continue
                    payload[id(obj)] = obj
                    if isinstance(obj, dict):
                        pending.extend(obj.keys())
                        pending.extend(obj.values())
                    elif isinstance(obj, tuple):
                        pending.extend(obj)
                self.assertEqual(budget.used_bytes, sum(map(sys.getsizeof, payload.values())))
                self.assertEqual(len(snapshots), 32)
                counts = Counter()
                sample_history_accounting(type("Reader", (), {"reuse_budget": budget})(), counts)
                # Reconstruct the previous representation's owned node/edge
                # storage for this identical payload graph, not a second cache.
                previous = counts["bookkeeping_peak_bytes"]
                empty_edges = False
                pointer_bytes = sys.getsizeof((None,)) - sys.getsizeof(())
                for node in budget.objects.values():
                    children = sum(1 for _ in ledger.history_children(node.obj))
                    previous += sys.getsizeof([None] * 4) - sys.getsizeof(node)
                    if children:
                        previous += (sys.getsizeof(())
                                     + children * (pointer_bytes + sys.getsizeof(id(node))))
                    else:
                        empty_edges = True
                    size = sys.getsizeof(node.obj)
                    if size > 256:
                        previous += sys.getsizeof(size)
                    self.assertFalse(hasattr(node, "__dict__"))
                if empty_edges:
                    previous += sys.getsizeof(())
                self.assertLess(counts["bookkeeping_peak_bytes"], previous * 0.65)
                print(f"\nWide accounting {allowance} MiB allowance: "
                      f"{budget.used_bytes / 1024**2:.2f} MiB payload; "
                      f"{counts['bookkeeping_peak_bytes'] / 1024**2:.2f} MiB bookkeeping "
                      f"versus {previous / 1024**2:.2f} MiB stored-edge representation")
                snapshots.clear()
                self.assertEqual(budget.used_bytes, 0)
                self.assertFalse(budget.objects)
                self.assertFalse(budget.entries)

    def test_history_accounting_counts_repeated_edges_and_identical_roots(self):
        ledger = audit.retirement_ledger
        budget = ledger.HistoryReuseBudget(64 * 1024)
        cache = ledger.HistoryObjectCache(8, budget=budget)
        shared = ("shared",)
        value = {"list": [shared, shared], "tuple": (shared, shared),
                 "set": {shared}, "frozen": frozenset({shared})}
        cache["first"] = value
        cache["second"] = shared
        self.assertEqual(budget.objects[id(shared)].references, 7)
        del cache["first"]
        self.assertEqual(budget.objects[id(shared)].references, 1)
        cache[shared] = shared
        self.assertEqual(budget.objects[id(shared)].references, 3)
        del cache["second"]
        self.assertEqual(budget.objects[id(shared)].references, 2)
        cache.clear()
        self.assertEqual(budget.used_bytes, 0)
        self.assertFalse(budget.objects)
        self.assertFalse(budget.entries)

    def test_history_cache_seeded_sequences_match_independent_identity_model(self):
        ledger = audit.retirement_ledger

        def children(obj):
            # Deliberately independent of history_children/graph/accounting.
            if isinstance(obj, dict):
                return [*obj.keys(), *obj.values()]
            if isinstance(obj, (list, tuple, set, frozenset)):
                return list(obj)
            return []

        def reachable(roots):
            found, pending = {}, list(roots)
            while pending:
                obj = pending.pop()
                if id(obj) not in found:
                    found[id(obj)] = obj
                    pending.extend(children(obj))
            return found

        def weight(objects):
            return sum(sys.getsizeof(obj) for obj in objects.values())

        def fingerprint(roots):
            # Preserve both contents and alias topology, not just equality.
            return {
                identity: (type(obj), tuple(map(id, children(obj))),
                           None if isinstance(obj, (dict, list, tuple, set, frozenset))
                           else obj)
                for identity, obj in reachable(roots).items()
            }

        for seed in (554, 2026, 8675309):
            rng = random.Random(seed)
            shared = ("共用-é-🙂", frozenset({"承認", "approved"}))
            record = {"evidence": ["界" * 73, shared, shared],
                      "labels": {"保存", "keep"}}
            external = {"current": record, "policy": {"format": 1}}
            payloads = [
                {"record": record, "tail": character * (170 + number * 31),
                 "repeated": [shared, record, shared]}
                for number, character in enumerate(("a", "é", "界", "🙂", "λ", "🦊"))
            ]
            payloads += [
                (external["policy"], {"ref": record}, {"ref": "decisions/保存.json"}),
                [record, shared, payloads[0], payloads[0]],
                shared, True,
            ]
            keys = [
                [f"blob-{number}-界" for number in range(9)],
                [f"validated-{number}-é" for number in range(9)],
                [(f"tree-{number}-🙂", "policy") for number in range(9)],
            ]
            protected = [external, *payloads, *[key for group in keys for key in group]]
            before = fingerprint(protected)
            largest = max(weight(reachable((key, value)))
                          for group in keys for key in group for value in payloads)
            limits = (3, 4, 2)
            coverage = Counter()

            for allowance in (weight(reachable(protected)) * 2, largest * 2):
                with self.subTest(seed=seed, allowance=allowance):
                    budget = ledger.HistoryReuseBudget(allowance)
                    caches = [ledger.HistoryObjectCache(limit, budget=budget)
                              for limit in limits]
                    # Plain dicts and a chronological list form the oracle;
                    # never consult cache state to choose an eviction victim.
                    model = [{} for _ in limits]
                    recency = []

                    def live_objects():
                        return reachable(item for entries in model
                                         for pair in entries.items() for item in pair)

                    def remove(index, key):
                        del model[index][key]
                        recency.remove((index, key))

                    def touch(index, key):
                        value = model[index].pop(key)
                        model[index][key] = value
                        recency.remove((index, key))
                        recency.append((index, key))

                    def check():
                        objects = live_objects()
                        self.assertEqual(budget.used_bytes, weight(objects))
                        self.assertLessEqual(budget.used_bytes, allowance)
                        self.assertEqual(set(budget.objects), set(objects))
                        for identity, obj in objects.items():
                            self.assertIs(budget.objects[identity].obj, obj)
                        # Roots plus edges of unique live containers, including
                        # repeated edges, independently explain every refcount.
                        references = Counter(
                            id(item) for entries in model
                            for pair in entries.items() for item in pair
                        )
                        for obj in objects.values():
                            references.update(map(id, children(obj)))
                        self.assertEqual(
                            {identity: node.references
                             for identity, node in budget.objects.items()},
                            dict(references),
                        )
                        self.assertEqual(
                            list(budget.entries),
                            [(caches[index].identity, key) for index, key in recency],
                        )
                        for index, cache in enumerate(caches):
                            self.assertEqual(list(cache), list(model[index]))
                            self.assertLessEqual(len(cache), limits[index])
                            # items() must not touch either LRU while checking.
                            for (key, value), (expected_key, expected_value) in zip(
                                    cache.items(), model[index].items()):
                                self.assertIs(key, expected_key)
                                self.assertIs(value, expected_value)
                                owner, roots = budget.entries[(cache.identity, key)]
                                self.assertIs(owner, cache)
                                self.assertEqual(roots, (id(key), id(value)))
                        self.assertEqual(fingerprint(protected), before)

                    def apply(operation, index, key=None, value=None):
                        cache, entries = caches[index], model[index]
                        coverage[operation] += 1
                        if operation == "put":
                            if key in entries:
                                coverage["replacement"] += 1
                                remove(index, key)
                            candidate = reachable((key, value))
                            self.assertLessEqual(weight(candidate), allowance)
                            while weight({**live_objects(), **candidate}) > allowance:
                                victim_index, victim_key = recency[0]
                                remove(victim_index, victim_key)
                                coverage["global_eviction"] += 1
                            entries[key] = value
                            recency.append((index, key))
                            if len(entries) > limits[index]:
                                remove(index, next(iter(entries)))
                                coverage["local_eviction"] += 1
                            cache[key] = value
                        elif operation in ("get", "contains", "touch"):
                            present = key in entries
                            coverage[f"{operation}_{'hit' if present else 'miss'}"] += 1
                            if operation == "contains":
                                self.assertEqual(key in cache, present)
                            elif present:
                                if operation == "get":
                                    self.assertIs(cache[key], entries[key])
                                else:
                                    cache.touch(key)
                            else:
                                with self.assertRaises(KeyError):
                                    cache[key] if operation == "get" else cache.touch(key)
                            if present:
                                touch(index, key)
                        elif operation == "delete":
                            if key in entries:
                                remove(index, key)
                                del cache[key]
                            else:
                                with self.assertRaises(KeyError):
                                    del cache[key]
                        else:
                            for old_key in list(entries):
                                remove(index, old_key)
                            cache.clear()
                        check()

                    check()
                    # Guarantee sharing and count pressure in every cache
                    # before the seeded mixed replacement/byte-pressure stream.
                    for index in range(3):
                        for key in keys[index][:limits[index] + 2]:
                            apply("put", index, key, shared)
                    operations = ("put", "get", "contains", "touch", "delete", "clear")
                    for step in range(600):
                        operation = rng.choices(operations, (50, 13, 12, 10, 12, 3))[0]
                        index = rng.randrange(3)
                        key = rng.choice(keys[index])
                        value = rng.choice(payloads) if operation == "put" else None
                        with self.subTest(step=step, operation=operation, cache=index):
                            apply(operation, index, key, value)
                    for index in range(3):
                        apply("clear", index)
                    self.assertEqual(budget.used_bytes, 0)
                    self.assertFalse(budget.objects)
                    self.assertFalse(budget.entries)
                    self.assertTrue(all(not cache for cache in caches))
                    self.assertEqual(fingerprint(protected), before)
            # Fixed seeds must actually exercise these paths, not just make
            # them possible. Both eviction mechanisms recur many times.
            for event in ("replacement", "local_eviction", "global_eviction",
                          "get_hit", "get_miss", "contains_hit", "contains_miss",
                          "touch_hit", "touch_miss", "delete", "clear"):
                self.assertGreater(coverage[event], 5, (seed, event, coverage))

    def test_history_byte_budget_evicts_across_caches_and_preserves_recent_hits(self):
        ledger = audit.retirement_ledger
        budget = ledger.HistoryReuseBudget(1800)
        blobs = ledger.HistoryObjectCache(8, budget=budget)
        snapshots = ledger.HistoryObjectCache(8, budget=budget)
        for cache, key, character in (
            (blobs, "a", "a"), (snapshots, "b", "b"),
        ):
            cache[key] = character * 700
        self.assertIn("a", blobs)
        blobs["c"] = "c" * 700
        self.assertIn("a", blobs)
        self.assertNotIn("b", snapshots)
        self.assertLessEqual(budget.used_bytes, budget.maxbytes)
        blobs["a"] = "replacement"
        self.assertEqual(blobs["a"], "replacement")
        self.assertEqual(len(blobs), 2)
        blobs.clear()
        self.assertEqual(budget.used_bytes, 0)
        self.assertFalse(budget.entries)

    def test_history_oversized_objects_bypass_without_flushing_or_rejection(self):
        ledger = audit.retirement_ledger
        budget = ledger.HistoryReuseBudget(1024)
        cache = ledger.HistoryObjectCache(2, budget=budget)
        cache["small"] = "kept"
        before = budget.used_bytes
        # sys.getsizeof reflects Unicode width, not just character counts.
        cache["wide"] = "界" * 512
        self.assertNotIn("wide", cache)
        self.assertEqual(cache["small"], "kept")
        self.assertEqual(budget.used_bytes, before)
        cache["small"] = "x" * 2048
        self.assertNotIn("small", cache)
        self.assertEqual(budget.used_bytes, 0)
        empty = ledger.HistoryObjectCache(0, budget=budget)
        empty["not-retained"] = True
        self.assertFalse(empty)
        self.assertFalse(budget.objects)

    def test_tiny_history_budget_preserves_oversized_active_ledger_and_evidence(self):
        ledger = audit.retirement_ledger
        baseline = self.git("rev-parse", "HEAD")
        self.write(self.record_path, {**self.record, "evidence": "界" * 4096})
        self.commit()
        for revision in range(3):
            self.write("unrelated.json", {"revision": revision})
            self.commit()
        self.write(self.record_path, self.record)
        self.commit()
        migration, new_policy, new_record = self.migrate()
        approvals = [f"{migration}={self.policy_path},{new_policy}"]
        self.git("rm", new_record)
        self.commit()
        original_init = ledger.HistoryLedgerReader.__init__
        readers = []

        def tiny_init(reader, root):
            original_init(reader, root)
            reader.reuse_budget.maxbytes = 1
            readers.append(reader)

        expected = ledger.audit_history(self.root, self.policy_path, baseline, approvals)
        with patch.object(ledger.HistoryLedgerReader, "__init__", tiny_init):
            result = ledger.audit_history(self.root, self.policy_path, baseline, approvals)
        self.assertEqual(result, expected)
        self.assertEqual(len(result["approved_migrations"]), 1)
        self.assertTrue(any(hold["reason"] == "rewritten-record" for hold in result["holds"]))
        self.assertTrue(any(hold["reason"] == "removed-record" for hold in result["holds"]))
        for reader in readers:
            self.assertEqual(reader.reuse_budget.used_bytes, 0)
            self.assertFalse(reader.state.cache)
            self.assertTrue(reader.current_blobs)
            self.assertTrue(reader.previous_blobs)

    def test_history_object_caches_evict_cold_entries_not_recent_hits(self):
        ledger = audit.retirement_ledger
        for cache in (ledger.HistoryLedgerReader(self.root).blobs,
                      ledger.HistoryLedgerReader(self.root).validated_records):
            with self.subTest(limit=cache.maxsize):
                for number in range(cache.maxsize):
                    cache.add(str(number))
                self.assertIn("0", cache)
                cache.add("new")
                self.assertEqual(len(cache), cache.maxsize)
                self.assertIn("0", cache)
                self.assertNotIn("1", cache)
                cache["new"] = {"updated": True}
                self.assertEqual(len(cache), cache.maxsize)
                self.assertEqual(cache["new"], {"updated": True})

    def test_repeated_tree_reads_are_reused_without_dropping_commits(self):
        baseline = self.git("rev-parse", "HEAD")
        for _ in range(40):
            self.commit()
        ledger = audit.retirement_ledger
        with patch.object(ledger, "history_git", wraps=ledger.history_git) as reads:
            result = ledger.audit_history(self.root, self.policy_path, baseline)
        counts = Counter(call.args[1] for call in reads.call_args_list)
        self.assertEqual(counts["ls-tree"], 1)
        self.assertEqual(counts["cat-file"], 2)
        self.assertEqual(result["commits_checked"], 41)
        self.assertTrue(result["passed"])
        self.assert_cached_history_equivalent(baseline)

    def test_cached_history_preserves_all_transient_holds_and_dirty_checkout(self):
        baseline = self.git("rev-parse", "HEAD")
        expected = []
        for content, reason in (
            (None, "removed-record"),
            ({**self.record, "evidence": "Temporary rewrite."}, "rewritten-record"),
            ("{invalid}", "invalid-ledger"),
        ):
            path = self.root / self.record_path
            if content is None:
                path.unlink()
            elif isinstance(content, str):
                path.write_text(content, encoding="utf-8")
            else:
                self.write(self.record_path, content)
            for _ in range(2):
                self.commit()
                expected.append((self.git("rev-parse", "HEAD"), reason))
            self.write(self.record_path, self.record)
            self.commit()
        # History must ignore uncommitted files and leave them alone.
        (self.root / self.policy_path).write_text("Dirty policy\n", encoding="utf-8")
        (self.root / "untracked.txt").write_text("Keep this\n", encoding="utf-8")
        result = self.assert_cached_history_equivalent(baseline)
        self.assertEqual([(hold["commit"], hold["reason"]) for hold in result["holds"]], expected)
        self.assertFalse(result["passed"])

    def test_cached_history_preserves_additions_and_approved_migrations(self):
        baseline = self.git("rev-parse", "HEAD")
        added = {**self.record, "ref": "refs/recovery/added"}
        self.write(self.records_path + "/added.json", added)
        self.commit()
        (self.root / (self.records_path + "/added.json")).unlink()
        self.commit()
        removal = self.git("rev-parse", "HEAD")
        self.write(self.records_path + "/added.json", added)
        self.commit()
        destination = self.root / "governance/retained/decisions"
        destination.mkdir(parents=True)
        self.git("mv", self.records_path + "/added.json",
                 "governance/retained/decisions/added.json")
        migration, new_policy, _ = self.migrate()
        approvals = [f"{migration}={self.policy_path},{new_policy}"]
        self.commit()
        result = self.assert_cached_history_equivalent(baseline, approvals)
        self.assertEqual(result["added_record_count"], 1)
        self.assertEqual(result["holds"], [{
            "commit": removal, "ref": added["ref"], "reason": "removed-record",
            "record_file": self.records_path + "/added.json",
        }])
        self.assertEqual(len(result["approved_migrations"]), 1)

    def test_cached_history_keys_include_policy_location_and_mode(self):
        baseline = self.git("rev-parse", "HEAD")
        other = "governance/other-policy.json"
        self.write(other, self.policy)
        self.commit()
        # The migration commit has the same tree as its parent but a different
        # selected policy. Tree identity alone must not suppress that migration.
        self.commit()
        migration = self.git("rev-parse", "HEAD")
        result = self.assert_cached_history_equivalent(
            baseline, [f"{migration}={self.policy_path},{other}"],
        )
        self.assertTrue(result["passed"])
        self.git("update-index", "--chmod=+x", self.record_path)
        self.git("commit", "-m", "Executable regular record")
        executable = self.git("rev-parse", "HEAD")
        # A cached blob is still rejected if the same identity is a symlink.
        oid = self.git("rev-parse", f"HEAD:{self.record_path}")
        self.git("update-index", "--cacheinfo", f"120000,{oid},{self.record_path}")
        self.git("commit", "-m", "Redirected record")
        invalid = self.git("rev-parse", "HEAD")
        self.git("read-tree", executable)
        self.git("commit", "-m", "Restore regular record")
        result = self.assert_cached_history_equivalent(
            baseline, [f"{migration}={self.policy_path},{other}"],
        )
        self.assertEqual(result["holds"][0]["commit"], invalid)
        self.assertEqual(result["holds"][0]["reason"], "invalid-ledger")

    def test_cached_unchanged_migration_still_requires_a_policy_change(self):
        baseline = self.git("rev-parse", "HEAD")
        self.commit()
        migration = self.git("rev-parse", "HEAD")
        approvals = [f"{migration}={self.policy_path},{self.policy_path}"]
        ledger = audit.retirement_ledger
        with self.assertRaisesRegex(ledger.LedgerError, "does not identify a policy change"):
            ledger.audit_history(self.root, self.policy_path, baseline, approvals)

    def test_cached_objects_do_not_survive_into_a_later_audit(self):
        baseline = self.git("rev-parse", "HEAD")
        ledger = audit.retirement_ledger
        with patch.object(ledger, "history_git", wraps=ledger.history_git) as reads:
            first = ledger.audit_history(self.root, self.policy_path, baseline)
            second = ledger.audit_history(self.root, self.policy_path, baseline)
        self.assertEqual(first, second)
        counts = Counter(call.args[1] for call in reads.call_args_list)
        self.assertEqual(counts["ls-tree"], 2)
        self.assertEqual(counts["cat-file"], 4)
        self.write(self.record_path, {**self.record, "approver": "changed-owner"})
        self.commit()
        self.assertFalse(ledger.audit_history(self.root, self.policy_path, baseline)["passed"])

    def test_history_retains_old_records_without_ref_or_object_lookup(self):
        self.git("update-ref", "-d", self.ref)
        # History retention is independent of live object reachability.
        self.record["protected_commit"] = "f" * 40
        self.write(self.record_path, self.record)
        self.commit()
        baseline = self.git("rev-parse", "HEAD")
        self.git("commit", "--allow-empty", "-m", "Later review")
        before_refs = self.git("show-ref")
        before_status = self.git("status", "--porcelain")
        result, data = self.history(baseline)
        self.assertEqual(result.returncode, 0, data)
        self.assertEqual(data["retirement_history"]["retained_record_count"], 1)
        self.assertEqual(data["retirement_history"]["commits_checked"], 2)
        self.assertEqual(before_refs, self.git("show-ref"))
        self.assertEqual(before_status, self.git("status", "--porcelain"))

    def test_history_catches_deleted_record_even_after_restoration(self):
        baseline = self.git("rev-parse", "HEAD")
        self.git("update-ref", "-d", self.ref)
        (self.root / self.record_path).unlink()
        self.commit()
        deletion = self.git("rev-parse", "HEAD")
        self.write(self.record_path, self.record)
        self.commit()
        result, data = self.history(baseline)
        self.assertNotEqual(result.returncode, 0, data)
        self.assertIn(
            {"commit": deletion, "ref": self.ref, "reason": "removed-record",
             "record_file": self.record_path},
            data["retirement_history"]["holds"],
        )

    def test_history_catches_each_substantive_field_rewrite(self):
        baseline = self.git("rev-parse", "HEAD")
        self.git("update-ref", "-d", self.ref)
        changes = {
            "evidence": "Different retained work.",
            "protected_commit": "f" * 40,
            "decision_date": "2026-10-03",
            "approver": "another-owner",
        }
        for field, value in changes.items():
            with self.subTest(field=field):
                self.write(self.record_path, {**self.record, field: value})
                self.commit()
                changed_commit = self.git("rev-parse", "HEAD")
                result, data = self.history(baseline)
                self.assertNotEqual(result.returncode, 0, data)
                self.assertIn(
                    {"commit": changed_commit, "ref": self.ref,
                     "reason": "rewritten-record", "changed_fields": [field]},
                    data["retirement_history"]["holds"],
                )
                self.write(self.record_path, self.record)
                self.commit()

    def test_history_ignores_json_presentation_and_filename_changes(self):
        baseline = self.git("rev-parse", "HEAD")
        new_path = self.records_path + "/renamed.json"
        self.git("mv", self.record_path, new_path)
        (self.root / new_path).write_text(
            json.dumps(dict(reversed(list(self.record.items()))), separators=(",", ":")),
            encoding="utf-8",
        )
        self.commit()
        result, data = self.history(baseline)
        self.assertEqual(result.returncode, 0, data)
        self.assertEqual(data["retirement_history"]["holds"], [])

    def test_history_tracks_records_added_after_baseline(self):
        baseline = self.git("rev-parse", "HEAD")
        second = {**self.record, "ref": "refs/recovery/older-work"}
        second_path = self.records_path + "/second.json"
        self.write(second_path, second)
        self.commit()
        result, data = self.history(baseline)
        self.assertEqual(result.returncode, 0, data)
        self.assertEqual(data["retirement_history"]["added_record_count"], 1)
        (self.root / second_path).unlink()
        self.commit()
        result, data = self.history(baseline)
        self.assertNotEqual(result.returncode, 0, data)
        self.assertEqual(data["retirement_history"]["holds"][0]["ref"], second["ref"])

    def test_history_accepts_exact_approved_location_migration(self):
        baseline = self.git("rev-parse", "HEAD")
        self.git("update-ref", "-d", self.ref)
        migration, new_policy, _ = self.migrate()
        result, data = self.history(baseline)
        self.assertNotEqual(result.returncode, 0, data)
        self.assertEqual(data["retirement_history"]["holds"][0]["reason"], "invalid-ledger")
        self.git("commit", "--allow-empty", "-m", "Later review")
        result, data = self.history(
            baseline, "--approve-ledger-migration",
            f"{migration}={self.policy_path},{new_policy}",
        )
        self.assertEqual(result.returncode, 0, data)
        self.assertEqual(data["retirement_history"]["approved_migrations"], [{
            "commit": migration, "from_policy": self.policy_path,
            "to_policy": new_policy, "status": "owner-approved-policy-migration",
        }])

    def assert_retention_failure(self, proof, holds, hidden=()):
        # Verification must fail even when the ref deletion is otherwise
        # fully approved and bound to a fresh before-snapshot.
        self.snapshot.unlink()
        result, data = self.cli("--snapshot-recovery", str(self.snapshot))
        self.assertEqual(result.returncode, 0, data)
        for mode in ("preflight", "verification"):
            with self.subTest(mode=mode):
                if mode == "verification":
                    self.git("update-ref", "-d", self.ref, self.sha)
                refs = self.git("show-ref")
                status = self.git("status", "--porcelain")
                try:
                    if mode == "preflight":
                        check = lambda: self.proven_preflight(proof)
                    else:
                        check = lambda: self.cli(
                            "--verify-recovery", str(self.snapshot),
                            "--retirement-ledger", self.policy_path, *proof,
                            "--approve-recovery-retirement", self.allowance(),
                        )
                    result, data = self.assert_read_only_retirement_check(check)
                    self.assertEqual(result.returncode, 1, data)
                    self.assertIn("retention holds", data["error"])
                    self.assertEqual(set(data), {"error", "root", "retention_holds"})
                    self.assertEqual(data["retention_holds"], holds)
                    for text in hidden:
                        self.assertNotIn(text, result.stdout + result.stderr)
                    self.assertEqual(refs, self.git("show-ref"))
                    self.assertEqual(status, self.git("status", "--porcelain"))
                finally:
                    if mode == "verification":
                        self.git("update-ref", self.ref, self.sha)

    def test_failed_retirement_snapshot_detects_checkout_git_and_recovery_writes(self):
        proof, old_path = self.new_retirement_after_migration()
        self.git("rm", old_path)
        self.commit()
        self.snapshot.unlink()
        result, data = self.cli("--snapshot-recovery", str(self.snapshot))
        self.assertEqual(result.returncode, 0, data)
        for mode in ("preflight", "verification"):
            if mode == "verification":
                self.git("update-ref", "-d", self.ref, self.sha)
            try:
                for relative in (
                    self.record_path, ".git/index", ".git/refs/heads/main",
                    ".git/logs/HEAD", ".git/objects/audit-write-probe", "before.json",
                ):
                    with self.subTest(mode=mode, path=relative):
                        path = self.root / relative
                        previous = path.read_bytes() if path.exists() else None

                        def writing_check():
                            if mode == "preflight":
                                result, data = self.proven_preflight(proof)
                            else:
                                result, data = self.cli(
                                    "--verify-recovery", str(self.snapshot),
                                    "--retirement-ledger", self.policy_path, *proof,
                                    "--approve-recovery-retirement", self.allowance(),
                                )
                            self.assertEqual(result.returncode, 1, data)
                            self.assertIn("retention_holds", data)
                            path.write_bytes((previous or b"") + b"\naudit write probe\n")
                            return result, data

                        try:
                            with self.assertRaisesRegex(AssertionError, "retirement check changed"):
                                self.assert_read_only_retirement_check(writing_check)
                        finally:
                            if previous is None:
                                path.unlink(missing_ok=True)
                            else:
                                path.write_bytes(previous)
            finally:
                if mode == "verification":
                    self.git("update-ref", self.ref, self.sha)

    def test_migrated_old_decision_allows_new_retirement_and_verification(self):
        proof, _ = self.new_retirement_after_migration()
        refs = self.git("show-ref")
        status = self.git("status", "--porcelain")
        result, data = self.proven_preflight(proof)
        self.assertEqual(result.returncode, 0, data)
        ledger = data["retirement_ledger"]
        self.assertEqual(ledger["record_count"], 2)
        self.assertEqual(ledger["decisions"][0]["ref"], self.ref)
        self.assertTrue(ledger["migration_provenance"]["passed"])
        self.assertEqual(refs, self.git("show-ref"))
        self.assertEqual(status, self.git("status", "--porcelain"))
        self.snapshot.unlink()
        result, data = self.cli("--snapshot-recovery", str(self.snapshot))
        self.assertEqual(result.returncode, 0, data)
        self.git("update-ref", "-d", self.ref, self.sha)
        result, data = self.cli(
            "--verify-recovery", str(self.snapshot),
            "--retirement-ledger", self.policy_path, *proof,
            "--approve-recovery-retirement", self.allowance(),
        )
        self.assertEqual(result.returncode, 0, data)
        self.assertTrue(data["recovery_guard"]["passed"])
        self.assertTrue(data["recovery_guard"]["retirement_ledger"]["migration_provenance"]["passed"])

    def test_migrated_preflight_requires_complete_exact_proof(self):
        proof, _ = self.new_retirement_after_migration()
        for partial in ((), proof[:2], proof[2:4], proof[4:], proof[:4], proof[2:]):
            with self.subTest(proof=partial):
                result, data = self.proven_preflight(partial)
                self.assertNotEqual(result.returncode, 0, data)
                self.assertIn("error", data)
        for index, value in (
            (1, "missing-baseline"),
            (3, "governance/wrong/policy.json"),
            (5, proof[5].split("=")[0] + "=governance/wrong/policy.json," + self.policy_path),
        ):
            incorrect = list(proof)
            incorrect[index] = value
            result, data = self.proven_preflight(incorrect)
            self.assertNotEqual(result.returncode, 0, data)

    def test_migration_proof_does_not_excuse_new_backdated_decision(self):
        proof, _ = self.new_retirement_after_migration()
        self.record["decision_date"] = "2026-10-02"
        self.write(self.record_path, self.record)
        self.commit()
        self.assert_retention_failure(proof, [
            {"commit": self.git("rev-parse", "HEAD"), "ref": self.ref,
             "reason": "rewritten-record", "changed_fields": ["decision_date"]},
        ])

    def test_migration_proof_blocks_each_old_field_rewrite_even_if_restored(self):
        proof, old_path = self.new_retirement_after_migration()
        original = json.loads((self.root / old_path).read_text())
        for field, value in (
            ("evidence", "Changed evidence."),
            ("protected_commit", "f" * 40),
            ("decision_date", "2026-10-03"),
            ("approver", "another-owner"),
        ):
            with self.subTest(field=field):
                # Start each case at the same clean post-migration state.
                anchor = self.git("rev-parse", "HEAD")
                self.write(old_path, {**original, field: value})
                self.commit()
                holds = [{
                    "commit": self.git("rev-parse", "HEAD"), "ref": original["ref"],
                    "reason": "rewritten-record", "changed_fields": [field],
                }]
                self.assert_retention_failure(proof, holds, [value])
                self.write(old_path, original)
                self.commit()
                self.assert_retention_failure(proof, holds, [value])
                self.git("reset", "--hard", anchor)

    def test_failed_proofs_report_all_rewrites_without_old_or_new_free_text(self):
        proof, old_path = self.new_retirement_after_migration()
        original = json.loads((self.root / old_path).read_text())
        changed = {
            **original, "evidence": "Private replacement evidence.",
            "approver": "private-replacement-owner",
        }
        self.write(old_path, changed)
        self.commit()
        first = self.git("rev-parse", "HEAD")
        self.git("commit", "--allow-empty", "-m", "Still rewritten")
        second = self.git("rev-parse", "HEAD")
        self.write(old_path, original)
        self.commit()
        self.assert_retention_failure(proof, [
            {"commit": commit, "ref": original["ref"], "reason": "rewritten-record",
             "changed_fields": ["approver", "evidence"]}
            for commit in (first, second)
        ], [original["evidence"], original["approver"],
            changed["evidence"], changed["approver"]])

    def test_failed_proofs_redact_removed_record_paths_even_after_restoration(self):
        unsafe_path = self.records_path + "/token=synthetic-filename-secret.json"
        self.git("mv", self.record_path, unsafe_path)
        self.record_path = unsafe_path
        self.commit()
        proof, old_path = self.new_retirement_after_migration()
        original = json.loads((self.root / old_path).read_text())
        self.git("rm", old_path)
        self.commit()
        offending = self.git("rev-parse", "HEAD")
        holds = [{"commit": offending, "ref": original["ref"], "reason": "removed-record"}]
        self.assert_retention_failure(proof, holds, [
            "synthetic-filename-secret", original["evidence"],
        ])
        self.write(old_path, original)
        self.commit()
        self.assert_retention_failure(proof, holds, ["synthetic-filename-secret"])

    @unittest.skipUnless(os.name == "posix", "raw non-UTF-8 filenames require POSIX")
    def test_unrelated_non_utf8_filename_keeps_migration_checks_reliable(self):
        # Use OS byte paths, not a locale-dependent text filename. Keep this
        # outside the ledger so only Git tree enumeration encounters it.
        filename = b"unrelated-private-\xff.txt"
        with self.assertRaises(UnicodeDecodeError):
            filename.decode("utf-8")
        content = b"Unrelated private fixture content.\n"
        byte_path = os.fsencode(self.root) + b"/" + filename
        with open(byte_path, "wb") as stream:
            stream.write(content)
        added = subprocess.run(
            [b"git", b"add", b"--", filename],
            cwd=self.root, capture_output=True,
        )
        self.assertEqual(added.returncode, 0, added.stderr)
        self.commit()
        tree = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", "-z", "HEAD"],
            cwd=self.root, capture_output=True,
        )
        self.assertEqual(tree.returncode, 0, tree.stderr)
        self.assertIn(filename, tree.stdout.split(b"\0"))

        proof, old_path = self.new_retirement_after_migration()
        original = json.loads((self.root / old_path).read_text(encoding="utf-8"))
        self.snapshot.unlink()
        result, data = self.cli("--snapshot-recovery", str(self.snapshot))
        self.assertEqual(result.returncode, 0, data)
        objects = subprocess.run(
            ["git", "rev-list", "--all", "--objects"],
            cwd=self.root, capture_output=True,
        )
        self.assertEqual(objects.returncode, 0, objects.stderr)
        expected_objects = sorted({
            line.split(maxsplit=1)[0].decode("ascii")
            for line in objects.stdout.splitlines() if line
        })
        saved = json.loads(self.snapshot.read_text(encoding="utf-8"))
        self.assertEqual(saved["reachable_objects"], expected_objects)
        for mode in ("preflight", "verification"):
            with self.subTest(stage="valid", mode=mode):
                if mode == "verification":
                    self.git("update-ref", "-d", self.ref, self.sha)
                try:
                    if mode == "preflight":
                        check = lambda: self.proven_preflight(proof)
                    else:
                        check = lambda: self.cli(
                            "--verify-recovery", str(self.snapshot),
                            "--retirement-ledger", self.policy_path, *proof,
                            "--approve-recovery-retirement", self.allowance(),
                        )
                    result, data = self.assert_read_only_retirement_check(check)
                    self.assertEqual(result.returncode, 0, data)
                    ledger = (data["retirement_ledger"] if mode == "preflight"
                              else data["recovery_guard"]["retirement_ledger"])
                    self.assertTrue(ledger["migration_provenance"]["passed"])
                    self.assertEqual(ledger["migration_provenance"]["holds"], [])
                    if mode == "verification":
                        self.assertTrue(data["recovery_guard"]["passed"])
                finally:
                    if mode == "verification":
                        self.git("update-ref", self.ref, self.sha)

        self.git("rm", old_path)
        self.commit()
        holds = [{
            "commit": self.git("rev-parse", "HEAD"), "ref": original["ref"],
            "reason": "removed-record",
        }]
        hidden = ["unrelated-private-", content.decode("ascii").strip(),
                  original["evidence"], original["approver"]]
        with self.subTest(stage="removed"):
            self.assert_retention_failure(proof, holds, hidden)
        self.write(old_path, original)
        self.commit()
        with self.subTest(stage="restored"):
            self.assert_retention_failure(proof, holds, hidden)
        with open(byte_path, "rb") as stream:
            self.assertEqual(stream.read(), content)

    def assert_retained_record_filename_redacted(
        self, filename, private_marker="synthetic-international-filename-secret",
    ):
        # Keep ref identities ASCII: this exercises Git tree path decoding,
        # not international recovery-ref spelling.
        baseline_path = self.records_path + "/" + filename
        self.git("mv", self.record_path, baseline_path)
        self.record_path = baseline_path
        self.commit()
        proof, migrated_path = self.new_retirement_after_migration()
        retained_path = str(Path(migrated_path).with_name(filename))
        self.git("mv", migrated_path, retained_path)
        self.commit()
        original = json.loads((self.root / retained_path).read_text(encoding="utf-8"))
        self.assertTrue(original["ref"].isascii())
        self.assertTrue(self.ref.isascii())

        # A valid proof must first read both unusual tree paths before
        # a committed removal can be identified as a retention violation.
        result, data = self.proven_preflight(proof)
        self.assertEqual(result.returncode, 0, data)
        self.assertTrue(data["retirement_ledger"]["migration_provenance"]["passed"])
        self.git("rm", retained_path)
        self.commit()
        holds = [{
            "commit": self.git("rev-parse", "HEAD"), "ref": original["ref"],
            "reason": "removed-record",
        }]
        hidden = [
            baseline_path, migrated_path, retained_path, filename,
            original["evidence"], original["approver"],
            private_marker,
        ]
        # Include JSON-escaped spellings so escaped historical paths cannot
        # bypass the raw stdout/stderr checks in assert_retention_failure.
        hidden += [json.dumps(text)[1:-1] for text in hidden]
        self.assert_retention_failure(proof, holds, hidden)
        self.write(retained_path, original)
        self.commit()
        self.assert_retention_failure(proof, holds, hidden)

    def test_failed_proofs_redact_international_record_filename_after_restoration(self):
        self.assert_retained_record_filename_redacted("décision-東京.json")

    def test_failed_proofs_redact_private_international_record_filename_after_restoration(self):
        self.assert_retained_record_filename_redacted(
            "de\u0301cision-復旧-🚀-token=synthetic-international-filename-secret.json",
        )

    def test_failed_proofs_redact_private_tab_record_filename_after_restoration(self):
        # A tab in the filename must not become another ls-tree metadata field.
        self.assert_retained_record_filename_redacted(
            "decision\t-token=synthetic-control-filename-secret.json",
            private_marker="synthetic-control-filename-secret",
        )

    def test_failed_proofs_redact_private_newline_record_filename_after_restoration(self):
        # A newline in the filename must not become another Git tree entry.
        self.assert_retained_record_filename_redacted(
            "decision\n-token=synthetic-control-filename-secret.json",
            private_marker="synthetic-control-filename-secret",
        )

    def prepare_international_retained_decision(self, retained_ref):
        # Only the older retained decision is international; the newly
        # requested retirement stays ASCII and uses the normal migration proof.
        self.git("check-ref-format", retained_ref)
        self.git("update-ref", retained_ref, self.sha)
        self.git("update-ref", "-d", self.ref, self.sha)
        self.ref = retained_ref
        self.record = {**self.record, "ref": retained_ref}
        self.write(self.record_path, self.record)
        self.commit()
        result, data = self.preflight()
        self.assertEqual(result.returncode, 0, data)
        self.assertEqual(data["retirement_ledger"]["decisions"][0]["ref"], retained_ref)
        proof, old_path = self.new_retirement_after_migration()
        original = json.loads((self.root / old_path).read_text(encoding="utf-8"))
        self.assertEqual(original["ref"], retained_ref)
        self.assertNotEqual(self.ref, retained_ref)
        result, data = self.proven_preflight(proof)
        self.assertEqual(result.returncode, 0, data)
        self.assertTrue(data["retirement_ledger"]["migration_provenance"]["passed"])
        return proof, old_path, original

    def test_failed_proofs_preserve_international_removed_ref_even_after_restoration(self):
        proof, old_path, original = self.prepare_international_retained_decision(
            "refs/recovery/récupération-東京",
        )
        self.git("rm", old_path)
        self.commit()
        holds = [{
            "commit": self.git("rev-parse", "HEAD"), "ref": original["ref"],
            "reason": "removed-record",
        }]
        self.assert_retention_failure(proof, holds, [original["evidence"]])
        self.write(old_path, original)
        self.commit()
        self.assert_retention_failure(proof, holds, [original["evidence"]])

    def test_failed_proofs_preserve_international_rewritten_ref_even_after_restoration(self):
        proof, old_path, original = self.prepare_international_retained_decision(
            "refs/recovery/re\u0301cupe\u0301ration-復旧-🚀",
        )
        changed = {
            **original, "evidence": "Private international replacement evidence.",
            "approver": "private-international-replacement-owner",
        }
        self.write(old_path, changed)
        self.commit()
        holds = [{
            "commit": self.git("rev-parse", "HEAD"), "ref": original["ref"],
            "reason": "rewritten-record", "changed_fields": ["approver", "evidence"],
        }]
        hidden = [
            original["evidence"], original["approver"],
            changed["evidence"], changed["approver"],
        ]
        self.assert_retention_failure(proof, holds, hidden)
        self.write(old_path, original)
        self.commit()
        self.assert_retention_failure(proof, holds, hidden)

    def prepare_canonically_equivalent_retained_decisions(self):
        retained_refs = (
            "refs/recovery/r\u00e9cup\u00e9ration",
            "refs/recovery/re\u0301cupe\u0301ration",
        )
        self.assertNotEqual(*retained_refs)
        self.assertEqual(*(unicodedata.normalize("NFC", ref) for ref in retained_refs))
        originals = [{**self.record, "ref": ref} for ref in retained_refs]
        old_paths = (self.record_path, self.records_path + "/second-combining.json")
        for ref, path, record in zip(retained_refs, old_paths, originals):
            self.git("check-ref-format", ref)
            self.git("update-ref", ref, self.sha)
            self.write(path, record)
        self.git("update-ref", "-d", self.ref, self.sha)
        self.commit()
        baseline = self.git("rev-parse", "HEAD")
        old_policy = self.policy_path

        # Retain both exact identities across the approved location migration.
        self.git("rm", old_policy, *old_paths)
        self.policy_path = "governance/retained/policy.json"
        new_directory = "governance/retained/decisions"
        paths = tuple(new_directory + "/" + Path(path).name for path in old_paths)
        self.write(self.policy_path, {
            **self.policy, "records_directory": new_directory,
            "approved_on": "2026-10-03",
        })
        for path, record in zip(paths, originals):
            self.write(path, record)
        self.commit()
        migration = self.git("rev-parse", "HEAD")
        proof = (
            "--ledger-baseline", baseline,
            "--ledger-baseline-policy", old_policy,
            "--approve-ledger-migration",
            f"{migration}={old_policy},{self.policy_path}",
        )
        self.ref = "refs/recovery/new-work"
        self.record = {
            **self.record, "ref": self.ref, "decision_date": "2026-10-03",
        }
        self.record_path = new_directory + "/new.json"
        self.write(self.record_path, self.record)
        self.git("update-ref", self.ref, self.sha)
        self.commit()
        result, data = self.proven_preflight(proof)
        self.assertEqual(result.returncode, 0, data)
        ledger = data["retirement_ledger"]
        self.assertEqual(ledger["record_count"], 3)
        self.assertEqual(ledger["migration_provenance"]["retained_record_count"], 3)
        self.assertTrue(ledger["migration_provenance"]["passed"])
        return proof, retained_refs, paths, originals

    def test_failed_proofs_keep_canonically_equivalent_retained_refs_distinct(self):
        proof, retained_refs, paths, originals = (
            self.prepare_canonically_equivalent_retained_decisions()
        )
        holds = []
        for reason in ("removed-record", "rewritten-record"):
            with self.subTest(reason=reason):
                if reason == "removed-record":
                    self.git("rm", *paths)
                else:
                    for path, record in zip(paths, originals):
                        self.write(path, {**record, "evidence": "Changed paired evidence."})
                self.commit()
                offending = self.git("rev-parse", "HEAD")
                holds.extend({
                    "commit": offending, "ref": ref, "reason": reason,
                    **({"changed_fields": ["evidence"]} if reason == "rewritten-record" else {}),
                } for ref in retained_refs)
                hidden = [self.record["evidence"], "Changed paired evidence."]
                self.assert_retention_failure(proof, holds, hidden)
                for path, record in zip(paths, originals):
                    self.write(path, record)
                self.commit()
                self.assert_retention_failure(proof, holds, hidden)
                for ref in retained_refs:
                    self.assertEqual(self.git("rev-parse", "--verify", ref), self.sha)

    def test_failed_proofs_bind_mixed_findings_to_exact_canonically_equivalent_refs(self):
        proof, retained_refs, paths, originals = (
            self.prepare_canonically_equivalent_retained_decisions()
        )
        anchor = self.git("rev-parse", "HEAD")
        for removed_index, removed_ref in enumerate(retained_refs):
            rewritten_index = 1 - removed_index
            for field, replacement in (
                ("evidence", "Private mixed-ref replacement evidence."),
                ("approver", "private-mixed-ref-replacement-owner"),
            ):
                with self.subTest(removed_ref=removed_ref, rewritten_field=field):
                    # Reverse the roles as well as the rewritten field: neither
                    # Unicode spelling may inherit its look-alike's finding.
                    self.git("reset", "--hard", anchor)
                    self.git("rm", paths[removed_index])
                    self.write(paths[rewritten_index], {
                        **originals[rewritten_index], field: replacement,
                    })
                    self.commit()
                    offending = self.git("rev-parse", "HEAD")
                    holds = sorted([
                        {
                            "commit": offending, "ref": removed_ref,
                            "reason": "removed-record",
                        },
                        {
                            "commit": offending, "ref": retained_refs[rewritten_index],
                            "reason": "rewritten-record", "changed_fields": [field],
                        },
                    ], key=lambda hold: retained_refs.index(hold["ref"]))
                    hidden = [
                        record[key] for record in originals
                        for key in ("evidence", "approver")
                    ] + [replacement]
                    # Full hold equality checks both CLI modes, including no
                    # changed_fields on the removal and the exact bad commit.
                    self.assert_retention_failure(proof, holds, hidden)
                    for path, record in zip(paths, originals):
                        self.write(path, record)
                    self.commit()
                    self.assert_retention_failure(proof, holds, hidden)
                    for ref in retained_refs:
                        self.assertEqual(self.git("rev-parse", "--verify", ref), self.sha)

    def test_failed_proofs_only_report_altered_canonically_equivalent_retained_ref(self):
        proof, retained_refs, paths, originals = (
            self.prepare_canonically_equivalent_retained_decisions()
        )
        anchor = self.git("rev-parse", "HEAD")
        for index, ref in enumerate(retained_refs):
            for reason in ("removed-record", "rewritten-record"):
                with self.subTest(ref=ref, reason=reason):
                    # Each spelling must fail alone, without historical holds
                    # from the other spelling or the previous mutation.
                    self.git("reset", "--hard", anchor)
                    unchanged_path = self.root / paths[1 - index]
                    unchanged_bytes = unchanged_path.read_bytes()
                    replacement = "Private single-ref replacement evidence."
                    if reason == "removed-record":
                        self.git("rm", paths[index])
                    else:
                        self.write(paths[index], {
                            **originals[index], "evidence": replacement,
                        })
                    self.commit()
                    holds = [{
                        "commit": self.git("rev-parse", "HEAD"),
                        "ref": ref, "reason": reason,
                        **({"changed_fields": ["evidence"]}
                           if reason == "rewritten-record" else {}),
                    }]
                    hidden = [record["evidence"] for record in originals] + [replacement]
                    # Exact hold-list equality checks both CLI modes, ruling
                    # out any hold for the unchanged look-alike or wrong commit.
                    self.assert_retention_failure(proof, holds, hidden)
                    self.assertEqual(unchanged_path.read_bytes(), unchanged_bytes)
                    self.write(paths[index], originals[index])
                    self.commit()
                    self.assert_retention_failure(proof, holds, hidden)
                    self.assertEqual(unchanged_path.read_bytes(), unchanged_bytes)
                    for retained_ref in retained_refs:
                        self.assertEqual(
                            self.git("rev-parse", "--verify", retained_ref), self.sha,
                        )

    def test_failed_proofs_never_echo_invalid_historical_text(self):
        proof, old_path = self.new_retirement_after_migration()
        original = json.loads((self.root / old_path).read_text())
        unsafe = "token=synthetic-historical-secret"
        changes = [
            {**original, "evidence": unsafe},
            {**original, "evidence": "https://example.invalid/review?signature=synthetic-url-secret"},
            {**original, "evidence": "Bearer synthetic-bearer-secret"},
            {**original, "approver": unsafe},
            {**original, "ref": "refs/recovery/" + unsafe},
            {**original, unsafe: "Private unexpected field value."},
        ]
        holds = []
        for change in changes:
            with self.subTest(fields=list(change)):
                self.write(old_path, change)
                self.commit()
                holds.append({"commit": self.git("rev-parse", "HEAD"),
                              "reason": "invalid-ledger"})
                self.write(old_path, original)
                self.commit()
                self.assert_retention_failure(proof, holds, [
                    "synthetic-historical-secret", "Private unexpected field value.",
                    "synthetic-url-secret", "synthetic-bearer-secret",
                    original["evidence"],
                ])
        (self.root / old_path).write_text(
            '{"evidence": "' + unsafe + '", broken}', encoding="utf-8",
        )
        self.commit()
        holds.append({"commit": self.git("rev-parse", "HEAD"), "reason": "invalid-ledger"})
        self.write(old_path, original)
        self.commit()
        self.assert_retention_failure(proof, holds, ["synthetic-historical-secret"])

    def test_failed_proofs_report_unapproved_policy_changes_without_approver_text(self):
        proof, _ = self.new_retirement_after_migration()
        policy = json.loads((self.root / self.policy_path).read_text())
        private_owner = "private-unapproved-policy-owner"
        self.write(self.policy_path, {**policy, "approved_by": private_owner})
        self.commit()
        self.assert_retention_failure(proof, [
            {"commit": self.git("rev-parse", "HEAD"), "reason": "unapproved-policy-change"},
        ], [private_owner])

    def test_migrated_sha1_retirement_rejects_sha256_length_protected_commit(self):
        proof, _ = self.new_retirement_after_migration()
        self.assertEqual(self.git("rev-parse", "--show-object-format"), "sha1")
        self.assertRegex(self.sha, r"^[0-9a-f]{40}$")
        result, data = self.proven_preflight(proof)
        self.assertEqual(result.returncode, 0, data)
        self.assertTrue(data["retirement_ledger"]["migration_provenance"]["passed"])
        self.snapshot.unlink()
        result, data = self.cli("--snapshot-recovery", str(self.snapshot))
        self.assertEqual(result.returncode, 0, data)
        snapshot_bytes = self.snapshot.read_bytes()
        self.assertEqual(json.loads(snapshot_bytes)["refs"][self.ref], self.sha)

        self.record["protected_commit"] = self.sha + "0" * 24
        self.assertRegex(self.record["protected_commit"], r"^[0-9a-f]{64}$")
        # Shared syntax accepts both hash lengths; repository binding must not.
        audit.retirement_ledger.validate_record(self.root, self.record)
        self.write(self.record_path, self.record)
        self.git("add", self.record_path)
        # Introduce the incorrect ID with the new decision, not as a later
        # rewrite whose retention hold could mask the exact-ID rejection.
        self.git("commit", "--amend", "--no-edit")
        for mode in ("preflight", "verification"):
            with self.subTest(mode=mode):
                if mode == "verification":
                    self.git("update-ref", "-d", self.ref, self.sha)
                result, data = self.assert_read_only_retirement_check(
                    lambda: self.proven_preflight(proof) if mode == "preflight"
                    else self.cli(
                        "--verify-recovery", str(self.snapshot),
                        "--retirement-ledger", self.policy_path, *proof,
                        "--approve-recovery-retirement", self.allowance(),
                    ),
                )
                self.assertEqual(result.returncode, 1, data)
                self.assertEqual(
                    data["error"],
                    "retirement ledger: protected commit differs from the pre-removal snapshot",
                )
                self.assertEqual(self.snapshot.read_bytes(), snapshot_bytes)

    def test_migration_proof_keeps_exact_retirement_binding(self):
        proof, _ = self.new_retirement_after_migration()
        result, data = self.proven_preflight(proof, self.ref + "=different evidence")
        self.assertNotEqual(result.returncode, 0, data)
        self.assertIn("evidence differs", data["error"])
        self.git("update-ref", self.ref, self.git("rev-parse", "HEAD"))
        result, data = self.proven_preflight(proof)
        self.assertNotEqual(result.returncode, 0, data)
        self.assertIn("protected commit differs", data["error"])
        self.git("update-ref", self.ref, self.sha)
        missing_ref = "refs/recovery/missing-object"
        missing_record = {
            **self.record, "ref": missing_ref, "protected_commit": "f" * 40,
        }
        self.write("governance/retained/decisions/missing.json", missing_record)
        self.commit()
        # Even with a matching synthetic snapshot SHA, a missing object fails.
        with self.assertRaisesRegex(audit.retirement_ledger.LedgerError, "resolve to a commit"):
            audit.retirement_ledger.validate_ledger(
                self.root, self.policy_path, {"refs": {missing_ref: "f" * 40}},
                [(missing_ref, missing_record["evidence"])],
                baseline=proof[1], baseline_policy=proof[3], migrations=[proof[5]],
            )

    def test_new_decision_in_migration_commit_cannot_be_backdated(self):
        baseline = self.git("rev-parse", "HEAD")
        migration, new_policy, _ = self.migrate()
        added = {**self.record, "ref": "refs/recovery/smuggled"}
        self.write("governance/retained/decisions/added.json", added)
        self.git("add", "governance")
        self.git("commit", "--amend", "--no-edit")
        migration = self.git("rev-parse", "HEAD")
        proof = (
            "--ledger-baseline", baseline, "--ledger-baseline-policy", self.policy_path,
            "--approve-ledger-migration", f"{migration}={self.policy_path},{new_policy}",
        )
        self.policy_path = new_policy
        result, data = self.proven_preflight(proof)
        self.assertNotEqual(result.returncode, 0, data)
        self.assertIn("retention holds", data["error"])

    def test_successive_migrations_retain_old_and_previously_new_decisions(self):
        proof, _ = self.new_retirement_after_migration()
        # A renewed approval at the same path is also a migration.
        policy = json.loads((self.root / self.policy_path).read_text())
        self.write(self.policy_path, {**policy, "approved_on": "2026-10-04"})
        self.commit()
        second = self.git("rev-parse", "HEAD")
        proof += ("--approve-ledger-migration",
                  f"{second}={self.policy_path},{self.policy_path}")
        result, data = self.proven_preflight(proof)
        self.assertEqual(result.returncode, 0, data)
        self.assertEqual(len(data["retirement_ledger"]["migration_provenance"]["approved_migrations"]), 2)
        # Omitting the renewed approval cannot authorize the current policy.
        result, data = self.proven_preflight(proof[:-2])
        self.assertNotEqual(result.returncode, 0, data)

    def test_provenance_must_end_at_selected_current_policy(self):
        proof, _ = self.new_retirement_after_migration()
        other = "governance/other/policy.json"
        current = json.loads((self.root / self.policy_path).read_text())
        self.write(other, current)
        self.commit()
        self.policy_path = other
        result, data = self.proven_preflight(proof)
        self.assertNotEqual(result.returncode, 0, data)
        self.assertIn("different policy", data["error"])

    def test_migration_proof_still_requires_unchanged_committed_files(self):
        proof, old_path = self.new_retirement_after_migration()
        original = json.loads((self.root / old_path).read_text())
        self.write(old_path, {**original, "evidence": "Dirty evidence."})
        result, data = self.proven_preflight(proof)
        self.assertNotEqual(result.returncode, 0, data)
        self.assertIn("committed unchanged", data["error"])

    def test_migration_proof_flags_cannot_be_ignored_in_other_modes(self):
        for args in (
            ("--ledger-baseline-policy", self.policy_path),
            ("--snapshot-recovery", str(self.root / "new-snapshot.json"),
             "--ledger-baseline-policy", self.policy_path),
            ("--verify-recovery", str(self.snapshot),
             "--ledger-baseline-policy", self.policy_path),
            ("--audit-retirement-history", self.policy_path,
             "--ledger-baseline", "HEAD", "--ledger-baseline-policy", self.policy_path),
        ):
            result, data = self.cli(*args)
            self.assertNotEqual(result.returncode, 0, data)
            self.assertIn("requires ledger preflight", data["error"])

    def test_history_policy_approval_cannot_excuse_rewritten_records(self):
        baseline = self.git("rev-parse", "HEAD")
        _, new_policy, new_record = self.migrate()
        self.write(new_record, {**self.record, "evidence": "Rewritten during migration."})
        self.git("add", "governance")
        self.git("commit", "--amend", "--no-edit")
        migration = self.git("rev-parse", "HEAD")
        result, data = self.history(
            baseline, "--approve-ledger-migration",
            f"{migration}={self.policy_path},{new_policy}",
        )
        self.assertNotEqual(result.returncode, 0, data)
        self.assertEqual(data["retirement_history"]["holds"][0]["reason"], "rewritten-record")

    def test_history_policy_edit_requires_exact_approval_even_at_same_path(self):
        baseline = self.git("rev-parse", "HEAD")
        self.write(self.policy_path, {**self.policy, "approved_by": "renewed-owner"})
        self.commit()
        migration = self.git("rev-parse", "HEAD")
        result, data = self.history(baseline)
        self.assertNotEqual(result.returncode, 0, data)
        self.assertEqual(data["retirement_history"]["holds"][0]["reason"], "unapproved-policy-change")
        result, data = self.history(
            baseline, "--approve-ledger-migration",
            f"{migration}={self.policy_path},{self.policy_path}",
        )
        self.assertEqual(result.returncode, 0, data)

    def test_history_missing_or_deleted_policy_and_invalid_json_fail(self):
        baseline = self.git("rev-parse", "HEAD")
        (self.root / self.policy_path).unlink()
        self.commit()
        result, data = self.history(baseline)
        self.assertNotEqual(result.returncode, 0, data)
        self.assertEqual(data["retirement_history"]["holds"][0]["reason"], "invalid-ledger")
        self.write(self.policy_path, self.policy)
        (self.root / self.record_path).write_text(
            '{"evidence":"token=synthetic-private-value"}', encoding="utf-8",
        )
        self.commit()
        result, data = self.history(baseline)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("synthetic-private-value", result.stdout)

    def test_history_baseline_is_explicit_valid_and_on_mainline(self):
        result, data = self.cli("--audit-retirement-history", self.policy_path)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--ledger-baseline", data["error"])
        result, data = self.history("missing-baseline")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unavailable", data["error"])
        self.git("checkout", "-b", "side")
        self.git("commit", "--allow-empty", "-m", "Side history")
        side = self.git("rev-parse", "HEAD")
        self.git("checkout", "main")
        result, data = self.history(side)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("first-parent", data["error"])
        result, data = self.history(self.sha)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing", data["error"])

    def test_history_reads_commits_not_dirty_checkout(self):
        baseline = self.git("rev-parse", "HEAD")
        self.write(self.record_path, {**self.record, "evidence": "Uncommitted edit."})
        (self.root / self.policy_path).unlink()
        before_status = self.git("status", "--porcelain")
        result, data = self.history(baseline)
        self.assertEqual(result.returncode, 0, data)
        self.assertEqual(data["retirement_history"]["commits_checked"], 1)
        self.assertEqual(before_status, self.git("status", "--porcelain"))

    def test_history_migration_allowances_fail_closed(self):
        baseline = self.git("rev-parse", "HEAD")
        self.git("commit", "--allow-empty", "-m", "No migration")
        current = self.git("rev-parse", "HEAD")
        for values in (
            ["bad"],
            [f"{baseline}={self.policy_path},{self.policy_path}"],
            [f"{current}=governance/wrong.json,{self.policy_path}"],
            [f"{current}={self.policy_path},{self.policy_path}"],
            [f"{current}={self.policy_path},{self.policy_path}"] * 2,
            [f"{current}={self.policy_path},../outside.json"],
        ):
            with self.subTest(values=values):
                args = [item for value in values for item in ("--approve-ledger-migration", value)]
                result, data = self.history(baseline, *args)
                self.assertNotEqual(result.returncode, 0, data)
                self.assertIn("error", data)

    def test_history_cannot_fetch_or_prepare_deletion(self):
        baseline = self.git("rev-parse", "HEAD")
        for args in (("--fetch",), ("--check-delete",), ("--hosted-branch", "origin=work")):
            result, data = self.history(baseline, *args)
            self.assertNotEqual(result.returncode, 0, data)
            self.assertIn("cannot be combined", data["error"])
        result, data = self.cli("--ledger-baseline", baseline)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("require --audit-retirement-history", data["error"])

    def test_history_merge_checks_integrated_state_not_unrelated_side_history(self):
        baseline = self.git("rev-parse", "HEAD")
        self.git("checkout", "-b", "side", self.sha)
        self.git("commit", "--allow-empty", "-m", "Pre-ledger side work")
        self.git("checkout", "main")
        self.git("merge", "--no-ff", "side", "-m", "Integrate side work")
        result, data = self.history(baseline)
        self.assertEqual(result.returncode, 0, data)
        self.assertEqual(data["retirement_history"]["commits_checked"], 2)

    def test_history_migrated_baseline_keeps_older_decision_dates(self):
        _, new_policy, _ = self.migrate()
        baseline = self.git("rev-parse", "HEAD")
        result, data = self.cli(
            "--audit-retirement-history", new_policy, "--ledger-baseline", baseline,
        )
        self.assertEqual(result.returncode, 0, data)
        self.assertEqual(data["retirement_history"]["retained_record_count"], 1)

    def test_history_rejects_redirected_and_duplicate_historical_records(self):
        baseline = self.git("rev-parse", "HEAD")
        path = self.root / self.record_path
        path.unlink()
        try:
            path.symlink_to("missing.json")
        except OSError as exc:
            if exc.errno in (1, 13) or getattr(exc, "winerror", None) == 1314:
                self.skipTest("symlink creation requires unavailable privileges")
            raise
        self.commit()
        result, data = self.history(baseline)
        self.assertNotEqual(result.returncode, 0, data)
        self.assertIn("regular blob", data["retirement_history"]["holds"][0]["detail"])
        path.unlink()
        path.write_text('{"format":1,"format":1}', encoding="utf-8")
        self.commit()
        result, data = self.history(baseline)
        self.assertNotEqual(result.returncode, 0, data)
        self.assertIn("duplicate JSON", data["retirement_history"]["holds"][-1]["detail"])


class SHA256RetirementLedgerTests(RetirementLedgerFixture):
    object_format = "sha256"

    # Run the identical real-Git workload and assertions with 64-character
    # objects, without inheriting every SHA-1 test. The expensive LRU-only
    # reference remains opt-in via RETIREMENT_LEDGER_ALTERNATING_LRU_BASELINE.
    test_alternating_wide_ledgers_avoid_scan_amplification = (
        RetirementLedgerTests.test_alternating_wide_ledgers_avoid_scan_amplification
    )
    test_recurring_wide_ledgers_reuse_blobs_across_approved_moves = (
        RetirementLedgerTests.test_recurring_wide_ledgers_reuse_blobs_across_approved_moves
    )

    def test_non_migrated_retirement_rejects_sha1_length_protected_commit(self):
        self.assertRegex(self.sha, r"^[0-9a-f]{64}$")
        snapshot_bytes = self.snapshot.read_bytes()
        self.assertEqual(json.loads(snapshot_bytes)["refs"][self.ref], self.sha)
        result, data = self.assert_read_only_retirement_check(self.preflight)
        self.assertEqual(result.returncode, 0, data)

        prefix = self.sha[:40]
        self.assertRegex(prefix, r"^[0-9a-f]{40}$")
        self.assertEqual(self.git("rev-parse", "--verify", prefix + "^{commit}"), self.sha)
        self.record["protected_commit"] = prefix
        # Shared syntax accepts SHA-1-length IDs; retirement must still bind
        # the decision to the exact full SHA-256 ID, not Git's resolved prefix.
        self.assert_read_only_retirement_check(
            lambda: audit.retirement_ledger.validate_record(self.root, self.record),
        )
        self.write(self.record_path, self.record)
        self.commit()
        for mode in ("preflight", "verification"):
            with self.subTest(mode=mode):
                if mode == "verification":
                    self.git("update-ref", "-d", self.ref, self.sha)
                else:
                    self.assertEqual(self.git("rev-parse", self.ref), self.sha)
                result, data = self.assert_read_only_retirement_check(
                    self.preflight if mode == "preflight" else self.verify,
                )
                self.assertEqual(result.returncode, 1, data)
                self.assertEqual(
                    data["error"],
                    "retirement ledger: protected commit differs from the pre-removal snapshot",
                )
                self.assertEqual(self.snapshot.read_bytes(), snapshot_bytes)

    def test_missing_record_blocks_completion_without_changing_recovery_evidence(self):
        self.assertRegex(self.sha, r"^[0-9a-f]{64}$")
        (self.root / self.record_path).unlink()
        self.commit()
        result, data = self.assert_read_only_retirement_check(self.preflight)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no retained decision", data["error"])

    def test_uncommitted_record_rejections_preserve_recovery_evidence(self):
        self.assertRegex(self.record["protected_commit"], r"^[0-9a-f]{64}$")
        self.record["approver"] = "different-owner"
        self.write(self.record_path, self.record)
        result, data = self.assert_read_only_retirement_check(self.preflight)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("committed unchanged", data["error"])

        self.git("rm", "--cached", self.record_path)
        self.git("commit", "-m", "Untrack record")
        result, data = self.assert_read_only_retirement_check(self.preflight)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("committed unchanged", data["error"])

    def test_mismatched_allowance_evidence_rejection_preserves_recovery_evidence(self):
        self.assertRegex(self.sha, r"^[0-9a-f]{64}$")
        self.git("update-ref", "-d", self.ref, self.sha)
        result, data = self.assert_read_only_retirement_check(
            lambda: self.verify(self.ref + "=different evidence"),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("evidence differs", data["error"])

    def test_unapproved_ref_removal_rejection_preserves_recovery_evidence(self):
        self.assertRegex(self.sha, r"^[0-9a-f]{64}$")
        result, data = self.assert_read_only_retirement_check(self.verify)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(data["recovery_guard"]["passed"])

        retained = "refs/remotes/origin/retained"
        self.git("update-ref", retained, self.sha)
        self.snapshot.unlink()
        result, data = self.cli("--snapshot-recovery", str(self.snapshot))
        self.assertEqual(result.returncode, 0, data)
        self.git("update-ref", "-d", self.ref, self.sha)
        self.git("update-ref", "-d", retained, self.sha)
        result, data = self.assert_read_only_retirement_check(self.verify)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(data["recovery_guard"]["passed"])
        self.assertIn(retained, data["recovery_guard"]["unexpected_removed_refs"])

    def test_history_snapshot_detects_checkout_and_git_acl_changes(self):
        baseline = self.git("rev-parse", "HEAD")
        expected = audit.retirement_ledger.audit_history(self.root, self.policy_path, baseline)
        self.assert_history_acls_detected(
            lambda: self.assert_history_report(baseline, expected), cli=True,
        )

    def test_history_snapshot_detects_identical_hard_link_replacement(self):
        baseline = self.git("rev-parse", "HEAD")
        expected = audit.retirement_ledger.audit_history(self.root, self.policy_path, baseline)
        self.assert_history_hard_link_changes_detected(
            lambda: self.assert_history_report(baseline, expected), cli=True,
        )

    def test_snapshot_preserves_self_referential_link_without_following(self):
        self.assert_snapshot_link_boundary("self")

    def test_snapshot_preserves_directory_link_cycle_without_following(self):
        self.assert_snapshot_link_boundary("cycle")

    def test_snapshot_never_traverses_or_reads_external_link_targets(self):
        self.assert_snapshot_link_boundary("external")

    def test_history_snapshot_detects_file_and_symlink_replacements(self):
        baseline = self.git("rev-parse", "HEAD")
        expected = audit.retirement_ledger.audit_history(self.root, self.policy_path, baseline)
        self.assert_history_link_type_changes_detected(
            lambda: self.assert_history_report(baseline, expected), cli=True,
        )

    def test_history_snapshot_detects_checkout_and_git_permission_changes(self):
        baseline = self.git("rev-parse", "HEAD")
        expected = audit.retirement_ledger.audit_history(self.root, self.policy_path, baseline)
        self.assert_history_modes_detected(
            lambda: self.assert_history_report(baseline, expected), cli=True,
        )

    def test_history_snapshot_detects_link_creation_removal_and_retargeting(self):
        baseline = self.git("rev-parse", "HEAD")
        expected = audit.retirement_ledger.audit_history(self.root, self.policy_path, baseline)
        self.assert_history_link_changes_detected(
            lambda: self.assert_history_report(baseline, expected), cli=True,
        )

    def test_fixture_disables_only_process_scoped_automatic_maintenance(self):
        self.assert_maintenance_is_isolated()

    def test_matching_sha256_snapshot_rejects_non_commit_protected_objects(self):
        ledger = audit.retirement_ledger
        cases = (
            ("blob", self.git("rev-parse", f"HEAD:{self.policy_path}")),
            ("tree", self.git("rev-parse", "HEAD^{tree}")),
            ("missing", "f" * 64),
        )
        for kind, object_id in cases:
            with self.subTest(kind=kind, object_id=object_id):
                self.assertRegex(object_id, r"^[0-9a-f]{64}$")
                object_type = subprocess.run(
                    ["git", "cat-file", "-t", object_id],
                    cwd=self.root, capture_output=True, text=True,
                )
                if kind == "missing":
                    self.assertNotEqual(object_type.returncode, 0)
                else:
                    self.assertEqual(object_type.returncode, 0, object_type.stderr)
                    self.assertEqual(object_type.stdout.strip(), kind)
                self.record["protected_commit"] = object_id
                self.write(self.record_path, self.record)
                self.commit()
                # Match the full ID so snapshot binding cannot mask the
                # independent protected-object type/existence check.
                before = {"refs": {self.ref: object_id}}
                with self.assertRaisesRegex(
                    ledger.LedgerError,
                    r"^retirement ledger: protected object must resolve to a commit$",
                ):
                    ledger.validate_ledger(
                        self.root, self.policy_path, before,
                        [(self.ref, self.record["evidence"])],
                    )

    def test_history_snapshot_detects_checkout_and_git_writes(self):
        baseline = self.git("rev-parse", "HEAD")
        expected = audit.retirement_ledger.audit_history(self.root, self.policy_path, baseline)
        self.assert_history_writes_detected(
            lambda: self.assert_history_report(baseline, expected),
        )

    def assert_history_report(self, baseline, expected, migrations=(), read_counts=None):
        ledger = audit.retirement_ledger

        def snapshot():
            # Check dirty checkout files and all Git metadata, not just refs.
            return self.repository_bytes()

        before = snapshot()
        with patch.object(ledger, "history_git", wraps=ledger.history_git) as reads, \
                patch.object(ledger, "validate_record", wraps=ledger.validate_record) as validations:
            cached = ledger.audit_history(self.root, self.policy_path, baseline, migrations)
        self.assertEqual(snapshot(), before)
        self.assertEqual(cached, expected)
        if read_counts is not None:
            counts = Counter(call.args[1] for call in reads.call_args_list)
            self.assertEqual(
                (counts["ls-tree"], counts["cat-file"], validations.call_count),
                read_counts,
            )
        for call in reads.call_args_list:
            if call.args[1] in ("ls-tree", "cat-file"):
                self.assertRegex(call.args[-1], r"^[0-9a-f]{64}$")
        for key in ("baseline", "head"):
            self.assertRegex(cached[key], r"^[0-9a-f]{64}$")
        for hold in cached["holds"]:
            self.assertRegex(hold["commit"], r"^[0-9a-f]{64}$")

        def uncached(reader, tree, policy_path):
            return ledger.tree_ledger(reader.root, tree, policy_path)

        with patch.object(ledger.HistoryLedgerReader, "read", uncached):
            reference = ledger.audit_history(self.root, self.policy_path, baseline, migrations)
        self.assertEqual(snapshot(), before)
        self.assertEqual(reference, cached)
        approvals = [arg for value in migrations for arg in ("--approve-ledger-migration", value)]
        result, data = self.cli(
            "--audit-retirement-history", self.policy_path,
            "--ledger-baseline", baseline, *approvals,
        )
        self.assertEqual(result.returncode, 0 if expected["passed"] else 1, data)
        self.assertEqual(data["retirement_history"], cached)
        self.assertEqual(snapshot(), before)

    def history_report(self, baseline, commits_checked, **overrides):
        return {
            "passed": True, "baseline": baseline, "head": self.git("rev-parse", "HEAD"),
            "history_scope": "first-parent", "commits_checked": commits_checked,
            "retained_record_count": 1, "added_record_count": 0,
            "policy_file": self.policy_path, "approved_migrations": [], "holds": [],
            **overrides,
        }

    def test_history_reuses_full_sha256_trees_and_blobs(self):
        baseline = self.git("rev-parse", "HEAD")
        for _ in range(2):
            self.commit()
        unrelated = self.root / "content.txt"
        unrelated.write_text("Unrelated content\n", encoding="utf-8")
        self.git("add", "content.txt")
        self.commit()
        self.commit()
        self.assertEqual(self.git("rev-parse", "--show-object-format"), "sha256")
        tree = self.git("rev-parse", "HEAD^{tree}")
        self.assertRegex(tree, r"^[0-9a-f]{64}$")
        policy, records, paths = audit.retirement_ledger.tree_ledger(
            self.root, tree, self.policy_path,
        )
        self.assertEqual(policy, self.policy)
        self.assertEqual(records, {self.ref: self.record})
        self.assertRegex(records[self.ref]["protected_commit"], r"^[0-9a-f]{64}$")
        self.assertEqual(paths, {self.ref: self.record_path})
        # Two distinct trees, but the same policy and decision blobs throughout.
        self.assert_history_report(
            baseline, self.history_report(baseline, 5), read_counts=(2, 2, 1),
        )

    def test_history_keeps_sha256_deletion_holds_after_restoration_read_only(self):
        baseline = self.git("rev-parse", "HEAD")
        self.git("update-ref", "-d", self.ref, self.sha)
        (self.root / self.record_path).unlink()
        removals = []
        for _ in range(2):
            self.commit()
            removals.append(self.git("rev-parse", "HEAD"))
        self.write(self.record_path, self.record)
        self.commit()
        # The audit must use committed blobs, not these staged/dirty files.
        self.write(self.record_path, {**self.record, "evidence": "Staged change."})
        self.git("add", self.record_path)
        (self.root / self.policy_path).write_text("Dirty policy\n", encoding="utf-8")
        (self.root / "untracked.txt").write_text("Keep this\n", encoding="utf-8")
        holds = [{
            "commit": commit, "ref": self.ref, "reason": "removed-record",
            "record_file": self.record_path,
        } for commit in removals]
        self.assert_history_report(
            baseline, self.history_report(baseline, 4, passed=False, holds=holds),
            read_counts=(2, 2, 1),
        )

    def test_history_keeps_sha256_invalid_json_errors_after_restoration_read_only(self):
        baseline = self.git("rev-parse", "HEAD")
        invalid_commits = []
        for _ in range(2):
            (self.root / self.record_path).write_text("{invalid}", encoding="utf-8")
            for _ in range(2):
                self.commit()
                invalid_commits.append(self.git("rev-parse", "HEAD"))
            self.write(self.record_path, self.record)
            self.commit()
        self.assertEqual(len(set(invalid_commits)), 4)
        # Staged, dirty and untracked bytes must survive all three audit paths.
        self.write(self.record_path, {**self.record, "evidence": "Staged change."})
        self.git("add", self.record_path)
        (self.root / self.policy_path).write_text("Dirty policy\n", encoding="utf-8")
        (self.root / "untracked.txt").write_text("Keep this\n", encoding="utf-8")
        holds = [{
            "commit": commit, "reason": "invalid-ledger",
            "detail": "retirement history: could not read valid UTF-8 JSON",
        } for commit in invalid_commits]
        self.assert_history_report(
            baseline, self.history_report(baseline, 7, passed=False, holds=holds),
            read_counts=(2, 3, 1),
        )

    def test_history_keeps_sha256_rewrite_holds_after_restoration_read_only(self):
        baseline = self.git("rev-parse", "HEAD")
        rewritten_commits = []
        for _ in range(2):
            self.write(self.record_path, {
                **self.record, "approver": "other-owner", "evidence": "Temporary rewrite.",
            })
            for _ in range(2):
                self.commit()
                rewritten_commits.append(self.git("rev-parse", "HEAD"))
            self.write(self.record_path, self.record)
            self.commit()
        self.assertEqual(len(set(rewritten_commits)), 4)
        self.write(self.record_path, {**self.record, "evidence": "Staged change."})
        self.git("add", self.record_path)
        (self.root / self.policy_path).write_text("Dirty policy\n", encoding="utf-8")
        (self.root / "untracked.txt").write_text("Keep this\n", encoding="utf-8")
        holds = [{
            "commit": commit, "ref": self.ref, "reason": "rewritten-record",
            "changed_fields": ["approver", "evidence"],
        } for commit in rewritten_commits]
        self.assert_history_report(
            baseline, self.history_report(baseline, 7, passed=False, holds=holds),
            read_counts=(2, 3, 2),
        )

    def test_history_reports_exact_sha256_approved_migration_and_additions(self):
        baseline = self.git("rev-parse", "HEAD")
        migration, new_policy, _ = self.migrate()
        added = {
            **self.record, "ref": "refs/recovery/added",
            "decision_date": "2026-10-03",
        }
        self.write("governance/retained/decisions/added.json", added)
        self.commit()
        self.commit()
        self.assertRegex(migration, r"^[0-9a-f]{64}$")
        self.assert_history_report(
            baseline, self.history_report(
                baseline, 4, retained_record_count=2, added_record_count=1,
                policy_file=new_policy, approved_migrations=[{
                    "commit": migration, "from_policy": self.policy_path,
                    "to_policy": new_policy, "status": "owner-approved-policy-migration",
                }],
            ),
            [f"{migration}={self.policy_path},{new_policy}"],
            read_counts=(3, 4, 2),
        )

    def test_history_sha256_policy_edit_requires_exact_approval_even_at_same_path(self):
        baseline = self.git("rev-parse", "HEAD")
        snapshot_bytes = self.snapshot.read_bytes()
        # Reuse the baseline's immutable tree before changing policy contents.
        self.commit()
        self.assertEqual(
            self.git("rev-parse", "HEAD^{tree}"),
            self.git("rev-parse", f"{baseline}^{{tree}}"),
        )
        renewed_policy = {**self.policy, "approved_by": "renewed-owner"}
        self.write(self.policy_path, renewed_policy)
        self.commit()
        migration = self.git("rev-parse", "HEAD")
        self.assertRegex(migration, r"^[0-9a-f]{64}$")
        self.assertEqual(self.git("cat-file", "-t", migration), "commit")
        self.assertNotEqual(
            self.git("rev-parse", f"{baseline}:{self.policy_path}"),
            self.git("rev-parse", f"{migration}:{self.policy_path}"),
        )
        self.assertEqual(
            json.loads(self.git("show", f"{migration}:{self.policy_path}")),
            renewed_policy,
        )
        self.assertEqual(
            self.git("diff-tree", "--no-commit-id", "--name-only", "-r", migration),
            self.policy_path,
        )
        before = self.repository_bytes()
        with self.subTest(approved=False):
            self.assert_history_report(
                baseline, self.history_report(
                    baseline, 3, passed=False, holds=[{
                        "commit": migration, "reason": "unapproved-policy-change",
                    }],
                ),
                read_counts=(2, 3, 1),
            )
            self.assertEqual(self.repository_bytes(), before)
            self.assertEqual(self.snapshot.read_bytes(), snapshot_bytes)
        with self.subTest(approved=True):
            self.assert_history_report(
                baseline, self.history_report(
                    baseline, 3, approved_migrations=[{
                        "commit": migration, "from_policy": self.policy_path,
                        "to_policy": self.policy_path,
                        "status": "owner-approved-policy-migration",
                    }],
                ),
                [f"{migration}={self.policy_path},{self.policy_path}"],
                read_counts=(2, 3, 1),
            )
            self.assertEqual(self.repository_bytes(), before)
            self.assertEqual(self.snapshot.read_bytes(), snapshot_bytes)

    def test_cached_unchanged_sha256_migration_still_requires_a_policy_change(self):
        baseline = self.git("rev-parse", "HEAD")
        # Prime immutable-state reuse before reaching the approved commit.
        self.commit()
        self.commit()
        migration = self.git("rev-parse", "HEAD")
        self.assertRegex(migration, r"^[0-9a-f]{64}$")
        self.assertEqual(self.git("cat-file", "-t", migration), "commit")
        self.assertNotEqual(migration, baseline)
        for revision in (migration + "^", migration):
            self.assertEqual(
                self.git("rev-parse", f"{revision}^{{tree}}"),
                self.git("rev-parse", f"{baseline}^{{tree}}"),
            )
            self.assertEqual(
                self.git("show", f"{revision}:{self.policy_path}"),
                self.git("show", f"{baseline}:{self.policy_path}"),
            )
        approvals = [f"{migration}={self.policy_path},{self.policy_path}"]
        ledger = audit.retirement_ledger
        expected_error = (
            "retirement history: migration approval does not identify a policy change"
        )
        before = self.repository_bytes()
        with patch.object(ledger, "history_git", wraps=ledger.history_git) as reads, \
                patch.object(ledger, "validate_record", wraps=ledger.validate_record) as validations:
            with self.assertRaises(ledger.LedgerError) as cached:
                ledger.audit_history(self.root, self.policy_path, baseline, approvals)
        self.assertEqual(str(cached.exception), expected_error)
        counts = Counter(call.args[1] for call in reads.call_args_list)
        # Baseline, warm-up and approval share one parsed tree and its two blobs.
        self.assertEqual((counts["ls-tree"], counts["cat-file"], validations.call_count),
                         (1, 2, 1))
        self.assertEqual(self.repository_bytes(), before)

        def uncached(reader, tree, policy_path):
            return ledger.tree_ledger(reader.root, tree, policy_path)

        with patch.object(ledger.HistoryLedgerReader, "read", uncached):
            with self.assertRaises(ledger.LedgerError) as reference:
                ledger.audit_history(self.root, self.policy_path, baseline, approvals)
        self.assertEqual(str(reference.exception), str(cached.exception))
        self.assertEqual(self.repository_bytes(), before)
        for attempt in range(2):
            with self.subTest(cli_attempt=attempt):
                result, data = self.cli(
                    "--audit-retirement-history", self.policy_path,
                    "--ledger-baseline", baseline,
                    "--approve-ledger-migration", approvals[0],
                )
                self.assertEqual(result.returncode, 1, data)
                self.assertEqual(data["error"], expected_error)
                self.assertEqual(self.repository_bytes(), before)

    def test_history_rejects_sha256_migration_source_differing_from_active_policy_read_only(self):
        other = "governance/other-policy.json"
        self.write(other, self.policy)
        self.commit()
        baseline = self.git("rev-parse", "HEAD")
        # Warm the active policy's immutable state before the approved commit.
        self.commit()
        self.commit()
        migration = self.git("rev-parse", "HEAD")
        self.assertRegex(migration, r"^[0-9a-f]{64}$")
        self.assertEqual(self.git("cat-file", "-t", migration), "commit")
        self.assertNotEqual(migration, baseline)
        for revision in (baseline, migration + "^", migration):
            self.assertEqual(
                self.git("rev-parse", f"{revision}^{{tree}}"),
                self.git("rev-parse", f"{baseline}^{{tree}}"),
            )
            self.assertEqual(
                self.git("rev-parse", f"{revision}:{other}"),
                self.git("rev-parse", f"{revision}:{self.policy_path}"),
            )
        # Both paths exist with identical policy bytes, but only policy_path
        # is active. A valid commit approval cannot substitute the other source.
        approvals = [f"{migration}={other},{self.policy_path}"]
        ledger = audit.retirement_ledger
        expected_error = (
            "retirement history: migration source differs from active policy"
        )
        before = self.repository_bytes()
        snapshot_bytes = self.snapshot.read_bytes()
        with patch.object(ledger, "history_git", wraps=ledger.history_git) as reads, \
                patch.object(ledger, "validate_record", wraps=ledger.validate_record) as validations:
            with self.assertRaises(ledger.LedgerError) as cached:
                ledger.audit_history(self.root, self.policy_path, baseline, approvals)
        self.assertEqual(str(cached.exception), expected_error)
        counts = Counter(call.args[1] for call in reads.call_args_list)
        # Baseline and warm-up reuse one parsed tree before source rejection.
        self.assertEqual((counts["ls-tree"], counts["cat-file"], validations.call_count),
                         (1, 2, 1))
        self.assertEqual(self.repository_bytes(), before)
        self.assertEqual(self.snapshot.read_bytes(), snapshot_bytes)

        def uncached(reader, tree, policy_path):
            return ledger.tree_ledger(reader.root, tree, policy_path)

        with patch.object(ledger.HistoryLedgerReader, "read", uncached):
            with self.assertRaises(ledger.LedgerError) as reference:
                ledger.audit_history(self.root, self.policy_path, baseline, approvals)
        self.assertEqual(str(reference.exception), expected_error)
        self.assertEqual(self.repository_bytes(), before)
        self.assertEqual(self.snapshot.read_bytes(), snapshot_bytes)
        result, data = self.cli(
            "--audit-retirement-history", self.policy_path,
            "--ledger-baseline", baseline,
            "--approve-ledger-migration", approvals[0],
        )
        self.assertEqual(result.returncode, 1, data)
        self.assertEqual(data["error"], expected_error)
        self.assertEqual(self.repository_bytes(), before)
        self.assertEqual(self.snapshot.read_bytes(), snapshot_bytes)

    def test_history_keeps_sha256_policy_location_switch_in_unchanged_tree_read_only(self):
        other = "governance/other-policy.json"
        self.write(other, self.policy)
        self.commit()
        baseline = self.git("rev-parse", "HEAD")
        self.commit()
        migration = self.git("rev-parse", "HEAD")
        self.assertNotEqual(migration, baseline)
        self.assertEqual(
            self.git("rev-parse", f"{baseline}^{{tree}}"),
            self.git("rev-parse", f"{migration}^{{tree}}"),
        )
        self.assertEqual(
            self.git("rev-parse", f"HEAD:{self.policy_path}"),
            self.git("rev-parse", f"HEAD:{other}"),
        )
        self.assertRegex(migration, r"^[0-9a-f]{64}$")
        self.assert_history_report(
            baseline, self.history_report(
                baseline, 2, policy_file=other, approved_migrations=[{
                    "commit": migration, "from_policy": self.policy_path,
                    "to_policy": other, "status": "owner-approved-policy-migration",
                }],
            ),
            [f"{migration}={self.policy_path},{other}"],
            read_counts=(2, 2, 1),
        )

    def test_history_rejects_sha256_cached_record_blob_as_symlink_read_only(self):
        baseline = self.git("rev-parse", "HEAD")
        oid = self.git("rev-parse", f"HEAD:{self.record_path}")
        self.assertRegex(oid, r"^[0-9a-f]{64}$")
        # Both regular-file modes are valid and warm the same blob cache.
        self.git("update-index", "--chmod=+x", self.record_path)
        self.git("commit", "-m", "Executable regular record")
        executable = self.git("rev-parse", "HEAD")
        self.assert_history_report(
            baseline, self.history_report(baseline, 2), read_counts=(2, 2, 1),
        )
        # Change only the entry mode, not the cached decision's full object ID.
        self.git("update-index", "--cacheinfo", f"120000,{oid},{self.record_path}")
        self.git("commit", "-m", "Redirected record")
        invalid = self.git("rev-parse", "HEAD")
        self.assertEqual(
            self.git("ls-tree", "HEAD", "--", self.record_path),
            f"120000 blob {oid}\t{self.record_path}",
        )
        self.git("read-tree", executable)
        self.git("commit", "-m", "Restore regular record")
        self.assertEqual(
            self.git("ls-tree", "HEAD", "--", self.record_path),
            f"100755 blob {oid}\t{self.record_path}",
        )
        self.assert_history_report(
            baseline, self.history_report(baseline, 4, passed=False, holds=[{
                "commit": invalid, "reason": "invalid-ledger",
                "detail": "retirement history: policy or record is missing or not a regular blob",
            }]),
            read_counts=(3, 2, 1),
        )

    def test_history_rejects_sha256_cached_policy_blob_as_symlink_read_only(self):
        baseline = self.git("rev-parse", "HEAD")
        oid = self.git("rev-parse", f"HEAD:{self.policy_path}")
        self.assertRegex(oid, r"^[0-9a-f]{64}$")
        self.assertEqual(
            self.git("ls-tree", "HEAD", "--", self.policy_path),
            f"100644 blob {oid}\t{self.policy_path}",
        )
        # A second regular mode reuses the policy blob in a distinct tree.
        self.git("update-index", "--chmod=+x", self.policy_path)
        self.git("commit", "-m", "Executable regular policy")
        executable = self.git("rev-parse", "HEAD")
        self.assert_history_report(
            baseline, self.history_report(baseline, 2), read_counts=(2, 2, 1),
        )
        # Redirect the policy itself without changing its already-read bytes.
        self.git("update-index", "--cacheinfo", f"120000,{oid},{self.policy_path}")
        self.git("commit", "-m", "Redirected policy")
        invalid = self.git("rev-parse", "HEAD")
        invalid_tree = self.git("rev-parse", "HEAD^{tree}")
        self.assertRegex(invalid_tree, r"^[0-9a-f]{64}$")
        self.assertEqual(
            self.git("ls-tree", "HEAD", "--", self.policy_path),
            f"120000 blob {oid}\t{self.policy_path}",
        )
        # Failure reuse must retain separate evidence for consecutive commits
        # with the same tree, then again when that tree recurs after a repair.
        invalid_commits = [invalid]
        self.git("commit", "--allow-empty", "-m", "Repeat redirected policy")
        invalid_commits.append(self.git("rev-parse", "HEAD"))
        self.git("read-tree", executable)
        self.git("commit", "-m", "Restore policy between redirected occurrences")
        restored = self.git("rev-parse", "HEAD")
        self.assertEqual(
            self.git("rev-parse", f"{restored}^{{tree}}"),
            self.git("rev-parse", f"{executable}^{{tree}}"),
        )
        self.git("read-tree", invalid)
        self.git("commit", "-m", "Reintroduce redirected policy")
        invalid_commits.append(self.git("rev-parse", "HEAD"))
        self.git("commit", "--allow-empty", "-m", "Repeat reintroduced redirected policy")
        invalid_commits.append(self.git("rev-parse", "HEAD"))
        self.assertEqual(len(set(invalid_commits)), 4)
        for commit in invalid_commits:
            with self.subTest(commit=commit):
                self.assertRegex(commit, r"^[0-9a-f]{64}$")
                self.assertEqual(
                    self.git("rev-parse", f"{commit}^{{tree}}"), invalid_tree,
                )
        self.git("read-tree", executable)
        self.git("commit", "-m", "Restore regular policy")
        self.assertEqual(
            self.git("ls-tree", "HEAD", "--", self.policy_path),
            f"100755 blob {oid}\t{self.policy_path}",
        )
        # Committed history must ignore and preserve staged/dirty checkout data.
        self.write(self.record_path, {**self.record, "evidence": "Staged change."})
        self.git("add", self.record_path)
        (self.root / self.policy_path).write_text("Dirty policy\n", encoding="utf-8")
        (self.root / "untracked.txt").write_text("Keep this\n", encoding="utf-8")
        self.assert_read_only_retirement_check(
            lambda: self.assert_history_report(
                baseline, self.history_report(baseline, 8, passed=False, holds=[{
                    "commit": commit, "reason": "invalid-ledger",
                    "detail": "retirement history: policy or record is missing or not a regular blob",
                } for commit in invalid_commits]),
                read_counts=(3, 2, 1),
            ),
        )

    def test_history_rejects_sha256_symlink_policy_baseline_read_only(self):
        regular = self.git("rev-parse", "HEAD")
        oid = self.git("rev-parse", f"HEAD:{self.policy_path}")
        self.assertRegex(oid, r"^[0-9a-f]{64}$")
        self.git("update-index", "--cacheinfo", f"120000,{oid},{self.policy_path}")
        self.git("commit", "-m", "Redirected baseline policy")
        baseline = self.git("rev-parse", "HEAD")
        self.assertRegex(baseline, r"^[0-9a-f]{64}$")
        self.assertEqual(
            self.git("ls-tree", baseline, "--", self.policy_path),
            f"120000 blob {oid}\t{self.policy_path}",
        )
        # A valid later ledger cannot turn an invalid starting point into a
        # retention anchor. Keep the policy bytes identical across the repair.
        self.git("read-tree", regular)
        self.git("commit", "-m", "Restore regular policy after invalid baseline")
        self.assertEqual(
            self.git("ls-tree", "HEAD", "--", self.policy_path),
            f"100644 blob {oid}\t{self.policy_path}",
        )
        self.write(self.record_path, {**self.record, "evidence": "Staged change."})
        self.git("add", self.record_path)
        (self.root / self.policy_path).write_text("Dirty policy\n", encoding="utf-8")
        (self.root / "untracked.txt").write_text("Keep this\n", encoding="utf-8")

        ledger = audit.retirement_ledger
        expected_error = (
            "retirement history: policy or record is missing or not a regular blob"
        )

        def assert_rejected():
            # Catch inside the preservation helper so it also checks bytes
            # after the expected exception, not only after successful returns.
            with self.assertRaises(ledger.LedgerError) as failure:
                ledger.audit_history(self.root, self.policy_path, baseline)
            self.assertEqual(str(failure.exception), expected_error)

        self.assert_read_only_retirement_check(assert_rejected)

        def uncached(reader, tree, policy_path):
            return ledger.tree_ledger(reader.root, tree, policy_path)

        with patch.object(ledger.HistoryLedgerReader, "read", uncached):
            self.assert_read_only_retirement_check(assert_rejected)
        result, data = self.assert_read_only_retirement_check(lambda: self.cli(
            "--audit-retirement-history", self.policy_path,
            "--ledger-baseline", baseline,
        ))
        self.assertEqual(result.returncode, 1, data)
        self.assertEqual(data["error"], expected_error)

    def prepare_migrated_retirement(self):
        proof, old_path = self.new_retirement_after_migration()
        self.assertEqual(self.git("rev-parse", "--show-object-format"), "sha256")
        migration = proof[5].partition("=")[0]
        for commit in (self.sha, proof[1], migration, self.git("rev-parse", "HEAD")):
            self.assertRegex(commit, r"^[0-9a-f]{64}$")
        self.snapshot.unlink()
        result, data = self.cli("--snapshot-recovery", str(self.snapshot))
        self.assertEqual(result.returncode, 0, data)
        return proof, old_path

    def migrated_verify(self, proof):
        return self.cli(
            "--verify-recovery", str(self.snapshot),
            "--retirement-ledger", self.policy_path, *proof,
            "--approve-recovery-retirement", self.allowance(),
        )

    def test_migrated_retirement_preserves_full_sha256_ids(self):
        proof, old_path = self.prepare_migrated_retirement()
        old_record = json.loads((self.root / old_path).read_text())
        self.assertLess(old_record["decision_date"], "2026-10-03")
        self.assertEqual(old_record["protected_commit"], self.sha)
        result, preflight = self.proven_preflight(proof)
        self.assertEqual(result.returncode, 0, preflight)
        self.git("update-ref", "-d", self.ref, self.sha)
        result, verification = self.migrated_verify(proof)
        self.assertEqual(result.returncode, 0, verification)
        self.assertTrue(verification["recovery_guard"]["passed"])
        for ledger in (
            preflight["retirement_ledger"],
            verification["recovery_guard"]["retirement_ledger"],
        ):
            self.assertTrue(ledger["validated"])
            self.assertEqual(ledger["record_count"], 2)
            self.assertEqual(ledger["decisions"], [{"record_file": self.record_path, **self.record}])
            provenance = ledger["migration_provenance"]
            self.assertTrue(provenance["passed"])
            self.assertEqual(provenance["holds"], [])
            self.assertEqual(provenance["baseline"], proof[1])
            self.assertEqual(provenance["head"], self.git("rev-parse", "HEAD"))
            self.assertEqual(provenance["approved_migrations"], [{
                "commit": proof[5].partition("=")[0],
                "from_policy": proof[3], "to_policy": self.policy_path,
                "status": "owner-approved-policy-migration",
            }])
        self.assertEqual(json.loads((self.root / old_path).read_text()), old_record)

    def test_migration_ids_must_be_exact_sha256_commits(self):
        proof, _ = self.prepare_migrated_retirement()
        migration, _, locations = proof[5].partition("=")
        self.assertNotEqual(migration.upper(), migration)
        cases = (
            (migration[:12], "FULL_COMMIT"),
            (migration[:40], "outside the audit"),
            (migration[:63], "FULL_COMMIT"),
            (migration + "0", "FULL_COMMIT"),
            (migration.upper(), "FULL_COMMIT"),
            (migration[:-1] + "g", "FULL_COMMIT"),
            ("f" * 64, "outside the audit"),
            (proof[1], "outside the audit"),
            (self.git("rev-parse", "HEAD"), "retention holds"),
        )
        for mode in ("preflight", "verification"):
            if mode == "verification":
                self.git("update-ref", "-d", self.ref, self.sha)
            for commit, error in cases:
                with self.subTest(mode=mode, commit=commit):
                    incorrect = (*proof[:5], f"{commit}={locations}")
                    result, data = (
                        self.proven_preflight(incorrect) if mode == "preflight"
                        else self.migrated_verify(incorrect)
                    )
                    self.assertEqual(result.returncode, 1, data)
                    self.assertIn(error, data["error"])

    def assert_malformed_sha256_protected_commit_is_rejected(self, protected_commit):
        proof, _ = self.prepare_migrated_retirement()
        self.record["protected_commit"] = protected_commit
        self.write(self.record_path, self.record)
        self.commit()
        with self.assertRaisesRegex(
            audit.retirement_ledger.LedgerError,
            r"^retirement ledger: protected_commit requires a full commit ID$",
        ):
            audit.retirement_ledger.validate_record(self.root, self.record)
        expected_holds = [{
            "commit": self.git("rev-parse", "HEAD"),
            "reason": "invalid-ledger",
        }]
        for mode in ("preflight", "verification"):
            with self.subTest(mode=mode):
                if mode == "verification":
                    self.git("update-ref", "-d", self.ref, self.sha)
                result, data = self.assert_read_only_retirement_check(
                    lambda: self.proven_preflight(proof) if mode == "preflight"
                    else self.migrated_verify(proof),
                )
                self.assertEqual(result.returncode, 1, data)
                self.assertIn("retention holds", data["error"])
                self.assertEqual(data["retention_holds"], expected_holds)

    def test_migrated_retirement_rejects_63_character_protected_commit(self):
        self.assert_malformed_sha256_protected_commit_is_rejected(self.sha[:63])

    def test_migrated_retirement_rejects_65_character_protected_commit(self):
        self.assert_malformed_sha256_protected_commit_is_rejected(self.sha + "0")

    def test_migrated_retirement_rejects_uppercase_hex_protected_commit(self):
        self.assertNotEqual(self.sha.upper(), self.sha)
        self.assert_malformed_sha256_protected_commit_is_rejected(self.sha.upper())

    def test_migrated_retirement_rejects_non_hex_protected_commit(self):
        self.assert_malformed_sha256_protected_commit_is_rejected(self.sha[:-1] + "g")

    def test_migrated_retirement_rejects_sha1_length_protected_commit(self):
        proof, _ = self.prepare_migrated_retirement()
        snapshot_bytes = self.snapshot.read_bytes()
        self.assertEqual(json.loads(snapshot_bytes)["refs"][self.ref], self.sha)
        self.record["protected_commit"] = self.sha[:40]
        self.assertRegex(self.record["protected_commit"], r"^[0-9a-f]{40}$")
        # This is valid shared record syntax, not a malformed SHA-256 ID.
        audit.retirement_ledger.validate_record(self.root, self.record)
        self.write(self.record_path, self.record)
        self.git("add", self.record_path)
        # Introduce the incorrect ID with the new decision, so a historical
        # rewrite hold cannot mask the exact snapshot-binding rejection.
        self.git("commit", "--amend", "--no-edit")
        for mode in ("preflight", "verification"):
            with self.subTest(mode=mode):
                if mode == "verification":
                    self.git("update-ref", "-d", self.ref, self.sha)
                result, data = self.assert_read_only_retirement_check(
                    lambda: self.proven_preflight(proof) if mode == "preflight"
                    else self.migrated_verify(proof),
                )
                self.assertEqual(result.returncode, 1, data)
                self.assertEqual(
                    data["error"],
                    "retirement ledger: protected commit differs from the pre-removal snapshot",
                )
                self.assertEqual(self.snapshot.read_bytes(), snapshot_bytes)

    def test_mismatched_sha256_protected_commit_is_rejected(self):
        proof, _ = self.prepare_migrated_retirement()
        other = self.git("rev-parse", "HEAD")
        self.assertNotEqual(other, self.sha)
        self.git("update-ref", self.ref, other, self.sha)
        result, data = self.proven_preflight(proof)
        self.assertEqual(result.returncode, 1, data)
        self.assertIn("protected commit differs", data["error"])
        self.snapshot.unlink()
        result, data = self.cli("--snapshot-recovery", str(self.snapshot))
        self.assertEqual(result.returncode, 0, data)
        self.git("update-ref", "-d", self.ref, other)
        result, data = self.migrated_verify(proof)
        self.assertEqual(result.returncode, 1, data)
        self.assertIn("protected commit differs", data["error"])


if __name__ == "__main__":
    unittest.main()
