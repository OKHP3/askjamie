from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import time
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).parents[1] / "scripts" / "audit-repo.py"
SPEC = importlib.util.spec_from_file_location("retirement_audit", SCRIPT)
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


class RetirementLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
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

    def test_complete_retirement_retains_exact_committed_decision(self):
        result, data = self.preflight()
        self.assertEqual(result.returncode, 0, data)
        self.git("update-ref", "-d", self.ref, self.sha)
        result, data = self.verify()
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
        result, data = self.preflight()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("committed unchanged", data["error"])

    def test_flag_without_ledger_cannot_complete_retirement(self):
        self.git("update-ref", "-d", self.ref, self.sha)
        result, data = self.cli(
            "--verify-recovery", str(self.snapshot),
            "--approve-recovery-retirement", self.allowance(),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--retirement-ledger", data["error"])

    def test_missing_ledger_blocks_verification(self):
        (self.root / self.policy_path).unlink()
        result, data = self.verify()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("error", data)

    def test_missing_record_blocks_completion(self):
        (self.root / self.record_path).unlink()
        self.commit()
        result, data = self.preflight()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no retained decision", data["error"])

    def test_modified_or_uncommitted_record_is_not_retained_evidence(self):
        self.record["approver"] = "different-owner"
        self.write(self.record_path, self.record)
        result, data = self.preflight()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("committed unchanged", data["error"])
        self.git("rm", "--cached", self.record_path)
        self.git("commit", "-m", "Untrack record")
        result, data = self.preflight()
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
                result, data = self.preflight()
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
                result, data = self.preflight()
                self.assertNotEqual(result.returncode, 0)

    def test_duplicate_records_and_duplicate_json_keys_fail(self):
        self.write(self.records_path + "/duplicate.json", self.record)
        self.commit()
        result, data = self.preflight()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("duplicate", data["error"])
        (self.root / (self.records_path + "/duplicate.json")).unlink()
        path = self.root / self.record_path
        path.write_text('{"format": 1, "format": 1}', encoding="utf-8")
        self.commit()
        result, data = self.preflight()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("duplicate", data["error"])

    def test_malformed_utf8_and_json_fail_visibly(self):
        for content in (b"{invalid}", b"\xff"):
            (self.root / self.record_path).write_bytes(content)
            self.commit()
            result, data = self.preflight()
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
        result, data = self.preflight()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("symlink", data["error"])

    def test_mismatched_evidence_blocks_completion(self):
        self.git("update-ref", "-d", self.ref, self.sha)
        result, data = self.verify(self.ref + "=different evidence")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("evidence differs", data["error"])

    def test_guard_still_blocks_other_removed_refs_and_still_present_ref(self):
        result, data = self.verify()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(data["recovery_guard"]["passed"])
        self.git("update-ref", "refs/remotes/origin/retained", self.sha)
        self.snapshot.unlink()
        self.cli("--snapshot-recovery", str(self.snapshot))
        self.git("update-ref", "-d", self.ref, self.sha)
        self.git("update-ref", "-d", "refs/remotes/origin/retained")
        result, data = self.verify()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refs/remotes/origin/retained", data["recovery_guard"]["unexpected_removed_refs"])

    def test_duplicate_allowances_fail_and_snapshot_is_not_overwritten(self):
        self.git("update-ref", "-d", self.ref, self.sha)
        result, data = self.cli(
            "--verify-recovery", str(self.snapshot), "--retirement-ledger", self.policy_path,
            "--approve-recovery-retirement", self.allowance(),
            "--approve-recovery-retirement", self.allowance(),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("duplicate", data["error"])
        old = self.snapshot.read_bytes()
        result, data = self.cli("--snapshot-recovery", str(self.snapshot))
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
        result, data = self.verify()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(unique, data["recovery_guard"]["unreachable_objects"])

    def test_recovery_verification_is_read_only(self):
        before = self.git("show-ref")
        self.preflight()
        self.verify()
        self.assertEqual(before, self.git("show-ref"))

    def history(self, baseline, *args):
        return self.cli(
            "--audit-retirement-history", self.policy_path,
            "--ledger-baseline", baseline, *args,
        )

    def assert_cached_history_equivalent(self, baseline, migrations=()):
        ledger = audit.retirement_ledger

        def snapshot():
            # Include checkout, index, refs, objects and reflogs, not only status.
            return {
                path.relative_to(self.root).as_posix(): path.read_bytes()
                for path in self.root.rglob("*")
                if path.is_file() and not path.is_symlink()
            }

        before = snapshot()
        cached = ledger.audit_history(self.root, self.policy_path, baseline, migrations)
        self.assertEqual(snapshot(), before)

        def uncached(reader, tree, policy_path):
            return ledger.tree_ledger(reader.root, tree, policy_path)

        with patch.object(ledger.HistoryLedgerReader, "read", uncached):
            reference = ledger.audit_history(self.root, self.policy_path, baseline, migrations)
        self.assertEqual(snapshot(), before)
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
        result, data = self.proven_preflight(proof)
        self.assertNotEqual(result.returncode, 0, data)
        self.assertIn("retention holds", data["error"])

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
                result, data = self.proven_preflight(proof)
                self.assertNotEqual(result.returncode, 0, data)
                self.assertIn("retention holds", data["error"])
                self.write(old_path, original)
                self.commit()
                result, data = self.proven_preflight(proof)
                self.assertNotEqual(result.returncode, 0, data)
                self.git("reset", "--hard", anchor)

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


if __name__ == "__main__":
    unittest.main()
