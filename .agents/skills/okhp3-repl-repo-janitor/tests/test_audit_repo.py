from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).parents[1] / "scripts" / "audit-repo.py"
SPEC = importlib.util.spec_from_file_location("audit_repo", SCRIPT)
assert SPEC and SPEC.loader
audit_repo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit_repo)


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


class DecisionLedgerTests(unittest.TestCase):
    def make_repo(self) -> tuple[Path, str]:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        git(root, "init", "-q")
        git(root, "branch", "-M", "main")
        git(root, "config", "user.email", "test@example.com")
        git(root, "config", "user.name", "Test User")
        (root / "README.md").write_text("initial\n", encoding="utf-8")
        git(root, "add", "README.md")
        git(root, "commit", "-qm", "initial")
        return root, git(root, "rev-parse", "HEAD")

    def write_ledger(
        self,
        root: Path,
        rows: str,
        exclusions: str = "",
        reconciliations: str = "",
    ) -> Path:
        path = root / "ledger.md"
        path.write_text(
            "\n".join([
                "## Branch decisions",
                "| Branch | Decision | Tip SHA | Evidence |",
                "|---|---|---|---|",
                rows,
                "",
                "## Archive reconciliation evidence",
                "| Branch | Archive tip SHA | Reviewed active tip SHA | Disposition | Active-line evidence | Rationale |",
                "|---|---|---|---|---|---|",
                reconciliations,
                "",
                "## Explicit exclusions and holds",
                exclusions,
            ]),
            encoding="utf-8",
        )
        return path

    def branch_facts(self, root: Path) -> list[dict[str, object]]:
        return audit_repo.audit_branches(root, "main")

    def test_selects_newest_dated_ledger_without_hardcoded_date(self) -> None:
        root, _ = self.make_repo()
        agents = root / ".agents"
        agents.mkdir()
        older = agents / "branch-decision-ledger-2026-01-15.md"
        newer = agents / "branch-decision-ledger-2026-09-10.md"
        older.write_text("older\n", encoding="utf-8")
        newer.write_text("newer\n", encoding="utf-8")

        self.assertEqual(audit_repo.select_decision_ledger(root), newer)

    def test_stable_active_ledger_wins_over_dated_ledgers(self) -> None:
        root, _ = self.make_repo()
        agents = root / ".agents"
        agents.mkdir()
        stable = agents / "branch-decision-ledger.md"
        dated = agents / "branch-decision-ledger-2099-12-31.md"
        stable.write_text("stable\n", encoding="utf-8")
        dated.write_text("dated\n", encoding="utf-8")

        self.assertEqual(audit_repo.select_decision_ledger(root), stable)

    def test_explicit_ledger_override_supports_historical_audit(self) -> None:
        root, _ = self.make_repo()
        agents = root / ".agents"
        agents.mkdir()
        historical = agents / "branch-decision-ledger-2024-05-01.md"
        historical.write_text("historical\n", encoding="utf-8")

        self.assertEqual(
            audit_repo.select_decision_ledger(
                root, ".agents/branch-decision-ledger-2024-05-01.md"
            ),
            historical,
        )

    def test_reports_missing_drift_and_stale_rows(self) -> None:
        root, initial = self.make_repo()
        git(root, "branch", "reviewed")
        git(root, "branch", "unreviewed")
        ledger = self.write_ledger(
            root,
            f"| `reviewed` | **keep** | `{initial}` | active |",
            "- `obsolete` — no longer present",
        )
        branches = self.branch_facts(root)
        current = "main"
        result = audit_repo.audit_decision_ledger(root, branches, current, ledger)

        self.assertEqual(result["missing_branches"], ["unreviewed"])
        self.assertEqual(result["tip_sha_drift"], [])
        self.assertEqual(
            result["stale_ledger_rows"],
            [
                {"branch": "obsolete", "kind": "exclusion"},
            ],
        )
        self.assertFalse(result["ok"])

    def test_reports_tip_sha_drift_and_accepts_exclusions(self) -> None:
        root, initial = self.make_repo()
        git(root, "branch", "reviewed")
        git(root, "branch", "held")
        git(root, "checkout", "-q", "main")
        (root / "change.txt").write_text("changed\n", encoding="utf-8")
        git(root, "add", "change.txt")
        git(root, "commit", "-qm", "change")
        git(root, "branch", "-f", "reviewed", "main")
        ledger = self.write_ledger(
            root,
            f"| `reviewed` | **keep** | `{initial}` | active |",
            "- `held` — active work",
        )
        branches = self.branch_facts(root)
        current = "main"
        result = audit_repo.audit_decision_ledger(root, branches, current, ledger)

        self.assertEqual(result["missing_branches"], [])
        self.assertEqual(result["tip_sha_drift"][0]["branch"], "reviewed")
        self.assertEqual(result["stale_ledger_rows"], [])
        self.assertFalse(result["ok"])

    def test_reports_malformed_decision_rows(self) -> None:
        root, initial = self.make_repo()
        git(root, "branch", "reviewed")
        ledger = self.write_ledger(
            root,
            "\n".join([
                f"| `reviewed` | **keep** | `{initial}` | valid |",
                "| reviewed | **keep** | missing backticks | malformed |",
                "| `truncated` | **keep** |",
            ]),
        )

        result = audit_repo.audit_decision_ledger(
            root, self.branch_facts(root), "main", ledger
        )

        self.assertEqual(
            [item["reason"] for item in result["malformed_decision_rows"]],
            [
                "branch cell must contain one backticked branch name",
                "decision row has fewer than three cells",
            ],
        )
        self.assertFalse(result["ok"])

    def test_accepts_supported_keep_and_archive_decisions(self) -> None:
        root, initial = self.make_repo()
        git(root, "branch", "kept")
        git(root, "branch", "archived")
        ledger = self.write_ledger(
            root,
            "\n".join([
                f"| `kept` | **keep** | `{initial}` | active |",
                f"| `archived` | **archive** | `{initial}` | retained |",
            ]),
        )

        result = audit_repo.audit_decision_ledger(
            root, self.branch_facts(root), "main", ledger
        )

        self.assertEqual(result["unsupported_decision_labels"], [])
        self.assertTrue(result["ok"])

    def test_reports_unsupported_decision_with_line_and_branch_context(self) -> None:
        root, initial = self.make_repo()
        git(root, "branch", "ambiguous")
        ledger = self.write_ledger(
            root,
            f"| `ambiguous` | **retain** | `{initial}` | unclear label |",
        )

        result = audit_repo.audit_decision_ledger(
            root, self.branch_facts(root), "main", ledger
        )

        self.assertEqual(
            result["unsupported_decision_labels"],
            [{
                "line": 4,
                "branch": "ambiguous",
                "decision": "retain",
                "supported_decisions": ["archive", "keep"],
            }],
        )
        self.assertEqual(result["missing_branches"], [])
        self.assertFalse(result["ok"])

    def test_reports_malformed_and_duplicate_exclusions(self) -> None:
        root, initial = self.make_repo()
        git(root, "branch", "held")
        ledger = self.write_ledger(
            root,
            "",
            "\n".join([
                "- `held` — active work",
                "- `held` — repeated hold",
                "- held — missing backticks",
            ]),
        )

        result = audit_repo.audit_decision_ledger(
            root, self.branch_facts(root), "main", ledger
        )

        self.assertEqual(result["exclusion_branch_count"], 1)
        self.assertEqual(
            result["duplicate_exclusion_entries"][0]["branch"], "held"
        )
        self.assertEqual(
            result["malformed_exclusion_entries"][0]["reason"],
            (
                "exclusion entry must list backticked branch names "
                "followed by an em-dash explanation"
            ),
        )
        self.assertFalse(result["ok"])

    def test_cli_reports_malformed_content_and_exits_nonzero(self) -> None:
        root, initial = self.make_repo()
        ledger = self.write_ledger(
            root,
            "| not-a-branch-row | **keep** | malformed |",
            "- `main` — current branch",
        )

        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--root",
                str(root),
                "--base",
                "main",
                "--decision-ledger",
                str(ledger),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 1)
        report = json.loads(result.stdout)
        self.assertEqual(
            report["decision_ledger"]["malformed_decision_rows"][0]["line"],
            4,
        )
        self.assertFalse(report["decision_ledger"]["ok"])

    def test_ledger_check_reports_invalid_draft_without_running_git(self) -> None:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root))
        ledger = self.write_ledger(
            root,
            "| reviewed | **retain** | malformed |",
            "\n".join([
                "- `held` — active work",
                "- `held` — repeated hold",
                "- held — missing backticks",
            ]),
        )

        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--check-ledger",
                "--root",
                str(root),
                "--decision-ledger",
                str(ledger),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 1)
        report = json.loads(result.stdout)
        self.assertEqual(report["malformed_decision_rows"][0]["line"], 4)
        self.assertEqual(report["duplicate_exclusion_entries"][0]["line"], 13)
        self.assertEqual(report["malformed_exclusion_entries"][0]["line"], 14)
        self.assertFalse(report["ok"])

    def test_ledger_check_accepts_clean_ledger_without_git_repository(self) -> None:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root))
        ledger = self.write_ledger(
            root,
            f"| `reviewed` | **keep** | `{'a' * 40}` | active |",
            "- `held` — active work",
        )

        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--check-ledger",
                "--root",
                str(root),
                "--decision-ledger",
                str(ledger),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0)
        report = json.loads(result.stdout)
        self.assertEqual(report["decision_row_count"], 1)
        self.assertEqual(report["exclusion_branch_count"], 1)
        self.assertTrue(report["ok"])

    def test_archive_equivalence_distinguishes_promoted_and_unrepresented_work(self) -> None:
        root, initial = self.make_repo()
        git(root, "branch", "promoted-archive")
        git(root, "checkout", "-q", "promoted-archive")
        (root / "promoted.txt").write_text("promoted\n", encoding="utf-8")
        git(root, "add", "promoted.txt")
        git(root, "commit", "-qm", "archive promoted change")
        promoted_tip = git(root, "rev-parse", "HEAD")

        git(root, "checkout", "-q", "main")
        git(root, "cherry-pick", "-n", promoted_tip)
        git(root, "commit", "-qm", "promoted change on active line")

        git(root, "branch", "unrepresented-archive")
        git(root, "checkout", "-q", "unrepresented-archive")
        (root / "unrepresented.txt").write_text("not promoted\n", encoding="utf-8")
        git(root, "add", "unrepresented.txt")
        git(root, "commit", "-qm", "active-only change")
        unrepresented_tip = git(root, "rev-parse", "HEAD")

        git(root, "checkout", "-q", "main")
        (root / "active-only.txt").write_text("active\n", encoding="utf-8")
        git(root, "add", "active-only.txt")
        git(root, "commit", "-qm", "active line change")
        active_tip = git(root, "rev-parse", "HEAD")

        ledger = self.write_ledger(
            root,
            "\n".join([
                f"| `promoted-archive` | **archive** | `{promoted_tip}` | promoted |",
                f"| `unrepresented-archive` | **archive** | `{unrepresented_tip}` | stale |",
            ]),
        )
        result = audit_repo.audit_archive_equivalents(root, ledger, "main")

        self.assertEqual(result["active_line_tip_sha"], active_tip)
        self.assertEqual(result["already_promoted"], ["promoted-archive"])
        self.assertEqual(result["unrepresented_changes"], ["unrepresented-archive"])
        self.assertEqual(result["unverifiable"], [])
        self.assertFalse(result["ok"])

        promoted = result["archives"][0]
        self.assertEqual(promoted["classification"], "already-promoted")
        self.assertFalse(promoted["tree_difference"]["same"])
        self.assertEqual(promoted["file_differences"][0]["status"], "D")
        self.assertEqual(
            promoted["commit_differences"]["unrepresented_commit_count"], 0
        )

        stale = result["archives"][1]
        self.assertEqual(stale["classification"], "unrepresented-changes")
        self.assertGreater(
            stale["commit_differences"]["unrepresented_commit_count"], 0
        )

    def test_archive_equivalence_reports_unverifiable_tip_without_mutation(self) -> None:
        root, initial = self.make_repo()
        ledger = self.write_ledger(
            root,
            f"| `missing-archive` | **archive** | `{'0' * 40}` | missing |",
        )
        before = git(root, "for-each-ref", "--format=%(refname) %(objectname)")

        result = audit_repo.audit_archive_equivalents(root, ledger, "main")

        self.assertEqual(result["unverifiable"], ["missing-archive"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["archives"][0]["classification"], "unverifiable")
        self.assertEqual(
            before,
            git(root, "for-each-ref", "--format=%(refname) %(objectname)"),
        )

    def test_archive_equivalence_preserves_both_paths_for_a_rename(self) -> None:
        root, _ = self.make_repo()
        git(root, "branch", "renamed-archive")
        git(root, "checkout", "-q", "renamed-archive")
        (root / "old-name.txt").write_text("same content\n", encoding="utf-8")
        git(root, "add", "old-name.txt")
        git(root, "commit", "-qm", "archive path")
        archive_tip = git(root, "rev-parse", "HEAD")

        git(root, "checkout", "-q", "main")
        (root / "new-name.txt").write_text("same content\n", encoding="utf-8")
        git(root, "add", "new-name.txt")
        git(root, "commit", "-qm", "active path")
        active_tip = git(root, "rev-parse", "HEAD")

        ledger = self.write_ledger(
            root,
            f"| `renamed-archive` | **archive** | `{archive_tip}` | moved |",
        )
        before = git(root, "for-each-ref", "--format=%(refname) %(objectname)")

        result = audit_repo.audit_archive_equivalents(root, ledger, "main")

        archive = result["archives"][0]
        self.assertEqual(result["active_line_tip_sha"], active_tip)
        self.assertEqual(
            archive["file_difference_direction"],
            "active-line-to-archive-tip",
        )
        self.assertEqual(
            archive["file_differences"],
            [
                {"status": "D", "path": "new-name.txt"},
                {"status": "A", "path": "old-name.txt"},
            ],
        )
        self.assertEqual(
            before,
            git(root, "for-each-ref", "--format=%(refname) %(objectname)"),
        )

    def test_archive_equivalence_accepts_exact_tip_supersession_evidence(self) -> None:
        root, _ = self.make_repo()
        git(root, "branch", "superseded-archive")
        git(root, "checkout", "-q", "superseded-archive")
        (root / "copy.txt").write_text("older wording\n", encoding="utf-8")
        git(root, "add", "copy.txt")
        git(root, "commit", "-qm", "older archive wording")
        archive_tip = git(root, "rev-parse", "HEAD")

        git(root, "checkout", "-q", "main")
        (root / "copy.txt").write_text("newer reconciled wording\n", encoding="utf-8")
        git(root, "add", "copy.txt")
        git(root, "commit", "-qm", "replace archive wording")
        active_tip = git(root, "rev-parse", "HEAD")

        ledger = self.write_ledger(
            root,
            f"| `superseded-archive` | **archive** | `{archive_tip}` | reviewed |",
            reconciliations=(
                f"| `superseded-archive` | `{archive_tip}` | `{active_tip}` | "
                "**superseded** | "
                "`main` replacement commit | Later wording intentionally replaces it. |"
            ),
        )
        before = git(root, "for-each-ref", "--format=%(refname) %(objectname)")

        result = audit_repo.audit_archive_equivalents(root, ledger, "main")

        self.assertTrue(result["ok"])
        self.assertEqual(result["confirmed_supersession"], ["superseded-archive"])
        self.assertEqual(result["unrepresented_changes"], [])
        archive = result["archives"][0]
        self.assertEqual(archive["classification"], "confirmed-supersession")
        self.assertEqual(
            archive["reconciliation_evidence"]["disposition"], "superseded"
        )
        self.assertGreater(
            archive["commit_differences"]["unrepresented_commit_count"], 0
        )
        self.assertEqual(
            before,
            git(root, "for-each-ref", "--format=%(refname) %(objectname)"),
        )

    def test_archive_equivalence_rejects_evidence_for_a_different_tip(self) -> None:
        root, initial = self.make_repo()
        git(root, "branch", "unrepresented-archive")
        git(root, "checkout", "-q", "unrepresented-archive")
        (root / "change.txt").write_text("archive change\n", encoding="utf-8")
        git(root, "add", "change.txt")
        git(root, "commit", "-qm", "archive change")
        archive_tip = git(root, "rev-parse", "HEAD")
        git(root, "checkout", "-q", "main")

        ledger = self.write_ledger(
            root,
            f"| `unrepresented-archive` | **archive** | `{archive_tip}` | reviewed |",
            reconciliations=(
                f"| `unrepresented-archive` | `{initial}` | `{initial}` | "
                "**superseded** | "
                "`main` evidence | This evidence belongs to the prior tip. |"
            ),
        )

        result = audit_repo.audit_archive_equivalents(root, ledger, "main")

        self.assertFalse(result["ok"])
        self.assertEqual(result["confirmed_supersession"], [])
        self.assertEqual(result["unrepresented_changes"], ["unrepresented-archive"])
        self.assertEqual(
            result["archives"][0]["classification"], "unrepresented-changes"
        )

    def test_archive_equivalence_rejects_evidence_from_another_active_line(self) -> None:
        root, initial = self.make_repo()
        git(root, "branch", "superseded-archive")
        git(root, "checkout", "-q", "superseded-archive")
        (root / "archive.txt").write_text("archive work\n", encoding="utf-8")
        git(root, "add", "archive.txt")
        git(root, "commit", "-qm", "archive work")
        archive_tip = git(root, "rev-parse", "HEAD")

        git(root, "checkout", "-q", "main")
        git(root, "branch", "reviewed-active")
        git(root, "checkout", "-q", "reviewed-active")
        (root / "replacement.txt").write_text("replacement\n", encoding="utf-8")
        git(root, "add", "replacement.txt")
        git(root, "commit", "-qm", "reviewed replacement")
        reviewed_tip = git(root, "rev-parse", "HEAD")
        git(root, "checkout", "-q", "main")

        ledger = self.write_ledger(
            root,
            f"| `superseded-archive` | **archive** | `{archive_tip}` | reviewed |",
            reconciliations=(
                f"| `superseded-archive` | `{archive_tip}` | `{reviewed_tip}` | "
                "**superseded** | Reviewed replacement | Replaced on another line. |"
            ),
        )

        result = audit_repo.audit_archive_equivalents(root, ledger, "main")

        self.assertFalse(result["ok"])
        self.assertEqual(result["confirmed_supersession"], [])
        self.assertEqual(result["unrepresented_changes"], ["superseded-archive"])

    def test_archive_equivalence_treats_git_comparison_failure_as_unverifiable(
        self,
    ) -> None:
        root, _ = self.make_repo()
        git(root, "branch", "archive")
        original_git_result = audit_repo._git_result

        def fail_cherry(repo: Path, *args: str):
            if args[:2] == ("cherry", "-v"):
                return subprocess.CompletedProcess(
                    ["git", *args], 128, stdout="", stderr="comparison failed"
                )
            return original_git_result(repo, *args)

        ledger = self.write_ledger(
            root,
            f"| `archive` | **archive** | `{git(root, 'rev-parse', 'archive')}` | reviewed |",
        )

        with patch.object(audit_repo, "_git_result", side_effect=fail_cherry):
            result = audit_repo.audit_archive_equivalents(root, ledger, "main")

        self.assertFalse(result["ok"])
        self.assertEqual(result["unverifiable"], ["archive"])
        self.assertEqual(result["archives"][0]["classification"], "unverifiable")
        self.assertIn("git cherry", result["archives"][0]["error"])

    def test_archive_equivalence_treats_ancestry_failure_as_unverifiable(
        self,
    ) -> None:
        root, initial = self.make_repo()
        git(root, "branch", "archive")
        git(root, "checkout", "-q", "archive")
        (root / "archive.txt").write_text("archive work\n", encoding="utf-8")
        git(root, "add", "archive.txt")
        git(root, "commit", "-qm", "archive work")
        archive_tip = git(root, "rev-parse", "HEAD")
        git(root, "checkout", "-q", "main")
        active_tip = git(root, "rev-parse", "HEAD")
        original_git_result = audit_repo._git_result

        def fail_ancestry(repo: Path, *args: str):
            if args[:2] == ("merge-base", "--is-ancestor"):
                return subprocess.CompletedProcess(
                    ["git", *args], 128, stdout="", stderr="object unavailable"
                )
            return original_git_result(repo, *args)

        ledger = self.write_ledger(
            root,
            f"| `archive` | **archive** | `{archive_tip}` | reviewed |",
            reconciliations=(
                f"| `archive` | `{archive_tip}` | `{active_tip}` | "
                "**superseded** | Reviewed replacement | Replaced on main. |"
            ),
        )

        with patch.object(audit_repo, "_git_result", side_effect=fail_ancestry):
            result = audit_repo.audit_archive_equivalents(root, ledger, "main")

        self.assertFalse(result["ok"])
        self.assertEqual(result["unverifiable"], ["archive"])
        self.assertEqual(result["archives"][0]["classification"], "unverifiable")
        self.assertIn("ancestry check", result["archives"][0]["error"])


class RemoteRefreshTests(unittest.TestCase):
    def make_repo(self) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        git(root, "init", "-q")
        git(root, "branch", "-M", "main")
        git(root, "config", "user.email", "test@example.com")
        git(root, "config", "user.name", "Test User")
        (root / "README.md").write_text("initial\n", encoding="utf-8")
        git(root, "add", "README.md")
        git(root, "commit", "-qm", "initial")
        git(root, "remote", "add", "origin", "ssh://127.0.0.1:1/example/repo.git")
        return root

    def test_refresh_is_non_interactive_and_classifies_unavailable_remote(self) -> None:
        root = self.make_repo()
        failed_fetch = subprocess.CompletedProcess(
            ["git", "fetch", "--all"],
            128,
            stdout="",
            stderr="Host key verification failed.",
        )

        with patch.object(audit_repo.subprocess, "run", return_value=failed_fetch) as run:
            result = audit_repo.refresh_remote(root)

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["classification"], "remote-unavailable")
        self.assertTrue(result["non_interactive"])
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["env"]["GIT_TERMINAL_PROMPT"], "0")
        self.assertIn("BatchMode=yes", kwargs["env"]["GIT_SSH_COMMAND"])

    def test_refresh_overrides_an_inherited_interactive_ssh_setting(self) -> None:
        root = self.make_repo()
        failed_fetch = subprocess.CompletedProcess(
            ["git", "fetch", "--all"],
            128,
            stdout="",
            stderr="Host key verification failed.",
        )

        with patch.dict(
            audit_repo.os.environ,
            {"GIT_SSH_COMMAND": "ssh -o BatchMode=no"},
            clear=False,
        ), patch.object(
            audit_repo.subprocess, "run", return_value=failed_fetch
        ) as run:
            audit_repo.refresh_remote(root)

        self.assertEqual(
            run.call_args.kwargs["env"]["GIT_SSH_COMMAND"],
            "ssh -o BatchMode=yes",
        )

    def test_cli_prints_local_evidence_when_refresh_is_unavailable(self) -> None:
        root = self.make_repo()
        ledger = root / "ledger.md"
        ledger.write_text(
            "\n".join([
                "## Branch decisions",
                "| Branch | Decision | Tip SHA | Evidence |",
                "|---|---|---|---|",
                "",
                "## Explicit exclusions and holds",
                "- `main` — current branch",
            ]),
            encoding="utf-8",
        )

        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--root",
                str(root),
                "--base",
                "origin/main",
                "--decision-ledger",
                str(ledger),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 1)
        report = json.loads(result.stdout)
        self.assertEqual(
            report["remote_refresh"]["classification"],
            "remote-unavailable",
        )
        self.assertEqual([branch["branch"] for branch in report["branches"]], ["main"])
        self.assertIn("naming_violations", report)
        self.assertIn("detritus_folders", report)


class RepositoryLedgerIntegrationTests(unittest.TestCase):
    def test_real_ledger_confirms_reviewed_supersession_on_main(self) -> None:
        root = SCRIPT.parents[4]
        ledger = root / ".agents" / "branch-decision-ledger-2026-09-07.md"
        required_refs = [
            "main",
            "replit-agent",
            "subrepl-ili4a5c9",
            "subrepl-j940c6i6",
        ]
        if not ledger.is_file() or any(
            subprocess.run(
                ["git", "rev-parse", "--verify", f"{ref}^{{commit}}"],
                cwd=root,
                capture_output=True,
                check=False,
            ).returncode
            for ref in required_refs
        ):
            self.skipTest("repository-specific ledger refs are unavailable")

        result = audit_repo.audit_archive_equivalents(root, ledger, "main")

        self.assertTrue(result["ok"])
        self.assertEqual(
            result["confirmed_supersession"],
            ["replit-agent", "subrepl-ili4a5c9", "subrepl-j940c6i6"],
        )
        self.assertEqual(result["unrepresented_changes"], [])
        self.assertEqual(result["unverifiable"], [])


if __name__ == "__main__":
    unittest.main()
