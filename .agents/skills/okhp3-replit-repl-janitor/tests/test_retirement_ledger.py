from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
