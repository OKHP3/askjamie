from __future__ import annotations

import base64
import contextlib
import importlib.util
import io
import json
import os
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

    def test_each_safety_diagnostic_blocks_both_ledger_checks(self) -> None:
        # Derive diagnostics from the parser's result so a new field must be
        # reported and block both checks, without updating a second allowlist.
        evidence_fields = {"decisions", "exclusions", "archive_reconciliations"}
        empty = audit_repo.DecisionLedger(
            **{field: [] for field in audit_repo.DecisionLedger._fields}
        )
        path = Path("ledger.md")
        diagnostics = set(empty._fields) - evidence_fields
        for field in [None, *sorted(diagnostics)]:
            with self.subTest(diagnostic=field):
                finding = {"branch": "reviewed", "line": 7, "reason": "unsafe"}
                ledger = empty if field is None else empty._replace(**{field: [finding]})
                with patch.object(audit_repo, "parse_decision_ledger", return_value=ledger), \
                     patch.object(audit_repo.subprocess, "run", side_effect=AssertionError(
                         "ledger checks must not invoke Git or the network"
                     )):
                    focused = audit_repo.validate_decision_ledger_structure(path)
                    full = audit_repo.audit_decision_ledger(path.parent, [], "main", path)
                for report in (focused, full):
                    self.assertEqual(report["ok"], field is None)
                    for diagnostic in diagnostics:
                        self.assertEqual(
                            report[diagnostic], [finding] if diagnostic == field else []
                        )
                self.assertEqual(
                    {key: value for key, value in full.items() if key in focused},
                    focused,
                )

    def test_git_consistency_findings_still_block_only_full_ledger_check(self) -> None:
        tip = "a" * 40
        row = {"branch": "reviewed", "decision": "keep", "tip_sha": tip}
        empty = audit_repo.DecisionLedger(
            **{field: [] for field in audit_repo.DecisionLedger._fields}
        )
        path = Path("ledger.md")
        cases = [
            ("missing_branches", empty, [{"branch": "reviewed", "tip_sha": tip}]),
            ("tip_sha_drift", empty._replace(decisions=[row]),
             [{"branch": "reviewed", "tip_sha": "b" * 40}]),
            ("invalid_tip_sha", empty._replace(decisions=[{**row, "tip_sha": "bad"}]),
             [{"branch": "reviewed", "tip_sha": tip}]),
            ("stale_ledger_rows", empty._replace(decisions=[row]), []),
        ]
        for field, ledger, branches in cases:
            with self.subTest(finding=field), patch.object(
                audit_repo, "parse_decision_ledger", return_value=ledger
            ):
                focused = audit_repo.validate_decision_ledger_structure(path)
                full = audit_repo.audit_decision_ledger(path.parent, branches, "main", path)
                self.assertTrue(focused["ok"])
                self.assertFalse(full["ok"])
                self.assertTrue(full[field])
                for other, _, _ in cases:
                    if other != field:
                        self.assertEqual(full[other], [])

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

    def test_hold_conflicts_preserve_sources_without_git_in_either_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = f"  | `held` | **ARCHIVE** | `{'a' * 40}` | approved |"
            keep = f"| `kept` | keep | {'b' * 40} | active |"
            hold = "  - `held`, `kept`, and `other` — owner hold"
            sections = [
                f"## Branch decisions\n{archive}\n{keep}",
                f"## Explicit exclusions and holds\n{hold}",
            ]
            for reverse in (False, True):
                with self.subTest(reverse=reverse):
                    lines = "\n\n".join(
                        reversed(sections) if reverse else sections
                    ).splitlines()
                    ledger = root / "ledger.md"
                    ledger.write_text("\n".join(lines), encoding="utf-8")
                    with patch.object(
                        audit_repo.subprocess, "run", side_effect=AssertionError("Git called")
                    ):
                        parsed = audit_repo.parse_decision_ledger(ledger)
                        report = audit_repo.validate_decision_ledger_structure(ledger)
                    expected = [{
                        "branch": "held",
                        "decision_line": lines.index(archive) + 1,
                        "decision_content": archive,
                        "hold_line": lines.index(hold) + 1,
                        "hold_content": hold,
                        "reason": (
                            "archive decision conflicts with an explicit hold; "
                            "change the decision to keep or explicitly resolve the hold "
                            "before planning archive cleanup"
                        ),
                    }]
                    self.assertEqual(parsed.decision_hold_conflicts, expected)
                    self.assertEqual(report["decision_hold_conflicts"], expected)
                    self.assertFalse(report["ok"])
                    # The focused CLI must also work outside a Git repository.
                    result = subprocess.run(
                        [sys.executable, str(SCRIPT), "--check-ledger",
                         "--root", str(root), "--decision-ledger", str(ledger)],
                        capture_output=True, text=True, check=False,
                    )
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertEqual(
                        json.loads(result.stdout)["decision_hold_conflicts"], expected
                    )

    def test_holds_block_both_cli_checks_and_archive_approval(self) -> None:
        root, initial = self.make_repo()
        git(root, "branch", "promoted")
        git(root, "checkout", "-qb", "superseded")
        (root / "unique.txt").write_text("archive work\n", encoding="utf-8")
        git(root, "add", "unique.txt")
        git(root, "commit", "-qm", "archive work")
        unique_tip = git(root, "rev-parse", "HEAD")
        git(root, "checkout", "-q", "main")
        git(root, "branch", "kept")
        rows = "\n".join([
            f"| `promoted` | archive | {initial} | approved |",
            f"| `superseded` | archive | {unique_tip} | approved |",
            f"| `kept` | keep | {initial} | active |",
        ])
        hold = "- `promoted`, `superseded`, and `kept` — do not clean up"
        review = (
            f"| `superseded` | {unique_tip} | {initial} | superseded "
            "| Reviewed replacement | Intentionally replaced. |"
        )
        branches = audit_repo.audit_branches(root, "main", refresh=False)
        for held in (True, False):
            with self.subTest(held=held):
                ledger = self.write_ledger(
                    root, rows, hold if held else "- `kept` — still active",
                    reconciliations=review,
                )
                expected = audit_repo.parse_decision_ledger(ledger).decision_hold_conflicts
                self.assertEqual([r["branch"] for r in expected],
                                 ["promoted", "superseded"] if held else [])
                if held:
                    for index, conflict in enumerate(expected):
                        self.assertEqual(conflict["decision_line"], 4 + index)
                        self.assertEqual(conflict["decision_content"], rows.splitlines()[index])
                        self.assertEqual(conflict["hold_line"], 14)
                        self.assertEqual(conflict["hold_content"], hold)
                coverage = audit_repo.audit_decision_ledger(root, branches, "main", ledger)
                self.assertEqual(coverage["missing_branches"], [])
                self.assertEqual(coverage["covered_branch_count"], 3)
                self.assertEqual(coverage["ok"], not held)
                before = git(root, "show-ref")
                status = git(root, "status", "--porcelain")
                for mode in ([], ["--check-ledger"]):
                    result = subprocess.run(
                        [sys.executable, str(SCRIPT), "--root", str(root),
                         "--base", "main", "--decision-ledger", str(ledger), *mode],
                        capture_output=True, text=True, check=False,
                    )
                    self.assertEqual(result.returncode, int(held), result.stdout + result.stderr)
                    report = json.loads(result.stdout)
                    findings = report if mode else report["decision_ledger"]
                    self.assertEqual(findings["decision_hold_conflicts"], expected)
                    self.assertEqual(findings["ok"], not held)
                    for field in (
                        "duplicate_decision_rows", "duplicate_exclusion_entries",
                        "duplicate_archive_reconciliation_rows",
                    ):
                        self.assertEqual(findings[field], [])
                    if not mode:
                        archives = report["archive_equivalents"]
                        self.assertEqual(archives["decision_hold_conflicts"], expected)
                        self.assertEqual(archives["ok"], not held)
                        self.assertEqual(archives["already_promoted"], [] if held else ["promoted"])
                        self.assertEqual(
                            archives["confirmed_supersession"], [] if held else ["superseded"]
                        )
                        self.assertEqual(
                            archives["unverifiable"], ["promoted", "superseded"] if held else []
                        )
                        if held:
                            for entry in archives["archives"]:
                                self.assertEqual(entry["classification"], "unverifiable")
                                self.assertIn("explicit hold", entry["error"])
                                self.assertNotIn("reconciliation_evidence", entry)
                self.assertEqual(git(root, "show-ref"), before)
                self.assertEqual(git(root, "status", "--porcelain"), status)

    def test_deleted_branch_holds_override_promotion_and_supersession(self) -> None:
        self.assert_deleted_branch_holds_override_approval(remote_only=False)

    def test_deleted_branch_preserves_every_repeated_hold_conflict(self) -> None:
        self.assert_deleted_branch_holds_override_approval(
            remote_only=False, multiple_holds=True,
        )

    def test_deleted_branches_preserve_every_shared_hold_pair(self) -> None:
        root, active_tip = self.make_repo()
        promoted = "promoted-archive"
        superseded = "superseded-archive"
        git(root, "branch", promoted)
        git(root, "tag", "retained-promoted-tip", promoted)
        git(root, "checkout", "-qb", superseded)
        (root / "unique.txt").write_text("archive work\n", encoding="utf-8")
        git(root, "add", "unique.txt")
        git(root, "commit", "-qm", "archive work")
        unique_tip = git(root, "rev-parse", "HEAD")
        git(root, "tag", "retained-superseded-tip", superseded)
        git(root, "checkout", "-q", "main")
        git(root, "branch", "-D", promoted, superseded)
        self.assertEqual(
            git(root, "for-each-ref", "--format=%(refname)", "refs/heads"),
            "refs/heads/main",
        )
        for tag, tip in (
            ("retained-promoted-tip", active_tip),
            ("retained-superseded-tip", unique_tip),
        ):
            self.assertEqual(git(root, "rev-parse", f"refs/tags/{tag}^{{commit}}"), tip)
            self.assertEqual(git(root, "cat-file", "-t", tip), "commit")
        self.assertNotEqual(active_tip, unique_tip)
        self.assertEqual(git(root, "remote"), "")
        rows = [
            f"| `{promoted}` | archive | {active_tip} | approved |",
            f"| `{superseded}` | archive | {unique_tip} | approved |",
        ]
        hold = f"  - `{promoted}` and `{superseded}` — owner hold"
        holds = [
            hold,
            f"- `{superseded}`, `{promoted}` — pending a separate review",
            hold,  # Repeated text must retain both source lines for both branches.
        ]
        review = (
            f"| `{superseded}` | {unique_tip} | {active_tip} | superseded "
            "| Reviewed main replacement | Intentionally replaced. |"
        )
        for held in (True, False):
            with self.subTest(held=held):
                ledger = self.write_ledger(
                    root, "\n".join(rows), "\n".join(holds) if held else "",
                    reconciliations=review,
                )
                # Explicit source positions catch dropped, deduplicated, or
                # cross-wired pairs without deriving expectations from the parser.
                expected = [{
                    "branch": branch,
                    "decision_line": decision_line,
                    "decision_content": row,
                    "hold_line": hold_line,
                    "hold_content": content,
                    "reason": (
                        "archive decision conflicts with an explicit hold; "
                        "change the decision to keep or explicitly resolve the hold "
                        "before planning archive cleanup"
                    ),
                } for branch, decision_line, row in (
                    (promoted, 4, rows[0]), (superseded, 5, rows[1]),
                ) for hold_line, content in zip((13, 14, 15), holds)] if held else []
                refs_before = git(root, "show-ref")
                status_before = git(root, "status", "--porcelain")
                direct = audit_repo.audit_archive_equivalents(root, ledger, "main")
                result = subprocess.run(
                    [sys.executable, str(SCRIPT), "--root", str(root),
                     "--base", "main", "--active-line", "main",
                     "--decision-ledger", str(ledger)],
                    capture_output=True, text=True, check=False,
                )
                # Deleted branches leave stale ledger rows even after holds are
                # removed; archive approval, not CLI success, is the control.
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                report = json.loads(result.stdout)
                self.assertEqual(report["remote_refresh"]["classification"], "no-remotes")
                self.assertFalse(report["remote_refresh"]["tracking_refs_changed"])
                self.assertEqual(
                    report["decision_ledger"]["decision_hold_conflicts"], expected,
                )
                for archives in (direct, report["archive_equivalents"]):
                    self.assertEqual(archives["decision_hold_conflicts"], expected)
                    self.assertEqual(archives["archive_count"], 2)
                    self.assertEqual(archives["ok"], not held)
                    self.assertEqual(
                        archives["unverifiable"], [promoted, superseded] if held else [],
                    )
                    self.assertEqual(archives["unrepresented_changes"], [])
                    self.assertEqual(archives["already_promoted"], [] if held else [promoted])
                    self.assertEqual(
                        archives["confirmed_supersession"], [] if held else [superseded],
                    )
                    self.assertEqual(
                        [entry["branch"] for entry in archives["archives"]],
                        [promoted, superseded],
                    )
                    for entry, branch, tip, approval in zip(
                        archives["archives"], (promoted, superseded),
                        (active_tip, unique_tip),
                        ("already-promoted", "confirmed-supersession"),
                    ):
                        self.assertEqual(entry["branch"], branch)
                        self.assertEqual(entry["tip_sha"], tip)
                        self.assertIsNone(entry["branch_tip_sha"])
                        self.assertEqual(
                            entry["classification"], "unverifiable" if held else approval,
                        )
                        if held:
                            self.assertIn("explicit hold", entry["error"])
                            self.assertNotIn("reconciliation_evidence", entry)
                        elif branch == superseded:
                            self.assertEqual(entry["reconciliation_evidence"], {
                                "reviewed_active_tip_sha": active_tip,
                                "disposition": "superseded",
                                "active_line_evidence": "Reviewed main replacement",
                                "rationale": "Intentionally replaced.",
                            })
                self.assertEqual(git(root, "show-ref"), refs_before)
                self.assertEqual(git(root, "status", "--porcelain"), status_before)

    def test_remote_only_branch_holds_override_promotion_and_supersession(self) -> None:
        self.assert_deleted_branch_holds_override_approval(remote_only=True)

    def test_remote_only_branch_preserves_every_repeated_hold_conflict(self) -> None:
        self.assert_deleted_branch_holds_override_approval(
            remote_only=True, multiple_holds=True,
        )

    def test_upstream_only_branch_holds_override_promotion_and_supersession(self) -> None:
        self.assert_deleted_branch_holds_override_approval(
            remote_only=True, remote_name="upstream",
        )

    def test_remote_only_branch_holds_survive_moved_tracking_tip(self) -> None:
        self.assert_deleted_branch_holds_override_approval(
            remote_only=True, moved_tracking_tip=True,
        )

    def assert_deleted_branch_holds_override_approval(
        self, *, remote_only: bool, multiple_holds: bool = False,
        moved_tracking_tip: bool = False, remote_name: str = "origin",
    ) -> None:
        for approval in ("already-promoted", "confirmed-supersession"):
            with self.subTest(approval=approval):
                root, active_tip = self.make_repo()
                branch = "held-archive"
                git(root, "checkout", "-qb", branch)
                if approval == "confirmed-supersession":
                    (root / "unique.txt").write_text("archive work\n", encoding="utf-8")
                    git(root, "add", "unique.txt")
                    git(root, "commit", "-qm", "archive work")
                tip = git(root, "rev-parse", "HEAD")
                retained_ref = (
                    f"refs/remotes/{remote_name}/{branch}"
                    if remote_only else "refs/tags/retained-archive-tip"
                )
                tracking_tip = tip
                if moved_tracking_tip:
                    self.assertTrue(remote_only)
                    # Keep the exact ledger commit independently reachable while
                    # the only surviving branch-name ref advances past it.
                    git(root, "update-ref", "refs/tags/retained-archive-tip", tip)
                    (root / "later.txt").write_text("later remote work\n", encoding="utf-8")
                    git(root, "add", "later.txt")
                    git(root, "commit", "-qm", "advance tracking tip")
                    tracking_tip = git(root, "rev-parse", "HEAD")
                    self.assertNotEqual(tracking_tip, tip)
                git(root, "update-ref", retained_ref, tracking_tip)
                git(root, "checkout", "-q", "main")
                git(root, "branch", "-D", branch)
                self.assertEqual(
                    git(root, "for-each-ref", "--format=%(refname)", f"refs/heads/{branch}"),
                    "",
                )
                self.assertEqual(
                    git(root, "rev-parse", f"{retained_ref}^{{commit}}"), tracking_tip,
                )
                if moved_tracking_tip:
                    self.assertEqual(
                        git(root, "rev-parse", "refs/tags/retained-archive-tip^{commit}"), tip,
                    )
                    for retained_tip in (tip, tracking_tip):
                        self.assertEqual(git(root, "cat-file", "-t", retained_tip), "commit")
                # A tracking ref needs no configured remote: the full CLI must
                # exercise local name resolution without any network access.
                self.assertEqual(git(root, "remote"), "")
                if remote_only:
                    expected_refs = ["refs/heads/main", retained_ref]
                    if moved_tracking_tip:
                        expected_refs.append("refs/tags/retained-archive-tip")
                    self.assertEqual(
                        git(root, "for-each-ref", "--format=%(refname)"),
                        "\n".join(expected_refs),
                    )
                    self.assertEqual(
                        git(root, "rev-parse", f"{remote_name}/{branch}^{{commit}}"), tracking_tip,
                    )
                row = f"  | `{branch}` | **archive** | `{tip}` | approved |"
                hold = f"  - `{branch}` — owner says do not clean up"
                holds = [
                    hold,
                    f"- `{branch}` — pending a separate owner review",
                    hold,  # Identical content still has a distinct source line.
                ] if multiple_holds else [hold]
                review = (
                    f"| `{branch}` | {tip} | {active_tip} | superseded "
                    "| Reviewed main replacement | Intentionally replaced. |"
                    if approval == "confirmed-supersession" else ""
                )
                for held in (True, False):
                    with self.subTest(held=held):
                        ledger = self.write_ledger(
                            root, row, "\n".join(holds) if held else "",
                            reconciliations=review,
                        )
                        lines = ledger.read_text(encoding="utf-8").splitlines()
                        expected = [{
                            "branch": branch,
                            "decision_line": lines.index(row) + 1,
                            "decision_content": row,
                            "hold_line": line_number,
                            "hold_content": content,
                            "reason": (
                                "archive decision conflicts with an explicit hold; "
                                "change the decision to keep or explicitly resolve the hold "
                                "before planning archive cleanup"
                            ),
                        } for line_number, content in enumerate(lines, start=1)
                            if content in holds] if held else []
                        if multiple_holds and held:
                            self.assertEqual(
                                [(item["decision_line"], item["hold_line"]) for item in expected],
                                [(4, 12), (4, 13), (4, 14)],
                            )
                        elif held:
                            self.assertEqual(
                                [(item["decision_line"], item["hold_line"]) for item in expected],
                                [(4, 12)],
                            )
                        refs_before = git(root, "show-ref")
                        status_before = git(root, "status", "--porcelain")
                        direct = audit_repo.audit_archive_equivalents(root, ledger, "main")
                        self.assertEqual(git(root, "show-ref"), refs_before)
                        self.assertEqual(git(root, "status", "--porcelain"), status_before)
                        result = subprocess.run(
                            [sys.executable, str(SCRIPT), "--root", str(root),
                             "--base", "main", "--active-line", "main",
                             "--decision-ledger", str(ledger)],
                            capture_output=True, text=True, check=False,
                        )
                        # Even without a hold, the deleted branch leaves a stale
                        # ledger row; use archive classification, not CLI success,
                        # to prove the approval evidence is otherwise valid.
                        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                        report = json.loads(result.stdout)
                        self.assertEqual(report["remote_refresh"]["classification"], "no-remotes")
                        self.assertFalse(report["remote_refresh"]["tracking_refs_changed"])
                        self.assertEqual(
                            report["decision_ledger"]["decision_hold_conflicts"], expected,
                        )
                        for archives in (direct, report["archive_equivalents"]):
                            self.assertEqual(archives["decision_hold_conflicts"], expected)
                            self.assertEqual(archives["ok"], not held)
                            self.assertEqual(archives["archive_count"], 1)
                            self.assertEqual(archives["unverifiable"], [branch] if held else [])
                            self.assertEqual(archives["unrepresented_changes"], [])
                            self.assertEqual(
                                archives["already_promoted"],
                                [branch] if not held and approval == "already-promoted" else [],
                            )
                            self.assertEqual(
                                archives["confirmed_supersession"],
                                [branch] if not held and approval == "confirmed-supersession" else [],
                            )
                            entry = archives["archives"][0]
                            self.assertEqual(entry["branch"], branch)
                            self.assertEqual(entry["tip_sha"], tip)
                            # A surviving tracking ref is not a local branch,
                            # nor a substitute for the exact saved ledger SHA.
                            self.assertIsNone(entry["branch_tip_sha"])
                            self.assertEqual(
                                entry["classification"], "unverifiable" if held else approval,
                            )
                            if held:
                                self.assertIn("explicit hold", entry["error"])
                                self.assertNotIn("reconciliation_evidence", entry)
                            elif approval == "confirmed-supersession":
                                self.assertEqual(
                                    entry["reconciliation_evidence"]["disposition"], "superseded",
                                )
                        self.assertEqual(git(root, "show-ref"), refs_before)
                        self.assertEqual(git(root, "status", "--porcelain"), status_before)

    def test_hold_matching_is_exact_and_keep_overlap_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = self.write_ledger(
                root,
                "\n".join([
                    f"| `Held` | archive | {'a' * 40} | approved |",
                    f"| `kept` | keep | {'b' * 40} | active |",
                ]),
                "- `held` and `kept` — active",
            )
            report = audit_repo.validate_decision_ledger_structure(ledger)
            self.assertEqual(report["decision_hold_conflicts"], [])
            self.assertTrue(report["ok"])

    def test_hold_conflicts_do_not_hide_duplicate_findings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            row = f"| `held` | archive | {'a' * 40} | approved |"
            hold = "- `held` — active"
            ledger = self.write_ledger(root, f"{row}\n{row}", f"{hold}\n{hold}")
            report = audit_repo.validate_decision_ledger_structure(ledger)
            self.assertEqual(len(report["duplicate_decision_rows"]), 1)
            self.assertEqual(len(report["duplicate_exclusion_entries"]), 1)
            self.assertEqual(
                [(r["decision_line"], r["hold_line"]) for r in report["decision_hold_conflicts"]],
                [(4, 13), (4, 14), (5, 13), (5, 14)],
            )
            self.assertFalse(report["ok"])

    def test_duplicate_decisions_fail_both_cli_checks_in_either_order(self) -> None:
        root, initial = self.make_repo()
        git(root, "branch", "reviewed")
        (root / "README.md").write_text("active update\n", encoding="utf-8")
        git(root, "commit", "-qam", "active update")
        active_tip = git(root, "rev-parse", "HEAD")
        archive_row = f"| `reviewed` | **archive** | `{initial}` | retained |"
        keep_row = f"| `reviewed` | **keep** | `{initial}` | active |"
        variants = {
            "identical archive": (archive_row, archive_row),
            "identical keep": (keep_row, keep_row),
            "retention label": (archive_row, keep_row),
            "tip SHA": (
                archive_row,
                f"| `reviewed` | **archive** | `{active_tip}` | retained |",
            ),
            "label and tip SHA": (
                archive_row,
                f"| `reviewed` | **keep** | `{active_tip}` | active |",
            ),
            "normalized cells": (
                archive_row,
                f"| `reviewed` | ARCHIVE | {initial.upper()} | retained |",
            ),
        }
        branches = audit_repo.audit_branches(root, "main", refresh=False)
        for name, pair in variants.items():
            for reverse in (False, True):
                with self.subTest(variant=name, reverse=reverse):
                    rows = list(reversed(pair)) if reverse else list(pair)
                    # All repeats refer to the original, not the previous repeat.
                    rows.append(rows[1])
                    ledger = self.write_ledger(
                        root,
                        "\n".join(rows),
                        reconciliations=(
                            f"| `reviewed` | {initial} | {initial} | superseded "
                            "| Reviewed replacement | Reason. |"
                        ),
                    )
                    expected = [
                        {
                            "line": 4 + index,
                            "content": rows[index],
                            "branch": "reviewed",
                            "first_line": 4,
                            "first_content": rows[0],
                            "reason": "duplicate branch decision; retain one unambiguous row per branch",
                        }
                        for index in (1, 2)
                    ]
                    parsed = audit_repo.parse_decision_ledger(ledger)
                    self.assertEqual(len(parsed.decisions), 3)
                    self.assertEqual(parsed.duplicate_decision_rows, expected)
                    coverage = audit_repo.audit_decision_ledger(
                        root, branches, "main", ledger
                    )
                    self.assertEqual(coverage["covered_branch_count"], 0)
                    self.assertEqual(coverage["missing_branches"], ["reviewed"])
                    self.assertEqual(coverage["tip_sha_drift"], [])
                    self.assertFalse(coverage["ok"])
                    for mode in ([], ["--check-ledger"]):
                        result = subprocess.run(
                            [
                                sys.executable, str(SCRIPT), "--root", str(root),
                                "--base", "main", "--decision-ledger", str(ledger), *mode,
                            ],
                            capture_output=True, text=True, check=False,
                        )
                        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                        report = json.loads(result.stdout)
                        findings = report if mode else report["decision_ledger"]
                        self.assertEqual(findings["duplicate_decision_rows"], expected)
                        self.assertEqual(findings["decision_row_count"], 3)
                        self.assertEqual(findings["duplicate_archive_reconciliation_rows"], [])
                        self.assertFalse(findings["ok"])
                        if not mode:
                            archive = report["archive_equivalents"]
                            self.assertEqual(archive["duplicate_decision_rows"], expected)
                            self.assertEqual(archive["already_promoted"], [])
                            self.assertEqual(archive["confirmed_supersession"], [])
                            self.assertFalse(archive["ok"])
                            for entry in archive["archives"]:
                                self.assertEqual(entry["classification"], "unverifiable")
                                self.assertIn("duplicate branch decisions", entry["error"])
                                self.assertNotIn("reconciliation_evidence", entry)

    def test_duplicate_decisions_block_supersession_even_with_valid_review(self) -> None:
        root, initial = self.make_repo()
        git(root, "checkout", "-qb", "reviewed")
        (root / "archive.txt").write_text("unique work\n", encoding="utf-8")
        git(root, "add", "archive.txt")
        git(root, "commit", "-qm", "archive work")
        tip = git(root, "rev-parse", "HEAD")
        git(root, "checkout", "-q", "main")
        archive_row = f"| `reviewed` | archive | {tip} | retained |"
        for rows in (
            [archive_row],
            [archive_row, archive_row],
            [archive_row, f"| `reviewed` | keep | {tip} | hold |"],
            [f"| `reviewed` | keep | {tip} | hold |", archive_row],
        ):
            with self.subTest(rows=rows):
                ledger = self.write_ledger(
                    root, "\n".join(rows),
                    reconciliations=(
                        f"| `reviewed` | {tip} | {initial} | superseded "
                        "| Reviewed replacement | Reason. |"
                    ),
                )
                report = audit_repo.audit_archive_equivalents(root, ledger, "main")
                if len(rows) == 1:
                    self.assertTrue(report["ok"])
                    self.assertEqual(report["confirmed_supersession"], ["reviewed"])
                else:
                    self.assertFalse(report["ok"])
                    self.assertEqual(report["confirmed_supersession"], [])
                    self.assertEqual(report["unverifiable"], ["reviewed"] * rows.count(archive_row))

    def test_decision_keys_are_case_sensitive_and_checked_without_git(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            row = f"| `reviewed` | keep | {'a' * 40} | hold |"
            other = f"| `Reviewed` | archive | {'a' * 40} | retained |"
            ledger = self.write_ledger(root, "\n".join([row, other]))
            with patch.object(audit_repo.subprocess, "run", side_effect=AssertionError("Git called")):
                self.assertTrue(audit_repo.validate_decision_ledger_structure(ledger)["ok"])
                ledger = self.write_ledger(root, "\n".join([row, other, row]))
                report = audit_repo.validate_decision_ledger_structure(ledger)
                self.assertFalse(report["ok"])
                self.assertEqual(report["duplicate_decision_rows"][0]["first_line"], 4)
                self.assertEqual(report["duplicate_decision_rows"][0]["line"], 6)

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

    def test_fenced_examples_preserve_live_sections_and_line_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for opener, closer in (
                ("```markdown", "```"),
                ("~~~md", "~~~"),
                ("   ```` markdown", "  ````` \t"),
                ("  ~~~~", "   ~~~~~"),
            ):
                with self.subTest(opener=opener):
                    example = "\n".join([
                        opener,
                        "## Branch decisions",
                        f"| `example` | delete | {'b' * 40} | not live |",
                        "## Archive reconciliation evidence",
                        "| malformed example |",
                        "### Explicit exclusions and holds",
                        "## Explicit exclusions and holds",
                        "- `example` — not a live hold",
                        "| malformed hold example |",
                        "## Unrelated notes",
                        closer,
                    ])
                    live_row = f"| `held` | archive | {'a' * 40} | reviewed |"
                    live_hold = "- `held` — owner hold"
                    reconciliation = (
                        f"| `held` | {'a' * 40} | {'c' * 40} | superseded "
                        "| owner review | replaced |"
                    )
                    ledger = self.write_ledger(
                        root,
                        example + "\n" + live_row,
                        example + "\n" + live_hold,
                        example + "\n" + reconciliation,
                    )
                    parsed = audit_repo.parse_decision_ledger(ledger)
                    self.assertEqual(parsed.decisions, [{
                        "branch": "held", "decision": "archive", "tip_sha": "a" * 40,
                    }])
                    self.assertEqual(parsed.exclusions, ["held"])
                    self.assertEqual(len(parsed.archive_reconciliations), 1)
                    self.assertEqual(parsed.archive_reconciliations[0]["branch"], "held")
                    for key in (
                        "malformed_decision_rows", "duplicate_decision_rows",
                        "unsupported_decision_labels", "malformed_exclusion_entries",
                        "duplicate_exclusion_entries", "unsupported_hold_section_headings",
                        "malformed_archive_reconciliation_rows",
                        "duplicate_archive_reconciliation_rows",
                    ):
                        self.assertEqual(getattr(parsed, key), [], key)
                    lines = ledger.read_text(encoding="utf-8").splitlines()
                    self.assertEqual(len(parsed.decision_hold_conflicts), 1)
                    conflict = parsed.decision_hold_conflicts[0]
                    self.assertEqual(conflict["decision_line"], lines.index(live_row) + 1)
                    self.assertEqual(conflict["hold_line"], lines.index(live_hold) + 1)

    def test_fence_closers_must_match_character_length_and_whitespace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for character, other in (("`", "~"), ("~", "`")):
                with self.subTest(character=character):
                    example = "\n".join([
                        character * 4 + "markdown",
                        other * 4,
                        character * 3,
                        character * 4 + " not a closer",
                        "    " + character * 4,
                        "## Branch decisions",
                        "| malformed example |",
                        "## Explicit exclusions and holds",
                        "- `example` — quoted",
                        character * 5,
                    ])
                    ledger = self.write_ledger(
                        root, example + f"\n| `live` | keep | {'a' * 40} | live |",
                    )
                    report = audit_repo.validate_decision_ledger_structure(ledger)
                    self.assertTrue(report["ok"], report)
                    parsed = audit_repo.parse_decision_ledger(ledger)
                    self.assertEqual([row["branch"] for row in parsed.decisions], ["live"])
                    self.assertEqual(parsed.exclusions, [])

    def test_example_only_ledger_has_no_live_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "ledger.md"
            ledger.write_text(
                "```md\n## Explicit exclusions and holds\n"
                "- `held` — quoted only\n```\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "no branch coverage rows"):
                audit_repo.parse_decision_ledger(ledger)

    def test_fenced_examples_and_live_holds_in_both_clis(self) -> None:
        root, tip = self.make_repo()
        git(root, "branch", "held")
        row = f"| `held` | archive | {tip} | promoted |"
        hold = "- `held` — owner hold"
        for marker in ("```", "~~~"):
            for live_hold in ("", hold):
                with self.subTest(marker=marker, live_hold=bool(live_hold)):
                    example = "\n".join([
                        marker + "markdown",
                        "## Branch decisions",
                        row,  # Must not produce a duplicate decision.
                        "| `example` | delete | invalid | quoted |",
                        "| malformed |",
                        "## Archive reconciliation evidence",
                        "| malformed |",
                        "### Explicit exclusions and holds",
                        "## Explicit exclusions and holds",
                        hold,  # Must not create a conflict or duplicate hold.
                        "| malformed |",
                        "## Notes",
                        marker,
                    ])
                    ledger = self.write_ledger(
                        root, example + "\n" + row, example + "\n" + live_hold,
                    )
                    before = git(root, "show-ref"), git(root, "status", "--porcelain")
                    for mode in ([], ["--check-ledger"]):
                        result = subprocess.run(
                            [sys.executable, str(SCRIPT), "--root", str(root),
                             "--base", "main", "--decision-ledger", str(ledger), *mode],
                            capture_output=True, text=True, check=False,
                        )
                        self.assertEqual(
                            result.returncode, 1 if live_hold else 0,
                            result.stdout + result.stderr,
                        )
                        report = json.loads(result.stdout)
                        findings = report if mode else report["decision_ledger"]
                        self.assertEqual(findings["ok"], not bool(live_hold))
                        self.assertEqual(len(findings["decision_hold_conflicts"]), bool(live_hold))
                        for key in (
                            "malformed_decision_rows", "duplicate_decision_rows",
                            "unsupported_decision_labels", "malformed_exclusion_entries",
                            "duplicate_exclusion_entries", "unsupported_hold_section_headings",
                            "malformed_archive_reconciliation_rows",
                            "duplicate_archive_reconciliation_rows",
                        ):
                            self.assertEqual(findings[key], [], key)
                        if not mode:
                            self.assertEqual(
                                report["archive_equivalents"]["ok"], not bool(live_hold),
                            )
                    self.assertEqual(
                        (git(root, "show-ref"), git(root, "status", "--porcelain")), before,
                    )

    def test_unclosed_fences_block_both_clis(self) -> None:
        root, tip = self.make_repo()
        git(root, "branch", "held")
        for marker in ("```", "~~~"):
            with self.subTest(marker=marker):
                ledger = self.write_ledger(
                    root, f"| `held` | archive | {tip} | promoted |",
                    marker + "\n## Notes\n- `held` — hidden by unclosed fence",
                )
                with self.assertRaisesRegex(ValueError, "unclosed code fence at line 12"):
                    audit_repo.parse_decision_ledger(ledger)
                for mode in ([], ["--check-ledger"]):
                    result = subprocess.run(
                        [sys.executable, str(SCRIPT), "--root", str(root),
                         "--base", "main", "--decision-ledger", str(ledger), *mode],
                        capture_output=True, text=True, check=False,
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("unclosed code fence at line 12", result.stdout)

    def test_unsupported_hold_formats_fail_structure_and_focused_cli(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)  # --check-ledger must not need Git.
            for entry in (
                "| `held` | owner hold |",
                "  | held | owner hold |",
                "`held` | owner hold",
                "held | owner hold",
                "held|owner hold",
                "held: owner hold",
                "held:owner hold",
                "held:",
                "> - `held` — owner hold",
                "  > `held` — owner hold",
                "> > held: owner hold",
                "* `held` — owner hold",
                "+ `held` — owner hold",
                "*\t`held` — owner hold",
                "+\t`held` — owner hold",
                "1. `held` — owner hold",
                "2) `held` — owner hold",
                "`held` — owner hold",
                "held — owner hold",
            ):
                with self.subTest(entry=entry):
                    ledger = self.write_ledger(
                        root, "", "These refs must remain unchanged:\n\n" + entry,
                    )
                    expected = [{
                        "line": 14,
                        "content": entry,
                        "reason": (
                            "exclusion entry must list backticked branch names "
                            "followed by an em-dash explanation"
                        ),
                    }]
                    parsed = audit_repo.parse_decision_ledger(ledger)
                    self.assertEqual(parsed.exclusions, [])
                    self.assertEqual(parsed.malformed_exclusion_entries, expected)
                    structure = audit_repo.validate_decision_ledger_structure(ledger)
                    self.assertFalse(structure["ok"])
                    self.assertEqual(structure["malformed_exclusion_entries"], expected)
                    result = subprocess.run(
                        [sys.executable, str(SCRIPT), "--root", str(root),
                         "--decision-ledger", str(ledger), "--check-ledger"],
                        capture_output=True, text=True, check=False,
                    )
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    report = json.loads(result.stdout)
                    self.assertFalse(report["ok"])
                    self.assertEqual(report["malformed_exclusion_entries"], expected)

    def test_unsupported_holds_block_full_cli_despite_complete_branch_decisions(self) -> None:
        root, initial = self.make_repo()
        git(root, "branch", "held")
        rows = f"| `held` | archive | {initial} | already promoted |"
        for table in (
            "| `held` | owner hold |",
            "| Branch | Reason |\n|---|---|\n| `held` | owner hold |",
            "Branch | Reason\n--- | ---\nheld | owner hold",
            "Branch|Reason\n---|---\nheld|owner hold",
            "held: owner hold",
            "held:owner hold",
            "held:",
            "> - `held` — owner hold",
            "  > `held` — owner hold",
            "> > held: owner hold",
        ):
            with self.subTest(table=table):
                ledger = self.write_ledger(
                    root, rows, "These refs must remain unchanged:\n\n" + table,
                )
                expected = [{
                    "line": 14 + index,
                    "content": entry,
                    "reason": (
                        "exclusion entry must list backticked branch names "
                        "followed by an em-dash explanation"
                    ),
                } for index, entry in enumerate(table.splitlines())]
                before = git(root, "show-ref")
                status = git(root, "status", "--porcelain")
                for mode in ([], ["--check-ledger"]):
                    result = subprocess.run(
                        [sys.executable, str(SCRIPT), "--root", str(root),
                         "--base", "main", "--decision-ledger", str(ledger), *mode],
                        capture_output=True, text=True, check=False,
                    )
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    report = json.loads(result.stdout)
                    findings = report if mode else report["decision_ledger"]
                    self.assertFalse(findings["ok"])
                    self.assertEqual(findings["malformed_exclusion_entries"], expected)
                    if not mode:
                        self.assertEqual(findings["missing_branches"], [])
                        self.assertEqual(findings["tip_sha_drift"], [])
                        self.assertEqual(findings["stale_ledger_rows"], [])
                self.assertEqual(git(root, "show-ref"), before)
                self.assertEqual(git(root, "status", "--porcelain"), status)

    def test_repeated_hold_sections_preserve_findings_in_both_clis(self) -> None:
        root, _ = self.make_repo()
        for branch in ("held", "other", "later"):
            git(root, "branch", branch)
        first_hold = "- `main`, `held`, and `other` — active work"
        second_hold = "  - `held`, `other`, and `later` — still active"
        first_table = [
            "| Branch | Reason |",
            "|---|---|",
            "| `held` | owner hold |",
        ]
        second_table = [
            "| `held` | owner hold |",  # Retain both occurrences, not just content.
            "  | `later` | owner review |",
            "other|owner hold",
        ]
        first_section = [
            "These refs must remain unchanged:",
            "",
            first_hold,
            *first_table,
        ]
        second_section = [
            "For context: these branches remain active.",
            "",
            second_hold,
            *second_table,
        ]
        unrelated_section = [
            "",
            "## Release notes",
            "These examples are not retention instructions:",
            "- `unrelated` — documentation only",
            first_hold,  # Would add false duplicates if section state leaked.
            first_table[-1],  # Would add a false malformed-entry finding.
        ]
        for intervening in ([], unrelated_section):
            with self.subTest(intervening_section=bool(intervening)):
                ledger = self.write_ledger(root, "", "\n".join([
                    *first_section,
                    *intervening,
                    "",
                    "## Explicit exclusions and holds",
                    *second_section,
                ]))
                lines = ledger.read_text(encoding="utf-8").splitlines()
                first_start = lines.index(first_section[0]) + 1
                second_start = lines.index(second_section[0]) + 1
                expected_malformed = [{
                    "line": start + 3 + index,
                    "content": entry,
                    "reason": (
                        "exclusion entry must list backticked branch names "
                        "followed by an em-dash explanation"
                    ),
                } for start, table in (
                    (first_start, first_table), (second_start, second_table),
                ) for index, entry in enumerate(table)]
                expected_duplicates = [{
                    "line": second_start + 2,
                    "content": second_hold,
                    "branch": branch,
                    "first_line": first_start + 2,
                } for branch in ("held", "other")]
                parsed = audit_repo.parse_decision_ledger(ledger)
                self.assertEqual(parsed.exclusions, ["held", "later", "main", "other"])
                self.assertEqual(parsed.malformed_exclusion_entries, expected_malformed)
                self.assertEqual(parsed.duplicate_exclusion_entries, expected_duplicates)
                for mode in ([], ["--check-ledger"]):
                    with self.subTest(mode=mode):
                        result = subprocess.run(
                            [sys.executable, str(SCRIPT), "--root", str(root),
                             "--base", "main", "--decision-ledger", str(ledger), *mode],
                            capture_output=True, text=True, check=False,
                        )
                        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                        report = json.loads(result.stdout)
                        findings = report if mode else report["decision_ledger"]
                        self.assertFalse(findings["ok"])
                        self.assertEqual(findings["exclusion_branch_count"], 4)
                        self.assertEqual(
                            findings["malformed_exclusion_entries"], expected_malformed,
                        )
                        self.assertEqual(
                            findings["duplicate_exclusion_entries"], expected_duplicates,
                        )
                        for key in (
                            "malformed_decision_rows", "duplicate_decision_rows",
                            "unsupported_hold_section_headings", "decision_hold_conflicts",
                        ):
                            self.assertEqual(findings[key], [])
                        if not mode:
                            for key in (
                                "missing_branches", "tip_sha_drift", "stale_ledger_rows",
                            ):
                                self.assertEqual(findings[key], [])

    def test_unsupported_hold_headings_fail_focused_cli_without_git(self) -> None:
        headings = [
            "## Explicit exlusions and holds",
            "## Explicit exclusions and hols",
            "## Explict exclusions and holds",
            "## Explicit exclusions adn holds",
            "## Explicit exclusions amd holds",
            "## Explicit exclusions an holds",
            "## Explicit exclusions andd holds",
            "## Explicit exclusions and hold",
            "## Explicit exclusions & holds",
            "## Exclusions and holds",
            "## Explicit holds",
            *[
                f"{'#' * level} Explicit exclusions and holds"
                for level in (1, 3, 4, 5, 6)
            ],
            "  ### Explicit exclusions and holds ###",
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for heading in headings:
                with self.subTest(heading=heading):
                    ledger = self.write_ledger(
                        root, f"| `held` | archive | {'a' * 40} | reviewed |",
                        "- `held` — owner hold",
                    )
                    text = ledger.read_text(encoding="utf-8").replace(
                        "## Explicit exclusions and holds", heading,
                    )
                    ledger.write_text(text, encoding="utf-8")
                    expected = [{
                        "line": text.splitlines().index(heading) + 1,
                        "content": heading,
                        "reason": (
                            "unsupported hold-section heading; use "
                            "'## Explicit exclusions and holds' so holds "
                            "can be validated before cleanup"
                        ),
                    }]
                    parsed = audit_repo.parse_decision_ledger(ledger)
                    self.assertEqual(parsed.unsupported_hold_section_headings, expected)
                    self.assertEqual(parsed.exclusions, [])
                    self.assertEqual(parsed.malformed_decision_rows, [])
                    result = subprocess.run(
                        [sys.executable, str(SCRIPT), "--root", str(root),
                         "--decision-ledger", str(ledger), "--check-ledger"],
                        capture_output=True, text=True, check=False,
                    )
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    report = json.loads(result.stdout)
                    self.assertFalse(report["ok"])
                    self.assertEqual(report["unsupported_hold_section_headings"], expected)

            # Even a draft containing only an unsupported heading retains its
            # line-level finding instead of collapsing into a generic error.
            ledger.write_text("### Explicit exclusions and holds\n", encoding="utf-8")
            report = audit_repo.validate_decision_ledger_structure(ledger)
            self.assertFalse(report["ok"])
            self.assertEqual(report["unsupported_hold_section_headings"][0]["line"], 1)

    def test_unsupported_hold_headings_block_full_audit_of_promoted_archive(self) -> None:
        root, tip = self.make_repo()
        git(root, "branch", "held")
        for heading in (
            "## Explicit exlusions and holds",
            "## Explicit exclusions and hols",
            "## Explicit exclusions adn holds",
            "### Explicit exclusions and holds",
            "# Explicit exclusions and holds",
        ):
            with self.subTest(heading=heading):
                ledger = self.write_ledger(
                    root, f"| `held` | archive | {tip} | promoted |",
                    "- `held` — owner hold",
                )
                ledger.write_text(
                    ledger.read_text(encoding="utf-8").replace(
                        "## Explicit exclusions and holds", heading,
                    ),
                    encoding="utf-8",
                )
                before = git(root, "show-ref"), git(root, "status", "--porcelain")
                result = subprocess.run(
                    [sys.executable, str(SCRIPT), "--root", str(root),
                     "--base", "main", "--decision-ledger", str(ledger)],
                    capture_output=True, text=True, check=False,
                )
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                report = json.loads(result.stdout)
                findings = report["decision_ledger"]
                self.assertFalse(findings["ok"])
                self.assertEqual(findings["exclusion_branch_count"], 0)
                expected = audit_repo.validate_decision_ledger_structure(ledger)
                self.assertEqual(
                    findings["unsupported_hold_section_headings"],
                    expected["unsupported_hold_section_headings"],
                )
                for key in (
                    "missing_branches", "tip_sha_drift", "stale_ledger_rows",
                    "malformed_decision_rows", "malformed_exclusion_entries",
                ):
                    self.assertEqual(findings[key], [])
                self.assertTrue(report["archive_equivalents"]["ok"])
                self.assertEqual(report["remote_refresh"]["status"], "ok")
                self.assertEqual(
                    (git(root, "show-ref"), git(root, "status", "--porcelain")), before,
                )

    def test_unrelated_headings_do_not_become_holds(self) -> None:
        root, tip = self.make_repo()
        git(root, "branch", "reviewed")
        ledger = self.write_ledger(
            root, f"| `reviewed` | archive | {tip} | promoted |",
        )
        with ledger.open("a", encoding="utf-8") as stream:
            stream.write("\n" + "\n".join(
                f"{heading}\n- `reviewed` — unrelated example"
                for heading in (
                    "## Release notes", "## Household notes",
                    "## Explicit exclusions and holds policy",
                    "## Discussion of explicit exclusions and holds",
                    "## Explicit exclusions and goals",
                    "### Holds in ordinary prose",
                )
            ))
        for mode in ([], ["--check-ledger"]):
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--root", str(root),
                 "--base", "main", "--decision-ledger", str(ledger), *mode],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = json.loads(result.stdout)
            findings = report if mode else report["decision_ledger"]
            self.assertTrue(findings["ok"])
            self.assertEqual(findings["unsupported_hold_section_headings"], [])
            self.assertEqual(findings["exclusion_branch_count"], 0)

    def test_introductory_prose_and_multi_branch_holds_pass_both_clis(self) -> None:
        root, _ = self.make_repo()
        for branch in ("held", "other"):
            git(root, "branch", branch)
        ledger = self.write_ledger(
            root, "", "\n".join([
                "These refs are not part of the decision set:",
                "For context: these branches remain active.",
                "Keep `held` and `other` unchanged until the owner reviews them.",
                "",
                "  - `main`, `held`, and `other` — active work",
            ]),
        )
        parsed = audit_repo.parse_decision_ledger(ledger)
        self.assertEqual(parsed.exclusions, ["held", "main", "other"])
        self.assertEqual(parsed.malformed_exclusion_entries, [])
        for mode in ([], ["--check-ledger"]):
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--root", str(root),
                 "--base", "main", "--decision-ledger", str(ledger), *mode],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = json.loads(result.stdout)
            findings = report if mode else report["decision_ledger"]
            self.assertTrue(findings["ok"])
            self.assertEqual(findings["malformed_exclusion_entries"], [])
            self.assertEqual(findings["exclusion_branch_count"], 3)

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

    def test_malformed_reconciliations_have_matching_line_level_findings(self) -> None:
        root, initial = self.make_repo()
        git(root, "branch", "archived")
        valid_cells = [
            "`archived`", f"`{initial}`", f"`{initial}`",
            "**superseded**", "Replacement commit", "Intentionally replaced.",
        ]
        cases = [
            (valid_cells[:5], "archive reconciliation row must contain exactly six cells"),
            (valid_cells + ["extra"], "archive reconciliation row must contain exactly six cells"),
            (["archived"] + valid_cells[1:], "branch cell must contain one backticked branch name"),
        ]
        for index, label in ((1, "archive tip SHA"), (2, "reviewed active tip SHA")):
            for invalid_sha in ("", "`abc123`", "z" * 40, f"`{initial}", f"``{initial}``"):
                cells = valid_cells.copy()
                cells[index] = invalid_sha
                cases.append((
                    cells,
                    f"{label} cell must contain one full 40-character commit SHA",
                ))
        for index, value, reason in (
            (3, "", "disposition must be reconciled or superseded"),
            (3, "**supersededd**", "disposition must be reconciled or superseded"),
            (4, "", "active-line evidence cell is empty"),
            (5, "", "rationale cell is empty"),
        ):
            cells = valid_cells.copy()
            cells[index] = value
            cases.append((cells, reason))

        for cells, reason in cases:
            with self.subTest(cells=cells):
                row = "| " + " | ".join(cells) + " |"
                ledger = self.write_ledger(
                    root,
                    f"| `archived` | **archive** | `{initial}` | reviewed |",
                    reconciliations=row,
                )
                expected = [{"line": 9, "content": row, "reason": reason}]
                focused = audit_repo.validate_decision_ledger_structure(ledger)
                full = audit_repo.audit_decision_ledger(
                    root, self.branch_facts(root), "main", ledger
                )
                for report in (focused, full):
                    self.assertEqual(
                        report["malformed_archive_reconciliation_rows"], expected
                    )
                    self.assertFalse(report["ok"])
                self.assertEqual(
                    audit_repo.parse_decision_ledger(ledger).archive_reconciliations,
                    [],
                )

    def test_reconciliation_cli_findings_match_and_fail_both_checks(self) -> None:
        root, initial = self.make_repo()
        git(root, "branch", "archived")
        rows = [
            "| `archived` | truncated |",
            f"`archived` | `{initial}` | `{initial}` | superseded | evidence | reason |",
        ]
        ledger = self.write_ledger(
            root,
            f"| `archived` | **archive** | `{initial}` | reviewed |",
            reconciliations="\n".join(rows),
        )
        reports = []
        for mode in ([], ["--check-ledger"]):
            result = subprocess.run(
                [
                    sys.executable, str(SCRIPT), "--root", str(root),
                    "--base", "main", "--decision-ledger", str(ledger), *mode,
                ],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 1)
            report = json.loads(result.stdout)
            reports.append(report if mode else report["decision_ledger"])
        expected = [
            {
                "line": 9 + index,
                "content": row,
                "reason": "archive reconciliation row must contain exactly six cells",
            }
            for index, row in enumerate(rows)
        ]
        for report in reports:
            self.assertEqual(report["malformed_archive_reconciliation_rows"], expected)
            self.assertFalse(report["ok"])

    def test_clean_reconciliation_evidence_passes_both_cli_checks(self) -> None:
        root, initial = self.make_repo()
        git(root, "branch", "archived")
        (root / "README.md").write_text("active update\n", encoding="utf-8")
        git(root, "commit", "-qam", "active update")
        active_tip = git(root, "rev-parse", "HEAD")
        ledger = self.write_ledger(
            root,
            f"| `archived` | **archive** | `{initial}` | reviewed |",
            reconciliations="\n".join([
                "These rows explain the reviewed archive tips.",
                "",
                f"| `archived` | `{initial.upper()}` | `{initial}` | **superseded** | Replacement | Reason. |",
                f"| `archived` | {active_tip} | {initial} | reconciled | Promotion | Reason. |",
                "",
            ]),
        )
        parsed = audit_repo.parse_decision_ledger(ledger)
        self.assertEqual(len(parsed.archive_reconciliations), 2)
        self.assertEqual(parsed.archive_reconciliations[0]["tip_sha"], initial)
        self.assertEqual(
            [row["disposition"] for row in parsed.archive_reconciliations],
            ["superseded", "reconciled"],
        )
        for mode in ([], ["--check-ledger"]):
            with self.subTest(mode=mode):
                result = subprocess.run(
                    [
                        sys.executable, str(SCRIPT), "--root", str(root),
                        "--base", "main", "--decision-ledger", str(ledger), *mode,
                    ],
                    capture_output=True, text=True, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                report = json.loads(result.stdout)
                report = report if mode else report["decision_ledger"]
                self.assertEqual(report["malformed_archive_reconciliation_rows"], [])
                self.assertEqual(report["duplicate_archive_reconciliation_rows"], [])
                self.assertTrue(report["ok"])

    def test_duplicate_reconciliations_preserve_first_and_fail_both_cli_checks(self) -> None:
        root, initial = self.make_repo()
        git(root, "checkout", "-qb", "archived")
        (root / "archive.txt").write_text("unique archive work\n", encoding="utf-8")
        git(root, "add", "archive.txt")
        git(root, "commit", "-qm", "archive work")
        archive_tip = git(root, "rev-parse", "HEAD")
        git(root, "checkout", "-q", "main")
        original_cells = [
            "`archived`", f"`{archive_tip}`", f"`{initial}`",
            "**superseded**", "Replacement", "Reason.",
        ]
        variants = {
            "identical": original_cells,
            "normalized": [
                "`archived`", archive_tip.upper(), initial.upper(),
                "SUPERSEDED", "Replacement", "Reason.",
            ],
            "active tip": original_cells[:2] + [f"`{archive_tip}`"] + original_cells[3:],
            "disposition": original_cells[:3] + ["reconciled"] + original_cells[4:],
            "evidence": original_cells[:4] + ["Different replacement", "Reason."],
            "rationale": original_cells[:5] + ["Different reason."],
        }
        for name, repeated_cells in variants.items():
            for reverse in (False, True):
                with self.subTest(variant=name, reverse=reverse):
                    cells = [original_cells, repeated_cells]
                    if reverse:
                        cells.reverse()
                    rows = ["| " + " | ".join(row) + " |" for row in cells]
                    # Every repeat must point to the first row, not the prior repeat.
                    rows.append(rows[1])
                    ledger = self.write_ledger(
                        root,
                        f"| `archived` | **archive** | `{archive_tip}` | reviewed |",
                        reconciliations="\n".join(rows),
                    )
                    expected = [
                        {
                            "line": 9 + index,
                            "content": rows[index],
                            "branch": "archived",
                            "tip_sha": archive_tip,
                            "first_line": 9,
                            "first_content": rows[0],
                            "reason": "duplicate archive reconciliation branch and tip SHA",
                        }
                        for index in (1, 2)
                    ]
                    parsed = audit_repo.parse_decision_ledger(ledger)
                    self.assertEqual(len(parsed.archive_reconciliations), 1)
                    self.assertEqual(
                        parsed.archive_reconciliations[0]["active_tip_sha"],
                        cells[0][2].strip("`").lower(),
                    )
                    self.assertEqual(
                        parsed.archive_reconciliations[0]["disposition"],
                        cells[0][3].strip("*").lower(),
                    )
                    self.assertEqual(
                        parsed.archive_reconciliations[0]["active_line_evidence"], cells[0][4]
                    )
                    self.assertEqual(parsed.archive_reconciliations[0]["rationale"], cells[0][5])
                    for mode in ([], ["--check-ledger"]):
                        result = subprocess.run(
                            [
                                sys.executable, str(SCRIPT), "--root", str(root),
                                "--base", "main", "--decision-ledger", str(ledger), *mode,
                            ],
                            capture_output=True, text=True, check=False,
                        )
                        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                        report = json.loads(result.stdout)
                        findings = report if mode else report["decision_ledger"]
                        self.assertEqual(findings["duplicate_archive_reconciliation_rows"], expected)
                        self.assertFalse(findings["ok"])
                        if not mode:
                            archive = report["archive_equivalents"]
                            self.assertEqual(archive["duplicate_archive_reconciliation_rows"], expected)
                            self.assertEqual(archive["confirmed_supersession"], [])
                            self.assertEqual(archive["unrepresented_changes"], ["archived"])
                            self.assertNotIn("reconciliation_evidence", archive["archives"][0])
                            self.assertFalse(archive["ok"])

    def test_reconciliation_keys_include_branch_and_check_without_git(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tip = "a" * 40
            first = f"| `one` | {tip} | {tip} | superseded | Replacement | Reason. |"
            second = first.replace("`one`", "`two`")
            ledger = self.write_ledger(
                root, "", "- `one`, `two` — retained",
                reconciliations="\n".join([first, second]),
            )
            self.assertTrue(audit_repo.validate_decision_ledger_structure(ledger)["ok"])
            ledger = self.write_ledger(
                root, "", "- `one`, `two` — retained",
                reconciliations="\n".join([first, second, first]),
            )
            with patch.object(audit_repo, "_git_result", side_effect=AssertionError("Git used")):
                report = audit_repo.validate_decision_ledger_structure(ledger)
            self.assertFalse(report["ok"])
            self.assertEqual(report["duplicate_archive_reconciliation_rows"][0]["first_line"], 9)
            self.assertEqual(report["duplicate_archive_reconciliation_rows"][0]["line"], 11)

    def test_reconciliation_only_malformed_draft_keeps_line_findings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger = self.write_ledger(
                Path(directory), "", reconciliations="| `archive` | truncated |"
            )
            report = audit_repo.validate_decision_ledger_structure(ledger)
            self.assertFalse(report["ok"])
            self.assertEqual(report["malformed_archive_reconciliation_rows"][0]["line"], 9)

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

    def test_archive_merge_resolution_is_not_hidden_by_patch_equivalents(self) -> None:
        root, _ = self.make_repo()
        git(root, "checkout", "-qb", "feature")
        (root / "feature.txt").write_text("promoted feature\n", encoding="utf-8")
        git(root, "add", "feature.txt")
        git(root, "commit", "-qm", "feature work")
        feature_tip = git(root, "rev-parse", "HEAD")

        git(root, "checkout", "-q", "main")
        git(root, "checkout", "-qb", "archived")
        git(root, "merge", "--no-ff", "--no-commit", "feature")
        (root / "resolution.txt").write_text(
            "archive merge resolution\n", encoding="utf-8"
        )
        git(root, "add", "resolution.txt")
        git(root, "commit", "-qm", "merge feature with archive resolution")
        archive_tip = git(root, "rev-parse", "HEAD")

        git(root, "checkout", "-q", "main")
        git(root, "cherry-pick", "-n", feature_tip)
        git(root, "commit", "-qm", "promote feature work")
        active_tip = git(root, "rev-parse", "HEAD")
        ledger = self.write_ledger(
            root,
            f"| `archived` | **archive** | `{archive_tip}` | reviewed |",
        )

        result = audit_repo.audit_archive_equivalents(root, ledger, "main")

        self.assertEqual(result["active_line_tip_sha"], active_tip)
        self.assertEqual(result["already_promoted"], [])
        self.assertEqual(result["unrepresented_changes"], ["archived"])
        self.assertEqual(result["unverifiable"], [])
        archive = result["archives"][0]
        self.assertEqual(archive["classification"], "unrepresented-changes")
        self.assertFalse(archive["tree_difference"]["same"])
        self.assertEqual(
            archive["commit_differences"]["unrepresented_patch_commit_count"], 0
        )
        self.assertEqual(
            archive["commit_differences"]["promoted_commit_count"], 1
        )
        self.assertEqual(
            archive["commit_differences"]["unrepresented_commit_count"], 1
        )
        merge_commits = archive["commit_differences"][
            "unrepresented_merge_commits"
        ]
        self.assertEqual(len(merge_commits), 1)
        self.assertEqual(
            merge_commits[0]["subject"], "merge feature with archive resolution"
        )
        resolution_evidence = archive["commit_differences"][
            "merge_resolution_differences"
        ]
        self.assertEqual(len(resolution_evidence), 1)
        self.assertEqual(
            resolution_evidence[0]["file_differences"],
            [{"status": "A", "path": "resolution.txt"}],
        )
        self.assertEqual(
            resolution_evidence[0]["archive_tip_differences_since_merge"], []
        )
        self.assertEqual(archive["file_differences"], [
            {"status": "A", "path": "resolution.txt"},
        ])

    def test_archive_merge_resolution_revert_and_reapply_ignores_old_merge(
        self,
    ) -> None:
        root, _ = self.make_repo()
        git(root, "checkout", "-qb", "feature")
        (root / "feature.txt").write_text("promoted feature\n", encoding="utf-8")
        git(root, "add", "feature.txt")
        git(root, "commit", "-qm", "feature work")
        feature_tip = git(root, "rev-parse", "HEAD")

        git(root, "checkout", "-q", "main")
        git(root, "checkout", "-qb", "archived")
        git(root, "merge", "--no-ff", "--no-commit", "feature")
        (root / "resolution.txt").write_text(
            "archive merge resolution\n", encoding="utf-8"
        )
        git(root, "add", "resolution.txt")
        git(root, "commit", "-qm", "merge feature with archive resolution")
        merge_sha = git(root, "rev-parse", "HEAD")

        git(root, "rm", "resolution.txt")
        git(root, "commit", "-qm", "revert archive merge resolution")
        (root / "resolution.txt").write_text(
            "archive merge resolution\n", encoding="utf-8"
        )
        git(root, "add", "resolution.txt")
        git(root, "commit", "-qm", "reapply archive resolution")
        reapply_sha = git(root, "rev-parse", "HEAD")
        archive_tip = git(root, "rev-parse", "HEAD")

        git(root, "checkout", "-q", "main")
        git(root, "cherry-pick", "-n", feature_tip)
        git(root, "commit", "-qm", "promote feature work")
        active_tip = git(root, "rev-parse", "HEAD")
        ledger = self.write_ledger(
            root,
            f"| `archived` | **archive** | `{archive_tip}` | reviewed |",
        )

        result = audit_repo.audit_archive_equivalents(root, ledger, "main")

        self.assertEqual(result["active_line_tip_sha"], active_tip)
        self.assertEqual(result["unverifiable"], [])
        self.assertEqual(result["unrepresented_changes"], ["archived"])
        archive = result["archives"][0]
        self.assertEqual(archive["classification"], "unrepresented-changes")
        differences = archive["commit_differences"]
        self.assertEqual(differences["unrepresented_patch_commit_count"], 2)
        self.assertEqual(differences["unrepresented_commit_count"], 2)
        self.assertEqual(
            differences["unrepresented_merge_commits"],
            [],
        )
        self.assertEqual(
            differences["latest_archive_commit_by_path"],
            [{"path": "resolution.txt", "sha": reapply_sha}],
        )
        self.assertEqual(
            [
                item["sha"]
                for item in differences["archive_commits"]
                if item["subject"] == "reapply archive resolution"
            ],
            [reapply_sha],
        )
        resolution = differences["merge_resolution_differences"][0]
        self.assertEqual(resolution["merge_commit"]["sha"], merge_sha)
        self.assertEqual(
            resolution["file_differences"],
            [{"status": "A", "path": "resolution.txt"}],
        )
        self.assertEqual(
            resolution["archive_tip_differences_since_merge"],
            [],
        )

    def test_archive_merge_resolution_rename_reports_both_paths(self) -> None:
        root, _ = self.make_repo()
        (root / "shared.txt").write_text("base content\n", encoding="utf-8")
        git(root, "add", "shared.txt")
        git(root, "commit", "-qm", "add shared content")

        git(root, "checkout", "-qb", "feature")
        (root / "shared.txt").write_text("feature content\n", encoding="utf-8")
        git(root, "add", "shared.txt")
        git(root, "commit", "-qm", "feature changes shared content")
        feature_tip = git(root, "rev-parse", "HEAD")

        git(root, "checkout", "-q", "main")
        git(root, "checkout", "-qb", "archived")
        (root / "shared.txt").write_text("archive content\n", encoding="utf-8")
        git(root, "add", "shared.txt")
        git(root, "commit", "-qm", "archive changes shared content")
        merge_result = subprocess.run(
            ["git", "merge", "--no-ff", "--no-commit", "feature"],
            cwd=root,
            capture_output=True,
            text=True,
        )
        self.assertEqual(merge_result.returncode, 1, merge_result.stderr)

        (root / "shared.txt").unlink()
        (root / "renamed-shared.txt").write_text(
            "archive resolution at renamed path\n", encoding="utf-8"
        )
        git(root, "add", "-A")
        git(root, "commit", "-qm", "resolve conflict by moving shared file")
        merge_sha = git(root, "rev-parse", "HEAD")
        archive_tip = git(root, "rev-parse", "HEAD")

        git(root, "checkout", "-q", "main")
        git(root, "cherry-pick", "-n", feature_tip)
        git(root, "commit", "-qm", "promote feature content")
        active_tip = git(root, "rev-parse", "HEAD")
        ledger = self.write_ledger(
            root,
            f"| `archived` | **archive** | `{archive_tip}` | reviewed |",
        )

        result = audit_repo.audit_archive_equivalents(root, ledger, "main")

        self.assertEqual(result["active_line_tip_sha"], active_tip)
        self.assertEqual(result["unverifiable"], [])
        self.assertEqual(result["unrepresented_changes"], ["archived"])
        archive = result["archives"][0]
        self.assertEqual(archive["classification"], "unrepresented-changes")
        self.assertEqual(
            archive["commit_differences"]["archive_only_merge_commits"][0]["sha"],
            merge_sha,
        )
        self.assertEqual(
            archive["commit_differences"]["merge_resolution_differences"][0][
                "file_differences"
            ],
            [
                {"status": "A", "path": "renamed-shared.txt"},
                {"status": "D", "path": "shared.txt"},
            ],
        )
        self.assertEqual(
            archive["file_differences"],
            [
                {"status": "A", "path": "renamed-shared.txt"},
                {"status": "D", "path": "shared.txt"},
            ],
        )
        self.assertEqual(
            [
                merge["sha"]
                for merge in archive["commit_differences"][
                    "unrepresented_merge_commits"
                ]
            ],
            [merge_sha],
        )

    def test_archive_review_counts_only_unrepresented_resolution_across_merges(
        self,
    ) -> None:
        root, _ = self.make_repo()
        (root / "shared.txt").write_text("base content\n", encoding="utf-8")
        git(root, "add", "shared.txt")
        git(root, "commit", "-qm", "add shared content")
        base_tip = git(root, "rev-parse", "HEAD")

        git(root, "checkout", "-qb", "feature-clean")
        (root / "shared.txt").write_text(
            "clean-feature content\n", encoding="utf-8"
        )
        (root / "clean-feature.txt").write_text(
            "promoted clean feature\n", encoding="utf-8"
        )
        git(root, "add", "shared.txt", "clean-feature.txt")
        git(root, "commit", "-qm", "clean feature work")
        clean_feature_tip = git(root, "rev-parse", "HEAD")

        git(root, "checkout", "-q", base_tip)
        git(root, "checkout", "-qb", "feature-resolution")
        (root / "shared.txt").write_text(
            "feature-side content\n", encoding="utf-8"
        )
        (root / "resolution-feature.txt").write_text(
            "promoted resolution feature\n", encoding="utf-8"
        )
        git(root, "add", "shared.txt", "resolution-feature.txt")
        git(root, "commit", "-qm", "resolution feature work")
        resolution_feature_tip = git(root, "rev-parse", "HEAD")

        git(root, "checkout", "-q", base_tip)
        git(root, "checkout", "-qb", "archived")
        git(
            root,
            "merge",
            "--no-ff",
            "-m",
            "archive clean merge",
            "feature-clean",
        )
        clean_merge_sha = git(root, "rev-parse", "HEAD")
        merge_result = subprocess.run(
            ["git", "merge", "--no-ff", "--no-commit", "feature-resolution"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(merge_result.returncode, 1, merge_result.stderr)
        (root / "shared.txt").write_text(
            "archive-side resolution\n", encoding="utf-8"
        )
        git(root, "add", "shared.txt")
        git(root, "commit", "-qm", "archive resolution merge")
        resolution_merge_sha = git(root, "rev-parse", "HEAD")
        archive_tip = git(root, "rev-parse", "HEAD")

        git(root, "checkout", "-q", base_tip)
        git(root, "checkout", "-qb", "main-review")
        git(
            root,
            "merge",
            "--no-ff",
            "-m",
            "active clean merge",
            "feature-clean",
        )
        merge_result = subprocess.run(
            ["git", "merge", "--no-ff", "--no-commit", "feature-resolution"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(merge_result.returncode, 1, merge_result.stderr)
        (root / "shared.txt").write_text(
            "active-line resolution\n", encoding="utf-8"
        )
        git(root, "add", "shared.txt")
        git(root, "commit", "-qm", "active resolution merge")
        active_tip = git(root, "rev-parse", "HEAD")
        ledger = self.write_ledger(
            root,
            f"| `archived` | **archive** | `{archive_tip}` | reviewed |",
        )

        result = audit_repo.audit_archive_equivalents(root, ledger, "main-review")

        self.assertEqual(result["active_line_tip_sha"], active_tip)
        self.assertEqual(result["unrepresented_changes"], ["archived"])
        self.assertEqual(result["unverifiable"], [])
        archive = result["archives"][0]
        self.assertEqual(archive["classification"], "unrepresented-changes")
        self.assertEqual(
            archive["file_differences"],
            [{"status": "M", "path": "shared.txt"}],
        )

        differences = archive["commit_differences"]
        self.assertEqual(differences["unrepresented_patch_commit_count"], 0)
        self.assertEqual(differences["unrepresented_commit_count"], 1)
        self.assertEqual(
            [
                commit["sha"]
                for commit in differences["archive_only_merge_commits"]
            ],
            [resolution_merge_sha, clean_merge_sha],
        )
        self.assertEqual(
            [
                commit["sha"]
                for commit in differences["unrepresented_merge_commits"]
            ],
            [resolution_merge_sha],
        )
        self.assertEqual(
            [
                entry["merge_commit"]["sha"]
                for entry in differences["merge_resolution_differences"]
            ],
            [resolution_merge_sha, clean_merge_sha],
        )
        self.assertEqual(
            differences["merge_resolution_differences"][0]["file_differences"],
            [{"status": "M", "path": "shared.txt"}],
        )
        self.assertEqual(
            differences["merge_resolution_differences"][1]["file_differences"],
            [],
        )
        self.assertEqual(
            {clean_feature_tip, resolution_feature_tip}
            & set(git(root, "rev-list", active_tip).splitlines()),
            {clean_feature_tip, resolution_feature_tip},
        )

    def test_active_line_only_file_does_not_hide_archive_merge_resolution(
        self,
    ) -> None:
        root, _ = self.make_repo()
        git(root, "checkout", "-qb", "feature")
        (root / "feature.txt").write_text("promoted feature\n", encoding="utf-8")
        git(root, "add", "feature.txt")
        git(root, "commit", "-qm", "feature work")
        feature_tip = git(root, "rev-parse", "HEAD")

        git(root, "checkout", "-q", "main")
        git(root, "checkout", "-qb", "archived")
        git(root, "merge", "--no-ff", "--no-commit", "feature")
        (root / "resolution.txt").write_text(
            "archive merge resolution\n", encoding="utf-8"
        )
        git(root, "add", "resolution.txt")
        git(root, "commit", "-qm", "merge feature with archive resolution")
        archive_tip = git(root, "rev-parse", "HEAD")

        git(root, "checkout", "-q", "main")
        git(root, "cherry-pick", "-n", feature_tip)
        git(root, "commit", "-qm", "promote feature work")
        (root / "active-only.txt").write_text(
            "active line only\n", encoding="utf-8"
        )
        git(root, "add", "active-only.txt")
        git(root, "commit", "-qm", "add active-line-only file")
        active_tip = git(root, "rev-parse", "HEAD")
        ledger = self.write_ledger(
            root,
            f"| `archived` | **archive** | `{archive_tip}` | reviewed |",
        )

        result = audit_repo.audit_archive_equivalents(root, ledger, "main")

        self.assertEqual(result["active_line_tip_sha"], active_tip)
        self.assertEqual(result["already_promoted"], [])
        self.assertEqual(result["unrepresented_changes"], ["archived"])
        self.assertEqual(result["unverifiable"], [])
        archive = result["archives"][0]
        self.assertEqual(archive["classification"], "unrepresented-changes")
        self.assertCountEqual(
            archive["file_differences"],
            [
                {"status": "D", "path": "active-only.txt"},
                {"status": "A", "path": "resolution.txt"},
            ],
        )

        commit_differences = archive["commit_differences"]
        self.assertEqual(
            commit_differences["unrepresented_patch_commit_count"], 0
        )
        self.assertEqual(
            commit_differences["unrepresented_commit_count"], 1
        )
        self.assertEqual(
            [
                merge["subject"]
                for merge in commit_differences["unrepresented_merge_commits"]
            ],
            ["merge feature with archive resolution"],
        )
        self.assertEqual(
            commit_differences["merge_resolution_differences"][0][
                "file_differences"
            ],
            [{"status": "A", "path": "resolution.txt"}],
        )

    def test_cli_reports_active_only_file_and_archive_merge_resolution_read_only(
        self,
    ) -> None:
        root, _ = self.make_repo()
        git(root, "checkout", "-qb", "feature")
        (root / "feature.txt").write_text("promoted feature\n", encoding="utf-8")
        git(root, "add", "feature.txt")
        git(root, "commit", "-qm", "feature work")
        feature_tip = git(root, "rev-parse", "HEAD")

        git(root, "checkout", "-q", "main")
        git(root, "checkout", "-qb", "archived")
        git(root, "merge", "--no-ff", "--no-commit", "feature")
        (root / "resolution.txt").write_text(
            "archive merge resolution\n", encoding="utf-8"
        )
        git(root, "add", "resolution.txt")
        git(root, "commit", "-qm", "merge feature with archive resolution")
        archive_tip = git(root, "rev-parse", "HEAD")

        git(root, "checkout", "-q", "main")
        git(root, "cherry-pick", "-n", feature_tip)
        git(root, "commit", "-qm", "promote feature work")
        (root / "active-only.txt").write_text(
            "active line only\n", encoding="utf-8"
        )
        git(root, "add", "active-only.txt")
        git(root, "commit", "-qm", "add active-line-only file")
        ledger = self.write_ledger(
            root,
            f"| `archived` | **archive** | `{archive_tip}` | reviewed |",
        )

        def worktree_files() -> dict[str, tuple[bytes, int]]:
            return {
                path.relative_to(root).as_posix(): (
                    path.read_bytes(),
                    path.stat().st_mode,
                )
                for path in root.rglob("*")
                if ".git" not in path.relative_to(root).parts and path.is_file()
            }

        refs_before = git(
            root, "for-each-ref", "--format=%(refname) %(objectname)"
        )
        head_ref_before = git(root, "symbolic-ref", "HEAD")
        head_before = git(root, "rev-parse", "HEAD")
        worktree_status_before = git(
            root, "status", "--porcelain", "--untracked-files=all"
        )
        worktree_files_before = worktree_files()

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
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 1, result.stderr + result.stdout)
        report = json.loads(result.stdout)
        archive_equivalents = report["archive_equivalents"]
        self.assertFalse(archive_equivalents["ok"])
        self.assertEqual(
            archive_equivalents["unrepresented_changes"], ["archived"]
        )
        archive = archive_equivalents["archives"][0]
        self.assertEqual(archive["classification"], "unrepresented-changes")
        self.assertCountEqual(
            archive["file_differences"],
            [
                {"status": "D", "path": "active-only.txt"},
                {"status": "A", "path": "resolution.txt"},
            ],
        )
        commit_differences = archive["commit_differences"]
        self.assertEqual(
            commit_differences["unrepresented_patch_commit_count"], 0
        )
        self.assertEqual(
            [
                merge["subject"]
                for merge in commit_differences["unrepresented_merge_commits"]
            ],
            ["merge feature with archive resolution"],
        )
        self.assertEqual(
            commit_differences["merge_resolution_differences"][0][
                "file_differences"
            ],
            [{"status": "A", "path": "resolution.txt"}],
        )

        self.assertEqual(
            git(root, "for-each-ref", "--format=%(refname) %(objectname)"),
            refs_before,
        )
        self.assertEqual(git(root, "symbolic-ref", "HEAD"), head_ref_before)
        self.assertEqual(git(root, "rev-parse", "HEAD"), head_before)
        self.assertEqual(
            git(root, "status", "--porcelain", "--untracked-files=all"),
            worktree_status_before,
        )
        self.assertEqual(worktree_files(), worktree_files_before)

    def test_active_line_only_file_does_not_hide_archive_merge_deletion(
        self,
    ) -> None:
        root, _ = self.make_repo()
        git(root, "checkout", "-qb", "feature")
        (root / "shared.txt").write_text(
            "feature adds shared path\n", encoding="utf-8"
        )
        git(root, "add", "shared.txt")
        git(root, "commit", "-qm", "feature adds shared path")
        feature_tip = git(root, "rev-parse", "HEAD")

        git(root, "checkout", "-q", "main")
        git(root, "checkout", "-qb", "archived")
        git(root, "merge", "--no-ff", "--no-commit", "feature")
        git(root, "rm", "-q", "-f", "shared.txt")
        git(root, "commit", "-qm", "merge feature while deleting shared path")
        archive_tip = git(root, "rev-parse", "HEAD")

        git(root, "checkout", "-q", "main")
        git(root, "cherry-pick", "-n", feature_tip)
        git(root, "commit", "-qm", "promote feature work")
        (root / "active-only.txt").write_text(
            "active line only\n", encoding="utf-8"
        )
        git(root, "add", "active-only.txt")
        git(root, "commit", "-qm", "add active-line-only file")
        active_tip = git(root, "rev-parse", "HEAD")
        ledger = self.write_ledger(
            root,
            f"| `archived` | **archive** | `{archive_tip}` | reviewed |",
        )

        result = audit_repo.audit_archive_equivalents(root, ledger, "main")

        self.assertEqual(result["active_line_tip_sha"], active_tip)
        self.assertEqual(result["already_promoted"], [])
        self.assertEqual(result["unrepresented_changes"], ["archived"])
        self.assertEqual(result["unverifiable"], [])
        archive = result["archives"][0]
        self.assertEqual(archive["classification"], "unrepresented-changes")
        self.assertCountEqual(
            archive["file_differences"],
            [
                {"status": "D", "path": "active-only.txt"},
                {"status": "D", "path": "shared.txt"},
            ],
        )

        commit_differences = archive["commit_differences"]
        self.assertEqual(
            commit_differences["unrepresented_patch_commit_count"], 0
        )
        self.assertEqual(
            commit_differences["unrepresented_commit_count"], 1
        )
        self.assertEqual(
            [
                merge["subject"]
                for merge in commit_differences["unrepresented_merge_commits"]
            ],
            ["merge feature while deleting shared path"],
        )
        resolution_evidence = commit_differences[
            "merge_resolution_differences"
        ][0]
        self.assertEqual(
            resolution_evidence["file_differences"],
            [{"status": "D", "path": "shared.txt"}],
        )
        self.assertNotIn(
            {"status": "D", "path": "active-only.txt"},
            resolution_evidence["file_differences"],
        )
        self.assertEqual(
            resolution_evidence["archive_tip_differences_since_merge"], []
        )

    @unittest.skipIf(
        sys.platform == "win32",
        "Windows does not support filenames with undecodable byte sequences",
    )
    def test_archive_merge_report_preserves_invalid_utf8_filename(self) -> None:
        root, _ = self.make_repo()
        invalid_path_bytes = b"merge-resolution-\xff-name.txt"
        invalid_path = root / os.fsdecode(invalid_path_bytes)

        git(root, "checkout", "-qb", "feature")
        invalid_path.write_bytes(b"feature version\n")
        git(root, "add", "--all")
        git(root, "commit", "-qm", "feature adds unusual path")
        feature_tip = git(root, "rev-parse", "HEAD")

        git(root, "checkout", "-q", "main")
        git(root, "checkout", "-qb", "archived")
        invalid_path.write_bytes(b"archive version\n")
        git(root, "add", "--all")
        git(root, "commit", "-qm", "archive adds unusual path")
        merge_result = subprocess.run(
            ["git", "merge", "--no-ff", "--no-commit", "--quiet", "feature"],
            cwd=root,
            capture_output=True,
            check=False,
        )
        self.assertEqual(merge_result.returncode, 1, merge_result.stderr)
        invalid_path.write_bytes(b"archive merge resolution\n")
        git(root, "add", "--all")
        git(root, "commit", "-qm", "resolve unusual path merge")
        invalid_path.write_bytes(b"archive post-merge edit\n")
        git(root, "add", "--all")
        git(root, "commit", "-qm", "edit unusual path after merge")
        archive_tip = git(root, "rev-parse", "HEAD")

        git(root, "checkout", "-q", "main")
        git(root, "cherry-pick", "-n", feature_tip)
        git(root, "commit", "-qm", "promote feature unusual path")
        ledger = self.write_ledger(
            root,
            f"| `archived` | **archive** | `{archive_tip}` | reviewed |",
        )
        expected_path = {
            "encoding": "base64",
            "data": base64.b64encode(invalid_path_bytes).decode("ascii"),
        }

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

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        archive = json.loads(result.stdout)["archive_equivalents"]["archives"][0]
        self.assertEqual(
            archive["commit_differences"]["merge_resolution_differences"][0][
                "file_differences"
            ],
            [{"status": "M", "path": expected_path}],
        )
        self.assertEqual(
            archive["commit_differences"]["merge_resolution_differences"][0][
                "archive_tip_differences_since_merge"
            ],
            [{"status": "M", "path": expected_path}],
        )
        self.assertEqual(
            archive["file_differences"],
            [{"status": "M", "path": expected_path}],
        )

    @unittest.skipIf(
        sys.platform == "win32",
        "Windows rejects tab and newline filenames",
    )
    def test_archive_merge_report_preserves_paths_with_tabs_and_newlines(
        self,
    ) -> None:
        root, _ = self.make_repo()
        paths = ["tab\tresolution.txt", "line\nbreak-resolution.txt"]
        for path in paths:
            (root / path).write_text("base\n", encoding="utf-8")
        git(root, "add", "--all")
        git(root, "commit", "-qm", "add paths with control characters")

        git(root, "checkout", "-qb", "feature")
        for path in paths:
            (root / path).write_text("feature version\n", encoding="utf-8")
        git(root, "add", "--all")
        git(root, "commit", "-qm", "change paths on feature")
        feature_tip = git(root, "rev-parse", "HEAD")

        git(root, "checkout", "-q", "main")
        git(root, "checkout", "-qb", "archived")
        for path in paths:
            (root / path).write_text("archive version\n", encoding="utf-8")
        git(root, "add", "--all")
        git(root, "commit", "-qm", "change paths on archive")
        merge_result = subprocess.run(
            ["git", "merge", "--no-ff", "--no-commit", "--quiet", "feature"],
            cwd=root,
            capture_output=True,
            check=False,
        )
        self.assertEqual(merge_result.returncode, 1, merge_result.stderr)

        for path in paths:
            (root / path).write_text("archive merge resolution\n", encoding="utf-8")
        git(root, "add", "--all")
        git(root, "commit", "-qm", "resolve paths with control characters")
        merge_sha = git(root, "rev-parse", "HEAD")
        for path in paths:
            (root / path).write_text("archive edit after merge\n", encoding="utf-8")
        git(root, "add", "--all")
        git(root, "commit", "-qm", "edit paths after merge")
        archive_tip = git(root, "rev-parse", "HEAD")

        git(root, "checkout", "-q", "main")
        git(root, "cherry-pick", "-n", feature_tip)
        git(root, "commit", "-qm", "promote feature changes")
        ledger = self.write_ledger(
            root,
            f"| `archived` | **archive** | `{archive_tip}` | reviewed |",
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

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        archive = report["archive_equivalents"]["archives"][0]
        self.assertEqual(archive["classification"], "unrepresented-changes")
        self.assertEqual(
            archive["commit_differences"]["archive_only_merge_commits"][0]["sha"],
            merge_sha,
        )
        self.assertEqual(
            report["archive_equivalents"]["unverifiable"], []
        )
        merge_evidence = archive["commit_differences"][
            "merge_resolution_differences"
        ][0]
        for field in ("file_differences", "archive_tip_differences_since_merge"):
            with self.subTest(field=field):
                self.assertEqual(
                    {
                        entry["path"]: entry["status"]
                        for entry in merge_evidence[field]
                    },
                    {path: "M" for path in paths},
                )

    def test_promoted_archive_merge_stays_promoted_with_active_line_only_file(
        self,
    ) -> None:
        root, _ = self.make_repo()
        git(root, "checkout", "-qb", "feature")
        (root / "feature.txt").write_text("promoted feature\n", encoding="utf-8")
        git(root, "add", "feature.txt")
        git(root, "commit", "-qm", "feature work")
        feature_tip = git(root, "rev-parse", "HEAD")

        git(root, "checkout", "-q", "main")
        git(root, "checkout", "-qb", "archived")
        git(root, "merge", "--no-ff", "-m", "merge promoted feature", "feature")
        archive_tip = git(root, "rev-parse", "HEAD")

        git(root, "checkout", "-q", "main")
        git(root, "cherry-pick", "-n", feature_tip)
        git(root, "commit", "-qm", "promote feature work")
        (root / "active-only.txt").write_text("active line only\n", encoding="utf-8")
        git(root, "add", "active-only.txt")
        git(root, "commit", "-qm", "add active-line-only file")
        active_tip = git(root, "rev-parse", "HEAD")
        ledger = self.write_ledger(
            root,
            f"| `archived` | **archive** | `{archive_tip}` | reviewed |",
        )

        result = audit_repo.audit_archive_equivalents(root, ledger, "main")

        self.assertEqual(result["active_line_tip_sha"], active_tip)
        self.assertEqual(result["already_promoted"], ["archived"])
        self.assertEqual(result["unrepresented_changes"], [])
        self.assertEqual(result["unverifiable"], [])
        archive = result["archives"][0]
        self.assertEqual(archive["classification"], "already-promoted")
        self.assertFalse(archive["tree_difference"]["same"])
        self.assertEqual(
            archive["file_differences"],
            [{"status": "D", "path": "active-only.txt"}],
        )
        commit_differences = archive["commit_differences"]
        self.assertEqual(commit_differences["unrepresented_commit_count"], 0)
        self.assertEqual(
            commit_differences["unrepresented_patch_commit_count"], 0
        )
        self.assertEqual(commit_differences["promoted_commit_count"], 1)
        self.assertEqual(commit_differences["unrepresented_merge_commits"], [])
        self.assertEqual(
            [
                merge["subject"]
                for merge in commit_differences["archive_only_merge_commits"]
            ],
            ["merge promoted feature"],
        )
        self.assertEqual(
            commit_differences["merge_resolution_differences"][0][
                "file_differences"
            ],
            [],
        )

    def test_unrelated_archive_edit_after_clean_merge_is_not_merge_resolution(self) -> None:
        root, _ = self.make_repo()
        git(root, "checkout", "-qb", "feature")
        (root / "feature.txt").write_text("promoted feature\n", encoding="utf-8")
        git(root, "add", "feature.txt")
        git(root, "commit", "-qm", "feature work")
        feature_tip = git(root, "rev-parse", "HEAD")

        git(root, "checkout", "-q", "main")
        git(root, "checkout", "-qb", "archived")
        git(root, "merge", "--no-ff", "-m", "merge feature cleanly", "feature")
        (root / "archive-only.txt").write_text(
            "unrelated archive edit\n", encoding="utf-8"
        )
        git(root, "add", "archive-only.txt")
        git(root, "commit", "-qm", "unrelated archive edit")
        archive_tip = git(root, "rev-parse", "HEAD")

        git(root, "checkout", "-q", "main")
        git(root, "cherry-pick", "-n", feature_tip)
        git(root, "commit", "-qm", "promote feature work")
        active_tip = git(root, "rev-parse", "HEAD")
        ledger = self.write_ledger(
            root,
            f"| `archived` | **archive** | `{archive_tip}` | reviewed |",
        )

        result = audit_repo.audit_archive_equivalents(root, ledger, "main")

        self.assertEqual(result["active_line_tip_sha"], active_tip)
        archive = result["archives"][0]
        self.assertEqual(archive["classification"], "unrepresented-changes")
        self.assertEqual(
            archive["commit_differences"]["unrepresented_patch_commit_count"], 1
        )
        self.assertEqual(
            archive["commit_differences"]["unrepresented_merge_commits"], []
        )
        merge_evidence = archive["commit_differences"][
            "merge_resolution_differences"
        ]
        self.assertEqual(len(merge_evidence), 1)
        self.assertEqual(
            merge_evidence[0]["merge_commit"]["subject"],
            "merge feature cleanly",
        )
        self.assertEqual(merge_evidence[0]["file_differences"], [])
        self.assertEqual(
            merge_evidence[0]["archive_tip_differences_since_merge"],
            [{"status": "A", "path": "archive-only.txt"}],
        )
        self.assertEqual(
            archive["file_differences"],
            [{"status": "A", "path": "archive-only.txt"}],
        )

    def test_later_edit_to_merge_resolution_path_is_not_merge_resolution_work(
        self,
    ) -> None:
        root, _ = self.make_repo()
        (root / "shared.txt").write_text("base content\n", encoding="utf-8")
        git(root, "add", "shared.txt")
        git(root, "commit", "-qm", "add shared content")
        git(root, "checkout", "-qb", "feature")
        (root / "shared.txt").write_text("feature content\n", encoding="utf-8")
        git(root, "add", "shared.txt")
        git(root, "commit", "-qm", "feature changes shared content")
        feature_tip = git(root, "rev-parse", "HEAD")

        git(root, "checkout", "-q", "main")
        git(root, "checkout", "-qb", "archived")
        (root / "shared.txt").write_text("archive content\n", encoding="utf-8")
        git(root, "add", "shared.txt")
        git(root, "commit", "-qm", "archive changes shared content")
        merge_result = subprocess.run(
            ["git", "merge", "--no-ff", "--no-commit", "feature"],
            cwd=root,
            capture_output=True,
            text=True,
        )
        self.assertEqual(merge_result.returncode, 1, merge_result.stderr)
        (root / "shared.txt").write_text("merge resolution\n", encoding="utf-8")
        git(root, "add", "shared.txt")
        git(root, "commit", "-qm", "resolve shared content merge")
        merge_sha = git(root, "rev-parse", "HEAD")
        (root / "shared.txt").write_text("later archive edit\n", encoding="utf-8")
        git(root, "add", "shared.txt")
        git(root, "commit", "-qm", "edit resolved content later")
        archive_tip = git(root, "rev-parse", "HEAD")

        git(root, "checkout", "-q", "main")
        git(root, "cherry-pick", "-n", feature_tip)
        git(root, "commit", "-qm", "promote feature content")
        active_tip = git(root, "rev-parse", "HEAD")
        ledger = self.write_ledger(
            root,
            f"| `archived` | **archive** | `{archive_tip}` | reviewed |",
        )

        result = audit_repo.audit_archive_equivalents(root, ledger, "main")

        self.assertEqual(result["active_line_tip_sha"], active_tip)
        self.assertEqual(result["unrepresented_changes"], ["archived"])
        self.assertEqual(result["unverifiable"], [])
        archive = result["archives"][0]
        self.assertEqual(archive["classification"], "unrepresented-changes")
        self.assertFalse(archive["tree_difference"]["same"])
        self.assertEqual(
            archive["file_differences"],
            [{"status": "M", "path": "shared.txt"}],
        )

        differences = archive["commit_differences"]
        self.assertEqual(
            [commit["sha"] for commit in differences["archive_only_merge_commits"]],
            [merge_sha],
        )
        self.assertEqual(differences["unrepresented_merge_commits"], [])
        self.assertEqual(differences["unrepresented_patch_commit_count"], 2)
        self.assertEqual(differences["unrepresented_commit_count"], 2)
        self.assertEqual(len(differences["merge_resolution_differences"]), 1)
        merge_evidence = differences["merge_resolution_differences"][0]
        self.assertEqual(
            merge_evidence["file_differences"],
            [{"status": "M", "path": "shared.txt"}],
        )
        self.assertEqual(
            merge_evidence["archive_tip_differences_since_merge"],
            [{"status": "M", "path": "shared.txt"}],
        )

    def test_later_deletion_of_merge_resolution_path_is_reported_separately(
        self,
    ) -> None:
        root, _ = self.make_repo()
        (root / "shared.txt").write_text("base content\n", encoding="utf-8")
        git(root, "add", "shared.txt")
        git(root, "commit", "-qm", "add shared content")

        git(root, "checkout", "-qb", "feature")
        (root / "shared.txt").write_text("feature content\n", encoding="utf-8")
        git(root, "add", "shared.txt")
        git(root, "commit", "-qm", "feature changes shared content")

        git(root, "checkout", "-q", "main")
        (root / "shared.txt").write_text(
            "archive pre-merge content\n", encoding="utf-8"
        )
        git(root, "add", "shared.txt")
        git(root, "commit", "-qm", "archive changes shared content")
        git(root, "checkout", "-qb", "archived")
        merge_result = subprocess.run(
            ["git", "merge", "--no-ff", "--no-commit", "feature"],
            cwd=root,
            capture_output=True,
            text=True,
        )
        self.assertEqual(merge_result.returncode, 1, merge_result.stderr)
        (root / "shared.txt").write_text(
            "archive merge resolution\n", encoding="utf-8"
        )
        git(root, "add", "shared.txt")
        git(root, "commit", "-qm", "resolve shared content merge")
        merge_sha = git(root, "rev-parse", "HEAD")

        (root / "shared.txt").unlink()
        git(root, "add", "-u", "shared.txt")
        git(root, "commit", "-qm", "delete resolved content later")
        deletion_sha = git(root, "rev-parse", "HEAD")
        archive_tip = git(root, "rev-parse", "HEAD")

        git(root, "checkout", "-q", "main")
        merge_result = subprocess.run(
            ["git", "merge", "--no-ff", "--no-commit", "feature"],
            cwd=root,
            capture_output=True,
            text=True,
        )
        self.assertEqual(merge_result.returncode, 1, merge_result.stderr)
        (root / "shared.txt").write_text(
            "active-line merge resolution\n", encoding="utf-8"
        )
        git(root, "add", "shared.txt")
        git(root, "commit", "-qm", "resolve shared content on active line")
        active_tip = git(root, "rev-parse", "HEAD")
        ledger = self.write_ledger(
            root,
            f"| `archived` | **archive** | `{archive_tip}` | reviewed |",
        )

        result = audit_repo.audit_archive_equivalents(root, ledger, "main")

        self.assertEqual(result["active_line_tip_sha"], active_tip)
        self.assertEqual(result["unrepresented_changes"], ["archived"])
        self.assertEqual(result["unverifiable"], [])
        archive = result["archives"][0]
        self.assertEqual(archive["classification"], "unrepresented-changes")
        self.assertFalse(archive["tree_difference"]["same"])
        self.assertEqual(
            archive["file_differences"],
            [{"status": "D", "path": "shared.txt"}],
        )

        differences = archive["commit_differences"]
        self.assertEqual(
            [commit["sha"] for commit in differences["archive_only_merge_commits"]],
            [merge_sha],
        )
        self.assertEqual(differences["unrepresented_merge_commits"], [])
        self.assertEqual(differences["unrepresented_patch_commit_count"], 1)
        self.assertEqual(differences["unrepresented_commit_count"], 1)
        self.assertEqual(
            [commit["sha"] for commit in differences["archive_only_commits"]],
            [deletion_sha, merge_sha],
        )
        self.assertEqual(
            differences["archive_only_commits"][0]["subject"],
            "delete resolved content later",
        )
        self.assertEqual(len(differences["merge_resolution_differences"]), 1)
        merge_evidence = differences["merge_resolution_differences"][0]
        self.assertEqual(
            merge_evidence["file_differences"],
            [{"status": "M", "path": "shared.txt"}],
        )
        self.assertEqual(
            merge_evidence["archive_tip_differences_since_merge"],
            [{"status": "D", "path": "shared.txt"}],
        )

    def test_later_edit_to_one_resolution_path_keeps_other_merge_resolution_work(
        self,
    ) -> None:
        root, _ = self.make_repo()
        for path in ("first.txt", "second.txt"):
            (root / path).write_text(f"base {path}\n", encoding="utf-8")
        git(root, "add", "first.txt", "second.txt")
        git(root, "commit", "-qm", "add shared files")

        git(root, "checkout", "-qb", "feature")
        for path in ("first.txt", "second.txt"):
            (root / path).write_text(f"feature {path}\n", encoding="utf-8")
        git(root, "add", "first.txt", "second.txt")
        git(root, "commit", "-qm", "feature changes both files")
        feature_tip = git(root, "rev-parse", "HEAD")

        git(root, "checkout", "-q", "main")
        git(root, "checkout", "-qb", "archived")
        for path in ("first.txt", "second.txt"):
            (root / path).write_text(f"archive {path}\n", encoding="utf-8")
        git(root, "add", "first.txt", "second.txt")
        git(root, "commit", "-qm", "archive changes both files")
        merge_result = subprocess.run(
            ["git", "merge", "--no-ff", "--no-commit", "feature"],
            cwd=root,
            capture_output=True,
            text=True,
        )
        self.assertEqual(merge_result.returncode, 1, merge_result.stderr)
        for path in ("first.txt", "second.txt"):
            (root / path).write_text(f"merge resolution {path}\n", encoding="utf-8")
        git(root, "add", "first.txt", "second.txt")
        git(root, "commit", "-qm", "resolve both files")
        merge_sha = git(root, "rev-parse", "HEAD")

        (root / "first.txt").write_text(
            "later archive edit first.txt\n", encoding="utf-8"
        )
        git(root, "add", "first.txt")
        git(root, "commit", "-qm", "edit first resolved file later")
        archive_tip = git(root, "rev-parse", "HEAD")

        git(root, "checkout", "-q", "main")
        git(root, "cherry-pick", "-n", feature_tip)
        git(root, "commit", "-qm", "promote feature changes")
        active_tip = git(root, "rev-parse", "HEAD")
        ledger = self.write_ledger(
            root,
            f"| `archived` | **archive** | `{archive_tip}` | reviewed |",
        )

        result = audit_repo.audit_archive_equivalents(root, ledger, "main")

        self.assertEqual(result["active_line_tip_sha"], active_tip)
        self.assertEqual(result["unrepresented_changes"], ["archived"])
        self.assertEqual(result["unverifiable"], [])
        archive = result["archives"][0]
        self.assertEqual(archive["classification"], "unrepresented-changes")
        self.assertEqual(
            archive["file_differences"],
            [
                {"status": "M", "path": "first.txt"},
                {"status": "M", "path": "second.txt"},
            ],
        )

        differences = archive["commit_differences"]
        self.assertEqual(
            [commit["sha"] for commit in differences["archive_only_merge_commits"]],
            [merge_sha],
        )
        self.assertEqual(
            [commit["sha"] for commit in differences["unrepresented_merge_commits"]],
            [merge_sha],
        )
        self.assertEqual(
            {
                commit["subject"]: commit["patch_status"]
                for commit in differences["archive_commits"]
            },
            {
                "archive changes both files": "unrepresented",
                "feature changes both files": "already-promoted",
                "edit first resolved file later": "unrepresented",
            },
        )
        self.assertEqual(differences["unrepresented_patch_commit_count"], 2)
        self.assertEqual(differences["unrepresented_commit_count"], 3)
        self.assertEqual(len(differences["merge_resolution_differences"]), 1)
        merge_evidence = differences["merge_resolution_differences"][0]
        self.assertEqual(
            merge_evidence["file_differences"],
            [
                {"status": "M", "path": "first.txt"},
                {"status": "M", "path": "second.txt"},
            ],
        )
        self.assertEqual(
            merge_evidence["archive_tip_differences_since_merge"],
            [{"status": "M", "path": "first.txt"}],
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

    def test_archive_equivalence_reports_modified_shared_path_read_only(self) -> None:
        root, _ = self.make_repo()
        (root / "shared.txt").write_text("common version\n", encoding="utf-8")
        git(root, "add", "shared.txt")
        git(root, "commit", "-qm", "add shared path")
        git(root, "branch", "modified-archive")

        git(root, "checkout", "-q", "modified-archive")
        (root / "shared.txt").write_text("archive version\n", encoding="utf-8")
        git(root, "commit", "-qam", "modify shared path on archive")
        archive_tip = git(root, "rev-parse", "HEAD")

        git(root, "checkout", "-q", "main")
        (root / "shared.txt").write_text("active version\n", encoding="utf-8")
        git(root, "commit", "-qam", "modify shared path on active line")
        active_tip = git(root, "rev-parse", "HEAD")

        ledger = self.write_ledger(
            root,
            f"| `modified-archive` | **archive** | `{archive_tip}` | reviewed |",
        )
        refs_before = git(root, "for-each-ref", "--format=%(refname) %(objectname)")
        worktree_before = git(root, "status", "--porcelain", "--untracked-files=all")

        result = audit_repo.audit_archive_equivalents(root, ledger, "main")

        archive = result["archives"][0]
        self.assertEqual(result["active_line_tip_sha"], active_tip)
        self.assertEqual(
            archive["file_difference_direction"],
            "active-line-to-archive-tip",
        )
        self.assertEqual(
            archive["file_differences"],
            [{"status": "M", "path": "shared.txt"}],
        )
        self.assertEqual(
            refs_before,
            git(root, "for-each-ref", "--format=%(refname) %(objectname)"),
        )
        self.assertEqual(
            worktree_before,
            git(root, "status", "--porcelain", "--untracked-files=all"),
        )

    def test_nul_evidence_preserves_unusual_paths_through_json(self) -> None:
        paths = ["space name.txt", "tab\tname.txt", "line\nbreak.txt"]
        evidence = "".join(f"M\0{path}\0" for path in paths)
        records = audit_repo._parse_file_differences(evidence)
        self.assertEqual(
            json.loads(json.dumps(records)),
            [{"status": "M", "path": path} for path in paths],
        )

    @unittest.skipIf(sys.platform == "win32", "Windows rejects tab and newline filenames")
    def test_cli_json_preserves_filenames_with_spaces_tabs_and_newlines(
        self,
    ) -> None:
        root, _ = self.make_repo()
        (root / "space name.txt").write_text("before\n", encoding="utf-8")
        (root / "tab\tname.txt").write_text("before\n", encoding="utf-8")
        git(root, "add", "--", "space name.txt", "tab\tname.txt")
        git(root, "commit", "-qm", "add whitespace paths")
        git(root, "branch", "unusual-paths-archive")
        git(root, "checkout", "-q", "unusual-paths-archive")
        (root / "space name.txt").unlink()
        (root / "tab\tname.txt").write_text("after\n", encoding="utf-8")
        (root / "line\nbreak.txt").write_text("added\n", encoding="utf-8")
        git(root, "add", "--all")
        git(root, "commit", "-qm", "add paths with whitespace")
        archive_tip = git(root, "rev-parse", "HEAD")
        git(root, "checkout", "-q", "main")
        ledger = self.write_ledger(
            root,
            (
                f"| `unusual-paths-archive` | **archive** | "
                f"`{archive_tip}` | reviewed |"
            ),
        )
        refs_before = git(
            root, "for-each-ref", "--format=%(refname) %(objectname)"
        )
        worktree_before = git(root, "status", "--porcelain", "--untracked-files=all")

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
            report["archive_equivalents"]["archives"][0]["file_differences"],
            [
                {"status": "A", "path": "line\nbreak.txt"},
                {"status": "D", "path": "space name.txt"},
                {"status": "M", "path": "tab\tname.txt"},
            ],
        )
        self.assertEqual(
            refs_before,
            git(root, "for-each-ref", "--format=%(refname) %(objectname)"),
        )
        self.assertEqual(
            worktree_before,
            git(root, "status", "--porcelain", "--untracked-files=all"),
        )

    @unittest.skipIf(
        sys.platform == "win32",
        "Windows does not support filenames with undecodable byte sequences",
    )
    def test_cli_json_preserves_invalid_utf8_filename_without_mutation(
        self,
    ) -> None:
        root, _ = self.make_repo()
        git(root, "branch", "invalid-utf8-archive")
        git(root, "checkout", "-q", "invalid-utf8-archive")
        invalid_path_bytes = b"invalid-\xff-name.txt"
        (root / os.fsdecode(invalid_path_bytes)).write_bytes(b"archive content\n")
        (root / "readable-café.txt").write_text(
            "ordinary UTF-8 path\n", encoding="utf-8"
        )
        git(root, "add", "--all")
        git(root, "commit", "-qm", "add unusual archive paths")
        archive_tip = git(root, "rev-parse", "HEAD")
        git(root, "checkout", "-q", "main")
        ledger = self.write_ledger(
            root,
            (
                f"| `invalid-utf8-archive` | **archive** | "
                f"`{archive_tip}` | reviewed |"
            ),
        )
        refs_before = git(
            root, "for-each-ref", "--format=%(refname) %(objectname)"
        )
        head_before = git(root, "rev-parse", "HEAD")
        worktree_before = git(root, "status", "--porcelain", "--untracked-files=all")

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

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        differences = report["archive_equivalents"]["archives"][0][
            "file_differences"
        ]
        self.assertEqual(
            differences,
            [
                {
                    "status": "A",
                    "path": {
                        "encoding": "base64",
                        "data": base64.b64encode(invalid_path_bytes).decode("ascii"),
                    },
                },
                {"status": "A", "path": "readable-café.txt"},
            ],
        )
        self.assertEqual(
            refs_before,
            git(root, "for-each-ref", "--format=%(refname) %(objectname)"),
        )
        self.assertEqual(head_before, git(root, "rev-parse", "HEAD"))
        self.assertEqual(
            worktree_before,
            git(root, "status", "--porcelain", "--untracked-files=all"),
        )

    def test_cli_json_preserves_invalid_utf8_commit_text_without_mutation(
        self,
    ) -> None:
        root, _ = self.make_repo()
        git(root, "branch", "invalid-utf8-archive")
        git(root, "checkout", "-q", "invalid-utf8-archive")
        (root / "archive.txt").write_text("baseline\n", encoding="utf-8")
        git(root, "add", "archive.txt")
        git(root, "commit", "-qm", "archive café baseline")
        baseline_tip = git(root, "rev-parse", "HEAD")

        (root / "archive.txt").write_text("changed\n", encoding="utf-8")
        git(root, "add", "archive.txt")
        archive_tree = git(root, "write-tree")
        git(root, "reset", "--hard", "-q", baseline_tip)
        git(root, "checkout", "-q", "main")

        invalid_author = b"Invalid-\xff-Author"
        invalid_subject = b"archive caf\xc3\xa9 invalid-\xfe subject"
        raw_commit = b"\n".join([
            b"tree " + archive_tree.encode("ascii"),
            b"parent " + baseline_tip.encode("ascii"),
            b"author " + invalid_author
            + b" <invalid@example.test> 1700000000 +0000",
            b"committer Test Committer <test@example.test> 1700000000 +0000",
            b"",
            invalid_subject,
            b"",
            b"message body",
            b"",
        ])
        invalid_tip = subprocess.run(
            ["git", "hash-object", "-t", "commit", "-w", "--stdin"],
            cwd=root,
            input=raw_commit,
            capture_output=True,
            check=True,
        ).stdout.decode("ascii").strip()
        git(root, "update-ref", "refs/heads/invalid-utf8-archive", invalid_tip)
        ledger = self.write_ledger(
            root,
            (
                f"| `invalid-utf8-archive` | **archive** | "
                f"`{invalid_tip}` | reviewed |"
            ),
        )
        refs_before = git(root, "for-each-ref", "--format=%(refname) %(objectname)")
        head_before = git(root, "rev-parse", "HEAD")
        worktree_before = git(root, "status", "--porcelain", "--untracked-files=all")

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

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        branch = next(
            item for item in report["branches"]
            if item["branch"] == "invalid-utf8-archive"
        )
        main_branch = next(
            item for item in report["branches"] if item["branch"] == "main"
        )
        self.assertEqual(main_branch["last_commit_author"], "Test User")
        self.assertEqual(main_branch["last_commit_subject"], "initial")
        self.assertEqual(
            branch["last_commit_author"],
            {
                "encoding": "base64",
                "data": base64.b64encode(invalid_author).decode("ascii"),
            },
        )
        self.assertEqual(
            branch["last_commit_subject"],
            {
                "encoding": "base64",
                "data": base64.b64encode(invalid_subject).decode("ascii"),
            },
        )
        archive = report["archive_equivalents"]["archives"][0]
        invalid_subject_value = {
            "encoding": "base64",
            "data": base64.b64encode(invalid_subject).decode("ascii"),
        }
        for commit_list in (
            archive["commit_differences"]["archive_commits"],
            archive["commit_differences"]["archive_only_commits"],
        ):
            commit = next(item for item in commit_list if item["sha"] in (
                invalid_tip,
                invalid_tip[:7],
            ))
            self.assertEqual(commit["subject"], invalid_subject_value)
        self.assertIn(
            "archive café baseline",
            [
                item["subject"]
                for item in archive["commit_differences"]["archive_only_commits"]
            ],
        )
        self.assertEqual(
            refs_before,
            git(root, "for-each-ref", "--format=%(refname) %(objectname)"),
        )
        self.assertEqual(head_before, git(root, "rev-parse", "HEAD"))
        self.assertEqual(
            worktree_before,
            git(root, "status", "--porcelain", "--untracked-files=all"),
        )

    def test_cli_reports_invalid_utf8_branch_names_without_mutation(self) -> None:
        root, initial = self.make_repo()
        invalid_branch_bytes = b"invalid-\xff-ref"
        git(root, "branch", "readable-café")
        subprocess.run(
            [
                os.fsencode("git"),
                b"update-ref",
                b"refs/heads/" + invalid_branch_bytes,
                git(root, "rev-parse", "HEAD").encode("ascii"),
            ],
            cwd=root,
            capture_output=True,
            check=True,
        )
        ledger = self.write_ledger(
            root,
            f"| `main` | **keep** | `{initial}` | active |",
        )
        refs_before = subprocess.run(
            ["git", "for-each-ref", "--format=%(refname) %(objectname)"],
            cwd=root,
            capture_output=True,
            check=True,
        ).stdout
        head_before = git(root, "rev-parse", "HEAD")
        worktree_before = git(
            root, "status", "--porcelain", "--untracked-files=all"
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
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertIn("branches", report, result.stdout + result.stderr)
        encoded_branch = {
            "encoding": "base64",
            "data": base64.b64encode(invalid_branch_bytes).decode("ascii"),
        }
        branch = next(
            item for item in report["branches"]
            if item["branch"] == encoded_branch
        )
        self.assertEqual(branch["tip_sha"], head_before)
        self.assertFalse(branch["is_current"])
        self.assertTrue(branch["merged_into_base"])
        self.assertIn(encoded_branch, report["decision_ledger"]["missing_branches"])
        self.assertTrue(any(
            item["branch"] == "main" for item in report["branches"]
        ))
        self.assertTrue(any(
            item["branch"] == "readable-café"
            for item in report["branches"]
        ))
        self.assertEqual(
            refs_before,
            subprocess.run(
                ["git", "for-each-ref", "--format=%(refname) %(objectname)"],
                cwd=root,
                capture_output=True,
                check=True,
            ).stdout,
        )
        self.assertEqual(head_before, git(root, "rev-parse", "HEAD"))
        self.assertEqual(
            worktree_before,
            git(root, "status", "--porcelain", "--untracked-files=all"),
        )

    @unittest.skipIf(
        sys.platform == "win32",
        "Windows does not support refs with undecodable byte sequences",
    )
    def test_cli_preserves_invalid_utf8_base_ref_merge_status_read_only(
        self,
    ) -> None:
        root, _ = self.make_repo()
        invalid_base_bytes = b"refs/remotes/origin/base-\xff-ref"
        git(root, "checkout", "-qb", "merged-branch")
        (root / "merged.txt").write_text("merged history\n")
        git(root, "add", "merged.txt")
        git(root, "commit", "-qm", "merged change")
        merged_tip = git(root, "rev-parse", "HEAD")
        git(root, "checkout", "-q", "main")
        git(root, "merge", "--no-ff", "-qm", "merge merged branch", "merged-branch")
        base_tip = git(root, "rev-parse", "HEAD")
        subprocess.run(
            [
                b"git",
                b"update-ref",
                invalid_base_bytes,
                base_tip.encode("ascii"),
            ],
            cwd=root,
            capture_output=True,
            check=True,
        )
        git(root, "checkout", "-qb", "unmerged-branch")
        (root / "unmerged.txt").write_text("not in the base history\n")
        git(root, "add", "unmerged.txt")
        git(root, "commit", "-qm", "unmerged change")
        unmerged_tip = git(root, "rev-parse", "HEAD")
        git(root, "checkout", "-q", "main")
        ledger = self.write_ledger(
            root,
            "\n".join([
                (
                    f"| `merged-branch` | **keep** | "
                    f"`{merged_tip}` | reviewed |"
                ),
                (
                    f"| `unmerged-branch` | **keep** | "
                    f"`{unmerged_tip}` | reviewed |"
                ),
            ]),
        )
        refs_before = subprocess.run(
            [
                b"git",
                b"for-each-ref",
                b"--format=%(refname) %(objectname)",
            ],
            cwd=root,
            capture_output=True,
            check=True,
        ).stdout
        head_ref_before = subprocess.run(
            [b"git", b"symbolic-ref", b"HEAD"],
            cwd=root,
            capture_output=True,
            check=True,
        ).stdout
        head_before = git(root, "rev-parse", "HEAD")
        worktree_before = subprocess.run(
            [
                b"git",
                b"status",
                b"--porcelain",
                b"--untracked-files=all",
            ],
            cwd=root,
            capture_output=True,
            check=True,
        ).stdout

        result = subprocess.run(
            [
                os.fsencode(sys.executable),
                os.fsencode(SCRIPT),
                b"--root",
                os.fsencode(root),
                b"--base",
                invalid_base_bytes,
                b"--decision-ledger",
                os.fsencode(ledger),
            ],
            cwd=root,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertIn("branches", report, result.stdout + result.stderr)
        self.assertEqual(
            report["base"],
            {
                "encoding": "base64",
                "data": base64.b64encode(invalid_base_bytes).decode("ascii"),
            },
        )
        for branch_name, expected_merged in (
            ("main", True),
            ("merged-branch", True),
            ("unmerged-branch", False),
        ):
            branch = next(
                item
                for item in report["branches"]
                if item["branch"] == branch_name
            )
            self.assertEqual(branch["merged_into_base"], expected_merged)
        self.assertEqual(
            refs_before,
            subprocess.run(
                [
                    b"git",
                    b"for-each-ref",
                    b"--format=%(refname) %(objectname)",
                ],
                cwd=root,
                capture_output=True,
                check=True,
            ).stdout,
        )
        self.assertEqual(
            head_ref_before,
            subprocess.run(
                [b"git", b"symbolic-ref", b"HEAD"],
                cwd=root,
                capture_output=True,
                check=True,
            ).stdout,
        )
        self.assertEqual(head_before, git(root, "rev-parse", "HEAD"))
        self.assertEqual(
            worktree_before,
            subprocess.run(
                [
                    b"git",
                    b"status",
                    b"--porcelain",
                    b"--untracked-files=all",
                ],
                cwd=root,
                capture_output=True,
                check=True,
            ).stdout,
        )

    def test_cli_preserves_existing_staged_unstaged_and_untracked_edits(self) -> None:
        root, _ = self.make_repo()
        ledger_directory = tempfile.TemporaryDirectory()
        self.addCleanup(ledger_directory.cleanup)
        ledger = Path(ledger_directory.name) / "ledger.md"
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

        (root / ".gitignore").write_text("ignored-local.bin\n", encoding="utf-8")
        git(root, "add", ".gitignore")
        git(root, "commit", "-qm", "ignore local artifact")
        ignored_path = root / "ignored-local.bin"
        ignored_content = b"local artifact\x00must remain unchanged\n"
        ignored_path.write_bytes(ignored_content)
        self.assertEqual(
            subprocess.run(
                ["git", "check-ignore", "--quiet", "ignored-local.bin"],
                cwd=root,
                check=False,
            ).returncode,
            0,
            "the fixture file must be ignored by Git",
        )

        staged_content = b"staged version\n"
        unstaged_content = b"staged, then edited again\n"
        (root / "README.md").write_bytes(staged_content)
        git(root, "add", "README.md")
        (root / "README.md").write_bytes(unstaged_content)
        untracked_content = b"untracked work in progress\n"
        (root / "notes.txt").write_bytes(untracked_content)

        status_args = [
            b"git",
            b"status",
            b"--porcelain",
            b"-z",
            b"--untracked-files=all",
        ]
        status_before = subprocess.run(
            status_args,
            cwd=root,
            capture_output=True,
            check=True,
        ).stdout
        self.assertIn(b"MM README.md", status_before)
        self.assertIn(b"?? notes.txt", status_before)
        index_before = (root / ".git" / "index").read_bytes()
        files_before = {
            path.relative_to(root): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file() and ".git" not in path.relative_to(root).parts
        }

        result = subprocess.run(
            [
                os.fsencode(sys.executable),
                os.fsencode(SCRIPT),
                b"--root",
                os.fsencode(root),
                b"--base",
                b"main",
                b"--decision-ledger",
                os.fsencode(ledger),
            ],
            cwd=root,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("branches", json.loads(result.stdout))
        self.assertTrue(ignored_path.is_file(), "the ignored file was removed")
        self.assertEqual(
            ignored_path.read_bytes(),
            ignored_content,
            "the audit changed the ignored file's bytes",
        )
        self.assertEqual(
            (root / ".git" / "index").read_bytes(),
            index_before,
            "the audit changed the Git index",
        )
        files_after = {
            path.relative_to(root): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file() and ".git" not in path.relative_to(root).parts
        }
        self.assertEqual(files_after, files_before)
        status_after = subprocess.run(
            status_args,
            cwd=root,
            capture_output=True,
            check=True,
        ).stdout
        self.assertEqual(status_after, status_before)
        self.assertEqual((root / ".git" / "index").read_bytes(), index_before)

    def test_cli_preserves_existing_edits_when_audit_reports_an_error(self) -> None:
        root, _ = self.make_repo()
        ledger_directory = tempfile.TemporaryDirectory()
        self.addCleanup(ledger_directory.cleanup)
        ledger = Path(ledger_directory.name) / "ledger.md"
        ledger.write_text(
            "\n".join([
                "## Branch decisions",
                "| Branch | Decision | Tip SHA | Evidence |",
                "|---|---|---|---|",
                "",
                "## Explicit exclusions and holds",
            ]),
            encoding="utf-8",
        )

        (root / "README.md").write_bytes(b"staged version\n")
        git(root, "add", "README.md")
        (root / "README.md").write_bytes(b"staged, then edited again\n")
        (root / "notes.txt").write_bytes(b"untracked work in progress\n")

        status_args = [
            b"git",
            b"status",
            b"--porcelain",
            b"-z",
            b"--untracked-files=all",
        ]
        status_before = subprocess.run(
            status_args,
            cwd=root,
            capture_output=True,
            check=True,
        ).stdout
        self.assertIn(b"MM README.md", status_before)
        self.assertIn(b"?? notes.txt", status_before)
        index_before = (root / ".git" / "index").read_bytes()
        files_before = {
            path.relative_to(root): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file() and ".git" not in path.relative_to(root).parts
        }

        result = subprocess.run(
            [
                os.fsencode(sys.executable),
                os.fsencode(SCRIPT),
                b"--root",
                os.fsencode(root),
                b"--base",
                b"main",
                b"--decision-ledger",
                os.fsencode(ledger),
            ],
            cwd=root,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(
            report["error"],
            f"decision ledger has no branch coverage rows: {ledger}",
        )
        self.assertEqual(
            (root / ".git" / "index").read_bytes(),
            index_before,
            "the failed audit changed the Git index",
        )
        files_after = {
            path.relative_to(root): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file() and ".git" not in path.relative_to(root).parts
        }
        self.assertEqual(files_after, files_before)
        status_after = subprocess.run(
            status_args,
            cwd=root,
            capture_output=True,
            check=True,
        ).stdout
        self.assertEqual(status_after, status_before)

    def test_cli_reports_checked_out_invalid_utf8_branch_as_current_read_only(
        self,
    ) -> None:
        root, initial = self.make_repo()
        invalid_branch_bytes = b"checked-out-\xff-ref"
        checkout = subprocess.run(
            [
                b"git",
                b"checkout",
                b"-qb",
                invalid_branch_bytes,
            ],
            cwd=root,
            capture_output=True,
            check=False,
        )
        self.assertEqual(checkout.returncode, 0, checkout.stderr)
        ledger = self.write_ledger(
            root,
            f"| `main` | **keep** | `{initial}` | active |",
        )
        refs_before = subprocess.run(
            [
                b"git",
                b"for-each-ref",
                b"--format=%(refname) %(objectname)",
            ],
            cwd=root,
            capture_output=True,
            check=True,
        ).stdout
        head_ref_before = subprocess.run(
            [b"git", b"symbolic-ref", b"HEAD"],
            cwd=root,
            capture_output=True,
            check=True,
        ).stdout
        head_before = git(root, "rev-parse", "HEAD")
        worktree_before = subprocess.run(
            [
                b"git",
                b"status",
                b"--porcelain",
                b"--untracked-files=all",
            ],
            cwd=root,
            capture_output=True,
            check=True,
        ).stdout

        result = subprocess.run(
            [
                os.fsencode(sys.executable),
                os.fsencode(SCRIPT),
                b"--root",
                os.fsencode(root),
                b"--base",
                b"main",
                b"--decision-ledger",
                os.fsencode(ledger),
            ],
            cwd=root,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        encoded_branch = {
            "encoding": "base64",
            "data": base64.b64encode(invalid_branch_bytes).decode("ascii"),
        }
        branch = next(
            item for item in report["branches"]
            if item["branch"] == encoded_branch
        )
        self.assertTrue(branch["is_current"])
        self.assertEqual(branch["tip_sha"], initial)
        self.assertFalse(next(
            item for item in report["branches"]
            if item["branch"] == "main"
        )["is_current"])
        self.assertEqual(
            report["archive_equivalents"]["active_line"], encoded_branch
        )
        self.assertEqual(
            report["archive_equivalents"]["active_line_tip_sha"], initial
        )
        self.assertEqual(
            refs_before,
            subprocess.run(
                [
                    b"git",
                    b"for-each-ref",
                    b"--format=%(refname) %(objectname)",
                ],
                cwd=root,
                capture_output=True,
                check=True,
            ).stdout,
        )
        self.assertEqual(
            head_ref_before,
            subprocess.run(
                [b"git", b"symbolic-ref", b"HEAD"],
                cwd=root,
                capture_output=True,
                check=True,
            ).stdout,
        )
        self.assertEqual(head_before, git(root, "rev-parse", "HEAD"))
        self.assertEqual(
            worktree_before,
            subprocess.run(
                [
                    b"git",
                    b"status",
                    b"--porcelain",
                    b"--untracked-files=all",
                ],
                cwd=root,
                capture_output=True,
                check=True,
            ).stdout,
        )

    def test_cli_archive_statuses_use_selected_active_line_not_checkout(self) -> None:
        root, _ = self.make_repo()

        git(root, "branch", "archive")
        git(root, "checkout", "-q", "archive")
        (root / "archive-only.txt").write_text("archive\n", encoding="utf-8")
        git(root, "add", "archive-only.txt")
        git(root, "commit", "-qm", "archive-only path")
        archive_tip = git(root, "rev-parse", "HEAD")

        git(root, "checkout", "-q", "main")
        (root / "active-only.txt").write_text("active\n", encoding="utf-8")
        git(root, "add", "active-only.txt")
        git(root, "commit", "-qm", "active-only path")
        historical_active_tip = git(root, "rev-parse", "HEAD")

        (root / "newer-main-tip-only.txt").write_text(
            "only on the later main tip\n", encoding="utf-8"
        )
        git(root, "add", "newer-main-tip-only.txt")
        git(root, "commit", "-qm", "advance main beyond reviewed commit")
        active_tip = git(root, "rev-parse", "HEAD")

        git(root, "branch", "checkout-only-line")
        git(root, "checkout", "-q", "checkout-only-line")
        (root / "checkout-only.txt").write_text("checkout\n", encoding="utf-8")
        git(root, "add", "checkout-only.txt")
        git(root, "commit", "-qm", "checkout-only path")

        ledger = self.write_ledger(
            root,
            f"| `archive` | **archive** | `{archive_tip}` | reviewed |",
        )
        refs_before = git(
            root, "for-each-ref", "--format=%(refname) %(objectname)"
        )
        self.assertEqual(git(root, "branch", "--show-current"), "checkout-only-line")
        checkout_tip = git(root, "rev-parse", "HEAD")
        self.assertNotEqual(checkout_tip, active_tip)
        self.assertEqual(git(root, "rev-parse", "main"), active_tip)
        self.assertNotEqual(historical_active_tip, active_tip)
        worktree_before = git(
            root, "status", "--porcelain", "--untracked-files=all"
        )

        comparisons = (
            (
                "main",
                active_tip,
                [
                    ("D", "active-only.txt"),
                    ("D", "newer-main-tip-only.txt"),
                    ("A", "archive-only.txt"),
                ],
            ),
            (
                historical_active_tip,
                historical_active_tip,
                [("D", "active-only.txt"), ("A", "archive-only.txt")],
            ),
        )
        for active_line, expected_active_tip, expected_differences in comparisons:
            with self.subTest(active_line=active_line):
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
                        "--active-line",
                        active_line,
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )

                report = json.loads(result.stdout)
                archive = report["archive_equivalents"]["archives"][0]
                self.assertEqual(
                    report["archive_equivalents"]["active_line"], active_line
                )
                self.assertEqual(
                    report["archive_equivalents"]["active_line_tip_sha"],
                    expected_active_tip,
                )
                self.assertEqual(archive["tip_sha"], archive_tip)
                self.assertEqual(archive["branch_tip_sha"], archive_tip)
                self.assertEqual(
                    archive["file_difference_direction"],
                    "active-line-to-archive-tip",
                )
                self.assertCountEqual(
                    [
                        (difference["status"], difference["path"])
                        for difference in archive["file_differences"]
                    ],
                    expected_differences,
                )
                self.assertEqual(
                    refs_before,
                    git(root, "for-each-ref", "--format=%(refname) %(objectname)"),
                )
                self.assertEqual(
                    git(root, "branch", "--show-current"), "checkout-only-line"
                )
                self.assertEqual(git(root, "rev-parse", "HEAD"), checkout_tip)
                self.assertEqual(
                    worktree_before,
                    git(root, "status", "--porcelain", "--untracked-files=all"),
                )

    def test_cli_reviews_active_commit_sha_without_a_branch_ref_read_only(self) -> None:
        root, _ = self.make_repo()

        git(root, "branch", "archive")
        git(root, "checkout", "-q", "archive")
        (root / "archive-only.txt").write_text("archive\n", encoding="utf-8")
        git(root, "add", "archive-only.txt")
        git(root, "commit", "-qm", "archive-only path")
        archive_tip = git(root, "rev-parse", "HEAD")

        git(root, "checkout", "-q", "main")
        git(root, "checkout", "-qb", "active-reference")
        (root / "active-only.txt").write_text("active\n", encoding="utf-8")
        git(root, "add", "active-only.txt")
        git(root, "commit", "-qm", "active-only path")
        active_tip = git(root, "rev-parse", "HEAD")

        git(root, "checkout", "-q", "main")
        git(root, "branch", "-D", "active-reference")
        self.assertEqual(
            git(root, "cat-file", "-e", f"{active_tip}^{{commit}}"), ""
        )
        self.assertEqual(
            git(root, "for-each-ref", "--contains", active_tip, "--format=%(refname)"),
            "",
        )

        ledger = self.write_ledger(
            root,
            f"| `archive` | **archive** | `{archive_tip}` | reviewed |",
        )
        refs_before = git(
            root, "for-each-ref", "--format=%(refname) %(objectname)"
        )
        checkout_tip = git(root, "rev-parse", "HEAD")
        worktree_before = git(
            root, "status", "--porcelain", "--untracked-files=all"
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
                "--active-line",
                active_tip,
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        archive_report = report["archive_equivalents"]["archives"][0]
        self.assertEqual(report["archive_equivalents"]["active_line"], active_tip)
        self.assertEqual(
            report["archive_equivalents"]["active_line_tip_sha"], active_tip
        )
        self.assertEqual(archive_report["tip_sha"], archive_tip)
        self.assertEqual(archive_report["branch_tip_sha"], archive_tip)
        self.assertEqual(
            archive_report["classification"], "unrepresented-changes"
        )
        self.assertEqual(
            archive_report["file_difference_direction"],
            "active-line-to-archive-tip",
        )
        self.assertCountEqual(
            [
                (difference["status"], difference["path"])
                for difference in archive_report["file_differences"]
            ],
            [("D", "active-only.txt"), ("A", "archive-only.txt")],
        )
        self.assertEqual(
            refs_before,
            git(root, "for-each-ref", "--format=%(refname) %(objectname)"),
        )
        self.assertEqual(git(root, "branch", "--show-current"), "main")
        self.assertEqual(git(root, "rev-parse", "HEAD"), checkout_tip)
        self.assertEqual(
            worktree_before,
            git(root, "status", "--porcelain", "--untracked-files=all"),
        )

    def test_cli_reports_differences_between_unrelated_root_histories_read_only(
        self,
    ) -> None:
        root, _ = self.make_repo()
        git(root, "checkout", "--orphan", "archive-root")
        git(root, "rm", "-q", "-rf", ".")
        (root / "archive-only.txt").write_text("archive\n", encoding="utf-8")
        (root / "shared.txt").write_text("archive version\n", encoding="utf-8")
        git(root, "add", "archive-only.txt", "shared.txt")
        git(root, "commit", "-qm", "archive root content")
        archive_tip = git(root, "rev-parse", "HEAD")
        git(root, "branch", "archive")

        git(root, "checkout", "--orphan", "active-root")
        git(root, "rm", "-q", "-rf", ".")
        (root / "active-only.txt").write_text("active\n", encoding="utf-8")
        (root / "shared.txt").write_text("active version\n", encoding="utf-8")
        git(root, "add", "active-only.txt", "shared.txt")
        git(root, "commit", "-qm", "independent active root")
        active_tip = git(root, "rev-parse", "HEAD")
        git(root, "checkout", "-q", "main")

        merge_base = subprocess.run(
            ["git", "merge-base", archive_tip, active_tip],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(merge_base.returncode, 1, merge_base.stdout + merge_base.stderr)
        self.assertEqual(merge_base.stdout, "")

        ledger = self.write_ledger(
            root,
            f"| `archive` | **archive** | `{archive_tip}` | reviewed |",
        )
        refs_before = git(
            root, "for-each-ref", "--format=%(refname) %(objectname)"
        )
        checkout_tip = git(root, "rev-parse", "HEAD")
        worktree_before = git(
            root, "status", "--porcelain", "--untracked-files=all"
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
                "--active-line",
                active_tip,
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        archives = report["archive_equivalents"]
        archive = archives["archives"][0]
        self.assertEqual(archives["active_line"], active_tip)
        self.assertEqual(archives["active_line_tip_sha"], active_tip)
        self.assertEqual(archive["tip_sha"], archive_tip)
        self.assertEqual(archive["branch_tip_sha"], archive_tip)
        self.assertEqual(archive["classification"], "unrepresented-changes")
        self.assertEqual(
            archive["file_difference_direction"], "active-line-to-archive-tip"
        )
        self.assertCountEqual(
            [
                (difference["status"], difference["path"])
                for difference in archive["file_differences"]
            ],
            [
                ("D", "active-only.txt"),
                ("A", "archive-only.txt"),
                ("M", "shared.txt"),
            ],
        )
        self.assertEqual(
            refs_before,
            git(root, "for-each-ref", "--format=%(refname) %(objectname)"),
        )
        self.assertEqual(git(root, "branch", "--show-current"), "main")
        self.assertEqual(git(root, "rev-parse", "HEAD"), checkout_tip)
        self.assertEqual(
            worktree_before,
            git(root, "status", "--porcelain", "--untracked-files=all"),
        )

    def test_cli_reviews_archived_commit_sha_without_archive_branch_ref_read_only(
        self,
    ) -> None:
        root, _ = self.make_repo()

        git(root, "checkout", "-qb", "archive")
        (root / "archive-only.txt").write_text("archive\n", encoding="utf-8")
        git(root, "add", "archive-only.txt")
        git(root, "commit", "-qm", "archive-only path")
        archive_tip = git(root, "rev-parse", "HEAD")

        git(root, "checkout", "-q", "main")
        git(root, "branch", "-D", "archive")
        self.assertEqual(
            git(root, "cat-file", "-e", f"{archive_tip}^{{commit}}"), ""
        )
        self.assertEqual(
            git(
                root,
                "for-each-ref",
                "--contains",
                archive_tip,
                "--format=%(refname)",
            ),
            "",
        )

        (root / "active-only.txt").write_text("active\n", encoding="utf-8")
        git(root, "add", "active-only.txt")
        git(root, "commit", "-qm", "active-only path")
        active_tip = git(root, "rev-parse", "HEAD")

        ledger = self.write_ledger(
            root,
            f"| `archive` | **archive** | `{archive_tip}` | reviewed |",
        )
        refs_before = git(
            root, "for-each-ref", "--format=%(refname) %(objectname)"
        )
        checkout_tip = git(root, "rev-parse", "HEAD")
        worktree_before = git(
            root, "status", "--porcelain", "--untracked-files=all"
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
                "--active-line",
                active_tip,
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        archive_report = report["archive_equivalents"]["archives"][0]
        self.assertEqual(
            report["archive_equivalents"]["active_line_tip_sha"], active_tip
        )
        self.assertEqual(archive_report["tip_sha"], archive_tip)
        self.assertIsNone(archive_report["branch_tip_sha"])
        self.assertEqual(
            archive_report["classification"], "unrepresented-changes"
        )
        self.assertEqual(
            archive_report["file_difference_direction"],
            "active-line-to-archive-tip",
        )
        self.assertCountEqual(
            [
                (difference["status"], difference["path"])
                for difference in archive_report["file_differences"]
            ],
            [("D", "active-only.txt"), ("A", "archive-only.txt")],
        )
        self.assertEqual(
            refs_before,
            git(root, "for-each-ref", "--format=%(refname) %(objectname)"),
        )
        self.assertEqual(git(root, "branch", "--show-current"), "main")
        self.assertEqual(git(root, "rev-parse", "HEAD"), checkout_tip)
        self.assertEqual(
            worktree_before,
            git(root, "status", "--porcelain", "--untracked-files=all"),
        )

    def test_cli_reviews_saved_archive_sha_when_branch_name_is_reused_read_only(
        self,
    ) -> None:
        root, _ = self.make_repo()

        git(root, "checkout", "-qb", "archive")
        (root / "saved-archive-only.txt").write_text(
            "saved archive commit\n", encoding="utf-8"
        )
        git(root, "add", "saved-archive-only.txt")
        git(root, "commit", "-qm", "saved archive commit")
        saved_archive_tip = git(root, "rev-parse", "HEAD")

        git(root, "checkout", "-q", "main")
        git(root, "branch", "-D", "archive")
        (root / "active-only.txt").write_text("active line\n", encoding="utf-8")
        git(root, "add", "active-only.txt")
        git(root, "commit", "-qm", "active-only path")
        active_tip = git(root, "rev-parse", "HEAD")
        git(root, "branch", "archive", active_tip)
        recreated_branch_tip = git(root, "rev-parse", "archive")
        self.assertNotEqual(saved_archive_tip, recreated_branch_tip)
        self.assertEqual(active_tip, recreated_branch_tip)

        ledger = self.write_ledger(
            root,
            f"| `archive` | **archive** | `{saved_archive_tip}` | reviewed |",
        )
        refs_before = git(
            root, "for-each-ref", "--format=%(refname) %(objectname)"
        )
        checkout_branch = git(root, "branch", "--show-current")
        checkout_tip = git(root, "rev-parse", "HEAD")
        worktree_before = git(
            root, "status", "--porcelain", "--untracked-files=all"
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
                "--active-line",
                active_tip,
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        archive_report = report["archive_equivalents"]["archives"][0]
        self.assertEqual(archive_report["tip_sha"], saved_archive_tip)
        self.assertEqual(archive_report["branch_tip_sha"], recreated_branch_tip)
        self.assertFalse(archive_report["tip_sha_matches_branch"])
        self.assertEqual(
            archive_report["classification"], "unrepresented-changes"
        )
        self.assertEqual(
            archive_report["file_difference_direction"],
            "active-line-to-archive-tip",
        )
        self.assertCountEqual(
            [
                (difference["status"], difference["path"])
                for difference in archive_report["file_differences"]
            ],
            [("D", "active-only.txt"), ("A", "saved-archive-only.txt")],
        )
        self.assertEqual(
            refs_before,
            git(root, "for-each-ref", "--format=%(refname) %(objectname)"),
        )
        self.assertEqual(git(root, "branch", "--show-current"), checkout_branch)
        self.assertEqual(git(root, "rev-parse", "HEAD"), checkout_tip)
        self.assertEqual(
            worktree_before,
            git(root, "status", "--porcelain", "--untracked-files=all"),
        )

    def test_recreated_archive_branch_does_not_hide_missing_saved_commit(
        self,
    ) -> None:
        root, _ = self.make_repo()

        git(root, "checkout", "-qb", "archive")
        (root / "saved-archive-only.txt").write_text(
            "saved archive commit\n", encoding="utf-8"
        )
        git(root, "add", "saved-archive-only.txt")
        git(root, "commit", "-qm", "saved archive commit")
        saved_archive_tip = git(root, "rev-parse", "HEAD")

        git(root, "checkout", "-q", "main")
        git(root, "branch", "-D", "archive")
        (root / "active-only.txt").write_text("active line\n", encoding="utf-8")
        git(root, "add", "active-only.txt")
        git(root, "commit", "-qm", "active-only path")
        active_tip = git(root, "rev-parse", "HEAD")
        git(root, "branch", "archive", active_tip)
        recreated_branch_tip = git(root, "rev-parse", "refs/heads/archive")
        self.assertNotEqual(saved_archive_tip, recreated_branch_tip)
        self.assertEqual(active_tip, recreated_branch_tip)

        # Remove unreachable objects as well as reflog retention so this tests
        # a genuinely missing saved commit, not merely a deleted branch ref.
        git(root, "reflog", "expire", "--expire=now", "--all")
        git(root, "gc", "--prune=now", "--quiet")
        self.assertNotEqual(
            subprocess.run(
                ["git", "cat-file", "-e", f"{saved_archive_tip}^{{commit}}"],
                cwd=root,
                capture_output=True,
                check=False,
            ).returncode,
            0,
        )
        self.assertEqual(
            git(root, "cat-file", "-e", f"{recreated_branch_tip}^{{commit}}"),
            "",
        )

        ledger = self.write_ledger(
            root,
            f"| `archive` | **archive** | `{saved_archive_tip}` | reviewed |",
        )
        refs_before = git(
            root, "for-each-ref", "--format=%(refname) %(objectname)"
        )
        checkout_branch = git(root, "branch", "--show-current")
        checkout_tip = git(root, "rev-parse", "HEAD")
        worktree_before = git(
            root, "status", "--porcelain", "--untracked-files=all"
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
                "--active-line",
                active_tip,
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        archive_equivalents = report["archive_equivalents"]
        archive_report = archive_equivalents["archives"][0]
        self.assertEqual(archive_equivalents["unverifiable"], ["archive"])
        self.assertFalse(archive_equivalents["ok"])
        self.assertEqual(archive_report["tip_sha"], saved_archive_tip)
        self.assertEqual(archive_report["branch_tip_sha"], recreated_branch_tip)
        self.assertFalse(archive_report["tip_sha_matches_branch"])
        self.assertEqual(archive_report["classification"], "unverifiable")
        self.assertIn(
            "archive tip SHA is unavailable as a local commit object "
            "and cannot be reviewed",
            archive_report["error"],
        )
        self.assertIn(saved_archive_tip, archive_report["error"])
        self.assertEqual(archive_report.get("file_differences", []), [])
        self.assertNotIn("saved-archive-only.txt", str(archive_report))
        self.assertNotIn("active-only.txt", str(archive_report))
        self.assertEqual(
            refs_before,
            git(root, "for-each-ref", "--format=%(refname) %(objectname)"),
        )
        self.assertEqual(git(root, "branch", "--show-current"), checkout_branch)
        self.assertEqual(git(root, "rev-parse", "HEAD"), checkout_tip)
        self.assertEqual(
            worktree_before,
            git(root, "status", "--porcelain", "--untracked-files=all"),
        )

    def test_cli_uses_branch_ref_when_tag_shares_archive_branch_name_read_only(
        self,
    ) -> None:
        root, initial_tip = self.make_repo()

        git(root, "checkout", "-qb", "archive")
        (root / "archive-only.txt").write_text("archive\n", encoding="utf-8")
        git(root, "add", "archive-only.txt")
        git(root, "commit", "-qm", "archive-only path")
        archive_tip = git(root, "rev-parse", "HEAD")
        git(root, "checkout", "-q", "main")
        git(root, "tag", "archive", initial_tip)

        self.assertEqual(git(root, "rev-parse", "archive"), initial_tip)
        self.assertEqual(git(root, "rev-parse", "refs/heads/archive"), archive_tip)
        self.assertEqual(git(root, "rev-parse", "refs/tags/archive"), initial_tip)

        ledger = self.write_ledger(
            root,
            f"| `archive` | **archive** | `{archive_tip}` | reviewed |",
        )
        refs_before = git(
            root, "for-each-ref", "--format=%(refname) %(objectname)"
        )
        checkout_branch = git(root, "branch", "--show-current")
        checkout_tip = git(root, "rev-parse", "HEAD")
        worktree_before = git(
            root, "status", "--porcelain", "--untracked-files=all"
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

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        archive_report = report["archive_equivalents"]["archives"][0]
        self.assertEqual(archive_report["tip_sha"], archive_tip)
        self.assertEqual(archive_report["branch_tip_sha"], archive_tip)
        self.assertTrue(archive_report["tip_sha_matches_branch"])
        branch_report = next(
            item for item in report["branches"] if item["branch"] == "archive"
        )
        self.assertEqual(branch_report["tip_sha"], archive_tip)
        self.assertEqual(branch_report["last_commit_subject"], "archive-only path")
        self.assertEqual(report["decision_ledger"]["tip_sha_drift"], [])
        self.assertEqual(
            git(root, "for-each-ref", "--format=%(refname) %(objectname)"),
            refs_before,
        )
        self.assertEqual(git(root, "branch", "--show-current"), checkout_branch)
        self.assertEqual(git(root, "rev-parse", "HEAD"), checkout_tip)
        self.assertEqual(
            git(root, "status", "--porcelain", "--untracked-files=all"),
            worktree_before,
        )

    def test_cli_reports_missing_archived_commit_object_as_unverifiable_read_only(
        self,
    ) -> None:
        root, active_tip = self.make_repo()
        archive_tip = "f" * len(active_tip)
        self.assertNotEqual(
            subprocess.run(
                ["git", "cat-file", "-e", f"{archive_tip}^{{commit}}"],
                cwd=root,
                capture_output=True,
                check=False,
            ).returncode,
            0,
        )
        ledger = self.write_ledger(
            root,
            f"| `archive` | **archive** | `{archive_tip}` | reviewed |",
        )
        refs_before = git(
            root, "for-each-ref", "--format=%(refname) %(objectname)"
        )
        checkout_tip = git(root, "rev-parse", "HEAD")
        checkout_branch = git(root, "branch", "--show-current")
        worktree_before = git(
            root, "status", "--porcelain", "--untracked-files=all"
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
                "--active-line",
                active_tip,
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        archive_equivalents = report["archive_equivalents"]
        archive_report = archive_equivalents["archives"][0]
        self.assertEqual(archive_equivalents["unverifiable"], ["archive"])
        self.assertFalse(archive_equivalents["ok"])
        self.assertEqual(archive_report["tip_sha"], archive_tip)
        self.assertIsNone(archive_report["branch_tip_sha"])
        self.assertEqual(archive_report["classification"], "unverifiable")
        self.assertIn(
            "archive tip SHA is unavailable as a local commit object "
            "and cannot be reviewed",
            archive_report["error"],
        )
        self.assertIn(archive_tip, archive_report["error"])
        self.assertEqual(archive_report.get("file_differences", []), [])
        self.assertEqual(
            refs_before,
            git(root, "for-each-ref", "--format=%(refname) %(objectname)"),
        )
        self.assertEqual(git(root, "branch", "--show-current"), checkout_branch)
        self.assertEqual(git(root, "rev-parse", "HEAD"), checkout_tip)
        self.assertEqual(
            worktree_before,
            git(root, "status", "--porcelain", "--untracked-files=all"),
        )

    def test_cli_missing_active_line_is_unverifiable_without_checkout_differences(
        self,
    ) -> None:
        root, _ = self.make_repo()

        git(root, "branch", "archive")
        git(root, "checkout", "-q", "archive")
        (root / "archive-only.txt").write_text("archive\n", encoding="utf-8")
        git(root, "add", "archive-only.txt")
        git(root, "commit", "-qm", "archive-only path")
        archive_tip = git(root, "rev-parse", "HEAD")

        git(root, "checkout", "-q", "main")
        (root / "active-only.txt").write_text("active\n", encoding="utf-8")
        git(root, "add", "active-only.txt")
        git(root, "commit", "-qm", "active-only path")
        git(root, "checkout", "-qb", "checkout-only-line")
        (root / "checkout-only.txt").write_text("checkout\n", encoding="utf-8")
        git(root, "add", "checkout-only.txt")
        git(root, "commit", "-qm", "checkout-only path")

        missing_active_line = "stale-selected-line"
        ledger = self.write_ledger(
            root,
            f"| `archive` | **archive** | `{archive_tip}` | reviewed |",
        )
        refs_before = git(
            root, "for-each-ref", "--format=%(refname) %(objectname)"
        )
        checkout_tip = git(root, "rev-parse", "HEAD")
        worktree_before = git(
            root, "status", "--porcelain", "--untracked-files=all"
        )
        self.assertEqual(
            git(root, "branch", "--show-current"), "checkout-only-line"
        )
        self.assertNotEqual(checkout_tip, archive_tip)

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
                "--active-line",
                missing_active_line,
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        archive_report = report["archive_equivalents"]["archives"][0]
        self.assertEqual(
            report["archive_equivalents"]["active_line"], missing_active_line
        )
        self.assertIsNone(report["archive_equivalents"]["active_line_tip_sha"])
        self.assertEqual(archive_report["tip_sha"], archive_tip)
        self.assertEqual(archive_report["branch_tip_sha"], archive_tip)
        self.assertEqual(archive_report["classification"], "unverifiable")
        self.assertIn(
            "active line does not resolve to a commit", archive_report["error"]
        )
        self.assertEqual(archive_report.get("file_differences", []), [])
        self.assertEqual(
            refs_before,
            git(root, "for-each-ref", "--format=%(refname) %(objectname)"),
        )
        self.assertEqual(
            git(root, "branch", "--show-current"), "checkout-only-line"
        )
        self.assertEqual(git(root, "rev-parse", "HEAD"), checkout_tip)
        self.assertEqual(
            worktree_before,
            git(root, "status", "--porcelain", "--untracked-files=all"),
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
        original_git_result = audit_repo._git_bytes_result

        def fail_cherry(repo: Path, *args: str):
            if args[:2] == ("cherry", "-v"):
                return subprocess.CompletedProcess(
                    ["git", *args], 128, stdout=b"", stderr=b"comparison failed"
                )
            return original_git_result(repo, *args)

        ledger = self.write_ledger(
            root,
            f"| `archive` | **archive** | `{git(root, 'rev-parse', 'archive')}` | reviewed |",
        )

        with patch.object(
            audit_repo, "_git_bytes_result", side_effect=fail_cherry
        ):
            result = audit_repo.audit_archive_equivalents(root, ledger, "main")

        self.assertFalse(result["ok"])
        self.assertEqual(result["unverifiable"], ["archive"])
        self.assertEqual(result["archives"][0]["classification"], "unverifiable")
        self.assertEqual(
            result["archives"][0]["error"],
            "Git comparison failed: git cherry",
        )

    def test_archive_git_startup_failure_retains_missing_executable_diagnostic(
        self,
    ) -> None:
        root, _ = self.make_repo()
        with patch.object(
            audit_repo.subprocess,
            "run",
            side_effect=FileNotFoundError("git executable unavailable"),
        ):
            result = audit_repo._archive_git_result(
                root, "cherry", "-v", binary=True
            )

        self.assertEqual(result.returncode, 127)
        self.assertEqual(
            result.startup_diagnostic,
            "FileNotFoundError: git executable unavailable",
        )
        self.assertEqual(
            result.stderr,
            b"FileNotFoundError: git executable unavailable",
        )

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

    def test_merge_base_evidence_matches_advertised_remote_head(self) -> None:
        root = self.make_repo()
        base_sha = git(root, "rev-parse", "main")
        git(root, "update-ref", "refs/remotes/origin/main", base_sha)

        evidence = audit_repo.audit_merge_base_evidence(
            root,
            "origin/main",
            {
                "status": "ok",
                "remote_refs": [{
                    "remote": "origin",
                    "ref": "refs/heads/main",
                    "sha": base_sha,
                }],
            },
        )

        self.assertTrue(evidence["ok"])
        self.assertEqual(evidence["status"], "current")
        self.assertFalse(evidence["stale"])
        self.assertEqual(evidence["selected_base_sha"], base_sha)
        self.assertEqual(evidence["remote_sha"], base_sha)

    def test_merge_base_evidence_marks_different_advertised_head_stale(self) -> None:
        root = self.make_repo()
        base_sha = git(root, "rev-parse", "main")
        git(root, "update-ref", "refs/remotes/origin/main", base_sha)

        evidence = audit_repo.audit_merge_base_evidence(
            root,
            "origin/main",
            {
                "status": "ok",
                "remote_refs": [{
                    "remote": "origin",
                    "ref": "refs/heads/main",
                    "sha": "f" * 40,
                }],
            },
        )

        self.assertFalse(evidence["ok"])
        self.assertEqual(evidence["status"], "stale")
        self.assertTrue(evidence["stale"])
        self.assertEqual(
            evidence["classification"],
            "base-differs-from-advertised-remote-head",
        )
        self.assertEqual(evidence["selected_base_sha"], base_sha)

    def test_merge_base_evidence_reports_missing_selected_base(self) -> None:
        root = self.make_repo()

        evidence = audit_repo.audit_merge_base_evidence(
            root, "missing-base", {"status": "ok", "remote_refs": []}
        )

        self.assertFalse(evidence["ok"])
        self.assertEqual(evidence["status"], "unavailable")
        self.assertEqual(
            evidence["classification"], "selected-base-unavailable"
        )
        self.assertIsNone(evidence["stale"])

    def test_merge_base_evidence_reports_missing_advertised_head(self) -> None:
        root = self.make_repo()
        base_sha = git(root, "rev-parse", "main")
        git(root, "update-ref", "refs/remotes/origin/main", base_sha)

        evidence = audit_repo.audit_merge_base_evidence(
            root,
            "origin/main",
            {"status": "ok", "remotes": ["origin"], "remote_refs": []},
        )

        self.assertFalse(evidence["ok"])
        self.assertEqual(evidence["status"], "unavailable")
        self.assertEqual(
            evidence["classification"], "advertised-remote-head-missing"
        )
        self.assertIsNone(evidence["stale"])

    def test_cli_reports_local_non_commit_archive_sha_without_mutating_repository(
        self,
    ) -> None:
        root = self.make_repo()
        git(root, "remote", "remove", "origin")
        git(root, "checkout", "-qb", "archive")
        (root / "archive.txt").write_text("archive evidence\n", encoding="utf-8")
        git(root, "add", "archive.txt")
        git(root, "commit", "-qm", "archive evidence")
        archive_tree = git(root, "rev-parse", "HEAD^{tree}")
        self.assertEqual(git(root, "cat-file", "-t", archive_tree), "tree")
        git(root, "checkout", "-q", "main")

        ledger = root / "ledger.md"
        ledger.write_text(
            "\n".join([
                "## Branch decisions",
                "| Branch | Decision | Tip SHA | Evidence |",
                "|---|---|---|---|",
                f"| `archive` | **archive** | `{archive_tree}` | reviewed |",
                "",
                "## Explicit exclusions and holds",
                "- `main` — current branch",
            ]),
            encoding="utf-8",
        )
        refs_before = git(
            root, "for-each-ref", "--format=%(refname) %(objectname)"
        )
        head_ref_before = git(root, "symbolic-ref", "HEAD")
        head_before = git(root, "rev-parse", "HEAD")
        worktree_before = git(
            root, "status", "--porcelain", "--untracked-files=all"
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
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 1, result.stderr + result.stdout)
        report = json.loads(result.stdout)
        archive_equivalents = report["archive_equivalents"]
        self.assertEqual(archive_equivalents["unverifiable"], ["archive"])
        self.assertFalse(archive_equivalents["ok"])
        archive_report = archive_equivalents["archives"][0]
        self.assertEqual(archive_report["classification"], "unverifiable")
        self.assertIn("local tree object, not a commit", archive_report["error"])
        self.assertIn(archive_tree, archive_report["error"])
        self.assertEqual(
            git(root, "for-each-ref", "--format=%(refname) %(objectname)"),
            refs_before,
        )
        self.assertEqual(git(root, "symbolic-ref", "HEAD"), head_ref_before)
        self.assertEqual(git(root, "rev-parse", "HEAD"), head_before)
        self.assertEqual(
            git(root, "status", "--porcelain", "--untracked-files=all"),
            worktree_before,
        )

    def test_cli_uses_commit_sha_and_annotated_tag_bases_read_only(self) -> None:
        root = self.make_repo()
        git(root, "remote", "remove", "origin")

        git(root, "checkout", "-qb", "merged")
        (root / "merged.txt").write_text("merged work\n", encoding="utf-8")
        git(root, "add", "merged.txt")
        git(root, "commit", "-qm", "merged work")
        merged_tip = git(root, "rev-parse", "HEAD")

        git(root, "checkout", "-q", "main")
        git(root, "checkout", "-qb", "unmerged")
        (root / "unmerged.txt").write_text("unmerged work\n", encoding="utf-8")
        git(root, "add", "unmerged.txt")
        git(root, "commit", "-qm", "unmerged work")
        unmerged_tip = git(root, "rev-parse", "HEAD")

        git(root, "checkout", "-q", "main")
        git(root, "merge", "--no-ff", "-qm", "merge completed work", "merged")
        (root / "base-checkpoint.txt").write_text(
            "comparison base\n", encoding="utf-8"
        )
        git(root, "add", "base-checkpoint.txt")
        git(root, "commit", "-qm", "comparison base")
        base_sha = git(root, "rev-parse", "HEAD")
        git(root, "tag", "-a", "annotated-base", "-m", "comparison base", base_sha)
        (root / "after-base.txt").write_text(
            "later main work\n", encoding="utf-8"
        )
        git(root, "add", "after-base.txt")
        git(root, "commit", "-qm", "later main work")
        main_tip = git(root, "rev-parse", "HEAD")

        self.assertNotEqual(base_sha, main_tip)
        self.assertNotIn(
            base_sha,
            git(root, "for-each-ref", "--format=%(objectname)").splitlines(),
        )
        ledger = root / "ledger.md"
        ledger.write_text(
            "\n".join([
                "## Branch decisions",
                "| Branch | Decision | Tip SHA | Evidence |",
                "|---|---|---|---|",
                f"| `merged` | **keep** | `{merged_tip}` | reviewed |",
                f"| `unmerged` | **keep** | `{unmerged_tip}` | reviewed |",
                "",
                "## Explicit exclusions and holds",
                "- `main` — current branch",
            ]),
            encoding="utf-8",
        )
        refs_before = git(
            root, "for-each-ref", "--format=%(refname) %(objectname)"
        )
        head_ref_before = git(root, "symbolic-ref", "HEAD")
        head_before = git(root, "rev-parse", "HEAD")
        worktree_before = git(
            root, "status", "--porcelain", "--untracked-files=all"
        )

        self.assertEqual(
            git(root, "cat-file", "-t", "refs/tags/annotated-base"),
            "tag",
        )
        for selected_base in (base_sha, "annotated-base"):
            with self.subTest(base=selected_base):
                result = subprocess.run(
                    [
                        sys.executable,
                        str(SCRIPT),
                        "--root",
                        str(root),
                        "--base",
                        selected_base,
                        "--decision-ledger",
                        str(ledger),
                    ],
                    cwd=root,
                    capture_output=True,
                    text=True,
                    check=False,
                )

                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                report = json.loads(result.stdout)
                self.assertEqual(report["base"], selected_base)
                self.assertEqual(report["merge_evidence"]["selected_base_sha"], base_sha)
                branch_statuses = {
                    branch["branch"]: branch["merged_into_base"]
                    for branch in report["branches"]
                }
                self.assertEqual(
                    branch_statuses,
                    {"main": False, "merged": True, "unmerged": False},
                )
                self.assertEqual(
                    git(root, "for-each-ref", "--format=%(refname) %(objectname)"),
                    refs_before,
                )
                self.assertEqual(git(root, "symbolic-ref", "HEAD"), head_ref_before)
                self.assertEqual(git(root, "rev-parse", "HEAD"), head_before)
                self.assertEqual(
                    git(root, "status", "--porcelain", "--untracked-files=all"),
                    worktree_before,
                )

    def test_cli_uses_commit_sha_base_from_detached_head_read_only(self) -> None:
        root = self.make_repo()
        git(root, "remote", "remove", "origin")

        git(root, "checkout", "-qb", "merged")
        (root / "merged.txt").write_text("merged work\n", encoding="utf-8")
        git(root, "add", "merged.txt")
        git(root, "commit", "-qm", "merged work")
        merged_tip = git(root, "rev-parse", "HEAD")

        git(root, "checkout", "-q", "main")
        git(root, "merge", "--no-ff", "-qm", "merge completed work", "merged")
        (root / "base-checkpoint.txt").write_text(
            "comparison base\n", encoding="utf-8"
        )
        git(root, "add", "base-checkpoint.txt")
        git(root, "commit", "-qm", "comparison base")
        base_sha = git(root, "rev-parse", "HEAD")

        git(root, "checkout", "-qb", "unmerged")
        (root / "unmerged.txt").write_text(
            "unmerged work\n", encoding="utf-8"
        )
        git(root, "add", "unmerged.txt")
        git(root, "commit", "-qm", "unmerged work")
        unmerged_tip = git(root, "rev-parse", "HEAD")

        ledger = root / "ledger.md"
        ledger.write_text(
            "\n".join([
                "## Branch decisions",
                "| Branch | Decision | Tip SHA | Evidence |",
                "|---|---|---|---|",
                f"| `merged` | **keep** | `{merged_tip}` | reviewed |",
                f"| `unmerged` | **keep** | `{unmerged_tip}` | reviewed |",
                "",
                "## Explicit exclusions and holds",
                "- `main` — current branch",
            ]),
            encoding="utf-8",
        )

        git(root, "checkout", "--detach", "-q", unmerged_tip)
        detached_head = git(root, "rev-parse", "HEAD")
        refs_before = git(
            root, "for-each-ref", "--format=%(refname) %(objectname)"
        )
        worktree_before = {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file() and ".git" not in path.relative_to(root).parts
        }

        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--root",
                str(root),
                "--base",
                base_sha,
                "--active-line",
                "main",
                "--decision-ledger",
                str(ledger),
            ],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["base"], base_sha)
        self.assertEqual(report["merge_evidence"]["selected_base_sha"], base_sha)
        branch_statuses = {
            branch["branch"]: branch["merged_into_base"]
            for branch in report["branches"]
        }
        self.assertTrue(branch_statuses["merged"])
        self.assertFalse(branch_statuses["unmerged"])
        self.assertEqual(git(root, "rev-parse", "--abbrev-ref", "HEAD"), "HEAD")
        self.assertEqual(git(root, "rev-parse", "HEAD"), detached_head)
        self.assertEqual(
            git(root, "for-each-ref", "--format=%(refname) %(objectname)"),
            refs_before,
        )
        self.assertEqual(
            {
                path.relative_to(root).as_posix(): path.read_bytes()
                for path in root.rglob("*")
                if path.is_file() and ".git" not in path.relative_to(root).parts
            },
            worktree_before,
        )

    def test_cli_classifies_branches_from_linked_worktree_read_only(self) -> None:
        root = self.make_repo()
        git(root, "remote", "remove", "origin")

        git(root, "checkout", "-qb", "merged")
        (root / "merged.txt").write_text("merged work\n", encoding="utf-8")
        git(root, "add", "merged.txt")
        git(root, "commit", "-qm", "merged work")
        merged_tip = git(root, "rev-parse", "HEAD")

        git(root, "checkout", "-q", "main")
        git(root, "checkout", "-qb", "unmerged")
        (root / "unmerged.txt").write_text("unmerged work\n", encoding="utf-8")
        git(root, "add", "unmerged.txt")
        git(root, "commit", "-qm", "unmerged work")
        unmerged_tip = git(root, "rev-parse", "HEAD")

        git(root, "checkout", "-q", "main")
        git(root, "merge", "--no-ff", "-qm", "merge completed work", "merged")
        (root / "base-checkpoint.txt").write_text(
            "comparison base\n", encoding="utf-8"
        )
        git(root, "add", "base-checkpoint.txt")
        git(root, "commit", "-qm", "comparison base")
        base_sha = git(root, "rev-parse", "HEAD")
        (root / "after-base.txt").write_text("later main work\n", encoding="utf-8")
        git(root, "add", "after-base.txt")
        git(root, "commit", "-qm", "later main work")
        main_tip = git(root, "rev-parse", "HEAD")

        linked = root.parent / f"{root.name}-linked"
        git(root, "worktree", "add", "--quiet", str(linked), "unmerged")
        self.addCleanup(
            lambda: git(root, "worktree", "remove", "--force", str(linked))
            if linked.exists()
            else None
        )

        ledger = root.parent / f"{root.name}-ledger.md"
        ledger.write_text(
            "\n".join([
                "## Branch decisions",
                "| Branch | Decision | Tip SHA | Evidence |",
                "|---|---|---|---|",
                f"| `main` | **keep** | `{main_tip}` | reviewed |",
                f"| `merged` | **keep** | `{merged_tip}` | reviewed |",
                "",
                "## Explicit exclusions and holds",
                "- `unmerged` — checked out in linked worktree",
            ]),
            encoding="utf-8",
        )
        self.addCleanup(lambda: ledger.unlink(missing_ok=True))

        def worktree_files(path: Path) -> dict[str, bytes]:
            return {
                file.relative_to(path).as_posix(): file.read_bytes()
                for file in path.rglob("*")
                if file.is_file() and ".git" not in file.relative_to(path).parts
            }

        refs_before = git(root, "for-each-ref", "--format=%(refname) %(objectname)")
        worktrees_before = git(root, "worktree", "list", "--porcelain")
        root_head_before = (git(root, "symbolic-ref", "HEAD"), git(root, "rev-parse", "HEAD"))
        linked_head_before = (
            git(linked, "symbolic-ref", "HEAD"),
            git(linked, "rev-parse", "HEAD"),
        )
        root_status_before = git(
            root, "status", "--porcelain", "--untracked-files=all"
        )
        linked_status_before = git(
            linked, "status", "--porcelain", "--untracked-files=all"
        )
        root_files_before = worktree_files(root)
        linked_files_before = worktree_files(linked)

        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--root",
                str(linked),
                "--base",
                base_sha,
                "--decision-ledger",
                str(ledger),
            ],
            cwd=linked,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["root"], str(linked))
        self.assertEqual(report["base"], base_sha)
        self.assertEqual(report["merge_evidence"]["selected_base_sha"], base_sha)
        branches = {branch["branch"]: branch for branch in report["branches"]}
        self.assertEqual(
            {
                name: branch["merged_into_base"]
                for name, branch in branches.items()
            },
            {"main": False, "merged": True, "unmerged": False},
        )
        self.assertTrue(branches["unmerged"]["is_current"])
        self.assertFalse(any(
            branch["is_current"]
            for name, branch in branches.items()
            if name != "unmerged"
        ))

        self.assertEqual(
            git(root, "for-each-ref", "--format=%(refname) %(objectname)"),
            refs_before,
        )
        self.assertEqual(git(root, "worktree", "list", "--porcelain"), worktrees_before)
        self.assertEqual(
            (git(root, "symbolic-ref", "HEAD"), git(root, "rev-parse", "HEAD")),
            root_head_before,
        )
        self.assertEqual(
            (
                git(linked, "symbolic-ref", "HEAD"),
                git(linked, "rev-parse", "HEAD"),
            ),
            linked_head_before,
        )
        self.assertEqual(
            git(root, "status", "--porcelain", "--untracked-files=all"),
            root_status_before,
        )
        self.assertEqual(
            git(linked, "status", "--porcelain", "--untracked-files=all"),
            linked_status_before,
        )
        self.assertEqual(worktree_files(root), root_files_before)
        self.assertEqual(worktree_files(linked), linked_files_before)

    def test_refresh_is_non_interactive_and_classifies_unavailable_remote(self) -> None:
        root = self.make_repo()
        failed_lookup = subprocess.CompletedProcess(
            ["git", "ls-remote", "--heads", "origin"],
            128,
            stdout=b"secondary stdout message",
            stderr=b"Host key verification failed.",
        )

        real_run = subprocess.run

        def mocked_run(args, *positional, **kwargs):
            if args == ["git", "ls-remote", "--heads", "origin"]:
                return failed_lookup
            return real_run(args, *positional, **kwargs)

        with patch.object(
            audit_repo.subprocess, "run", side_effect=mocked_run
        ) as run:
            result = audit_repo.refresh_remote(root)

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["classification"], "remote-unavailable")
        self.assertTrue(result["non_interactive"])
        self.assertFalse(result["tracking_refs_changed"])
        self.assertEqual(result["detail"], "origin: Host key verification failed.")
        kwargs = next(
            call.kwargs
            for call in run.call_args_list
            if call.args[0] == ["git", "ls-remote", "--heads", "origin"]
        )
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["env"]["GIT_TERMINAL_PROMPT"], "0")
        self.assertIn("BatchMode=yes", kwargs["env"]["GIT_SSH_COMMAND"])

    def test_refresh_reports_diagnostics_for_every_failed_remote(self) -> None:
        root = self.make_repo()
        invalid_utf8_diagnostic = b"backup unavailable: \xff\n"
        real_run = subprocess.run

        def mocked_run(args, *positional, **kwargs):
            if args == ["git", "remote"]:
                return subprocess.CompletedProcess(
                    args, 0, stdout="origin\nbackup\n", stderr=""
                )
            if args == ["git", "ls-remote", "--heads", "origin"]:
                return subprocess.CompletedProcess(
                    args, 128, stdout=b"", stderr=b"Host key verification failed."
                )
            if args == ["git", "ls-remote", "--heads", "backup"]:
                return subprocess.CompletedProcess(
                    args, 128, stdout=invalid_utf8_diagnostic, stderr=b""
                )
            return real_run(args, *positional, **kwargs)

        with patch.object(
            audit_repo.subprocess, "run", side_effect=mocked_run
        ):
            result = audit_repo.refresh_remote(root)

        encoded_diagnostic = {
            "encoding": "base64",
            "data": base64.b64encode(invalid_utf8_diagnostic).decode("ascii"),
        }
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["classification"], "remote-unavailable")
        self.assertEqual(
            result["failures"],
            [
                {
                    "remote": "origin",
                    "detail": "Host key verification failed.",
                },
                {"remote": "backup", "detail": encoded_diagnostic},
            ],
        )
        self.assertIn("origin: Host key verification failed.", result["detail"])
        self.assertIn("backup:", result["detail"])
        self.assertIn(encoded_diagnostic["data"], result["detail"])
        self.assertEqual(
            json.loads(json.dumps(result, ensure_ascii=True, allow_nan=False)),
            result,
        )

    def test_malformed_remote_rows_fail_audit_but_keep_valid_rows(self) -> None:
        root = self.make_repo()
        valid_sha = git(root, "rev-parse", "main")
        malformed_listing = b"\n".join([
            f"{valid_sha}\trefs/heads/main".encode("ascii"),
            b"missing separator",
            b"g" * 40 + b"\trefs/heads/bad-sha",
            f"{valid_sha}\trefs/heads/.hidden".encode("ascii"),
            f"{valid_sha}\trefs/tags/not-a-head".encode("ascii"),
            b"",
        ])
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
        original_run = subprocess.run

        def mocked_run(args, *positional, **kwargs):
            if args == ["git", "ls-remote", "--heads", "origin"]:
                return subprocess.CompletedProcess(
                    args, 0, stdout=malformed_listing, stderr=b""
                )
            return original_run(args, *positional, **kwargs)

        output = io.StringIO()
        with patch.object(
            audit_repo.subprocess, "run", side_effect=mocked_run
        ), patch.object(
            sys,
            "argv",
            [
                str(SCRIPT),
                "--root",
                str(root),
                "--base",
                "main",
                "--decision-ledger",
                str(ledger),
            ],
        ), contextlib.redirect_stdout(output):
            exit_code = audit_repo.main()

        report = json.loads(output.getvalue())
        remote_refresh = report["remote_refresh"]
        self.assertEqual(exit_code, 1)
        self.assertEqual(remote_refresh["status"], "unavailable")
        self.assertEqual(
            remote_refresh["classification"], "remote-unavailable"
        )
        self.assertNotEqual(
            remote_refresh["classification"], "remote-available"
        )
        self.assertEqual(
            remote_refresh["remote_refs"],
            [{
                "remote": "origin",
                "ref": "refs/heads/main",
                "sha": valid_sha,
            }],
        )
        self.assertEqual(
            [failure["line"] for failure in remote_refresh["failures"]],
            [2, 3, 4, 5],
        )
        self.assertIn(
            "missing tab separator", remote_refresh["failures"][0]["detail"]
        )
        self.assertIn("revision", remote_refresh["failures"][1]["detail"])
        self.assertIn("valid Git ref", remote_refresh["failures"][2]["detail"])
        self.assertIn("refs/heads/", remote_refresh["failures"][3]["detail"])
        self.assertFalse(remote_refresh["tracking_refs_changed"])

    def test_refresh_reads_valid_utf8_diagnostic_from_stdout_when_stderr_is_empty(self) -> None:
        root = self.make_repo()
        failed_lookup = subprocess.CompletedProcess(
            ["git", "ls-remote", "--heads", "origin"],
            128,
            stdout="remote unavailable: café".encode("utf-8"),
            stderr=b"",
        )

        real_run = subprocess.run

        def mocked_run(args, *positional, **kwargs):
            if args == ["git", "ls-remote", "--heads", "origin"]:
                return failed_lookup
            return real_run(args, *positional, **kwargs)

        with patch.object(audit_repo.subprocess, "run", side_effect=mocked_run):
            result = audit_repo.refresh_remote(root)

        self.assertEqual(result["classification"], "remote-unavailable")
        self.assertEqual(result["detail"], "origin: remote unavailable: café")

    def test_refresh_uses_stdout_diagnostic_when_stderr_is_whitespace(self) -> None:
        root = self.make_repo()
        failed_lookup = subprocess.CompletedProcess(
            ["git", "ls-remote", "--heads", "origin"],
            128,
            stdout=b"remote unavailable: permission denied\n",
            stderr=b" \t\r\n",
        )

        real_run = subprocess.run

        def mocked_run(args, *positional, **kwargs):
            if args == ["git", "ls-remote", "--heads", "origin"]:
                return failed_lookup
            return real_run(args, *positional, **kwargs)

        with patch.object(audit_repo.subprocess, "run", side_effect=mocked_run):
            result = audit_repo.refresh_remote(root)

        self.assertEqual(result["classification"], "remote-unavailable")
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(
            result["detail"], "origin: remote unavailable: permission denied"
        )
        self.assertEqual(
            result["failures"],
            [{"remote": "origin", "detail": "remote unavailable: permission denied"}],
        )

    def test_refresh_overrides_an_inherited_interactive_ssh_setting(self) -> None:
        root = self.make_repo()
        failed_lookup = subprocess.CompletedProcess(
            ["git", "ls-remote", "--heads", "origin"],
            128,
            stdout=b"",
            stderr=b"Host key verification failed.",
        )

        real_run = subprocess.run

        def mocked_run(args, *positional, **kwargs):
            if args == ["git", "ls-remote", "--heads", "origin"]:
                return failed_lookup
            return real_run(args, *positional, **kwargs)

        with patch.dict(
            audit_repo.os.environ,
            {"GIT_SSH_COMMAND": "ssh -o BatchMode=no"},
            clear=False,
        ), patch.object(
            audit_repo.subprocess, "run", side_effect=mocked_run
        ) as run:
            audit_repo.refresh_remote(root)

        self.assertEqual(
            next(
                call.kwargs["env"]
                for call in run.call_args_list
                if call.args[0] == ["git", "ls-remote", "--heads", "origin"]
            )["GIT_SSH_COMMAND"],
            "ssh -o BatchMode=yes",
        )

    def test_cli_reports_advanced_remote_heads_without_moving_tracking_refs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root = parent / "checkout"
            remote = parent / "origin.git"
            advance = parent / "advance"
            git(parent, "init", "-q", "--bare", str(remote))
            git(parent, "init", "-q", str(root))
            git(root, "branch", "-M", "main")
            git(root, "config", "user.email", "test@example.com")
            git(root, "config", "user.name", "Test User")
            (root / "README.md").write_text("initial\n", encoding="utf-8")
            git(root, "add", "README.md")
            git(root, "commit", "-qm", "initial")
            initial_sha = git(root, "rev-parse", "HEAD")
            git(root, "remote", "add", "origin", str(remote))
            git(root, "push", "-qu", "origin", "main")
            git(root, "fetch", "-q", "origin")

            git(parent, "clone", "-q", "--branch", "main", str(remote), str(advance))
            git(advance, "config", "user.email", "test@example.com")
            git(advance, "config", "user.name", "Test User")
            (advance / "README.md").write_text("advanced\n", encoding="utf-8")
            git(advance, "commit", "-qam", "advance remote")
            advanced_sha = git(advance, "rev-parse", "HEAD")
            git(advance, "push", "-q", "origin", "main")

            tracking_ref = "refs/remotes/origin/main"
            self.assertEqual(git(root, "rev-parse", tracking_ref), initial_sha)
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

            self.assertEqual(result.returncode, 1, result.stderr + result.stdout)
            report = json.loads(result.stdout)
            self.assertEqual(
                report["remote_refresh"]["classification"],
                "remote-available",
            )
            self.assertEqual(
                report["remote_refresh"]["remote_refs"],
                [{
                    "remote": "origin",
                    "ref": "refs/heads/main",
                    "sha": advanced_sha,
                }],
            )
            self.assertFalse(report["remote_refresh"]["tracking_refs_changed"])
            self.assertFalse(report["merge_evidence"]["ok"])
            self.assertEqual(report["merge_evidence"]["status"], "stale")
            self.assertTrue(report["merge_evidence"]["stale"])
            self.assertEqual(
                report["merge_evidence"]["selected_base_sha"], initial_sha
            )
            self.assertEqual(
                report["merge_evidence"]["remote_sha"], advanced_sha
            )
            self.assertEqual(git(root, "rev-parse", tracking_ref), initial_sha)
            self.assertNotEqual(
                git(root, "rev-parse", tracking_ref),
                report["remote_refresh"]["remote_refs"][0]["sha"],
            )

    def test_cli_preserves_available_remote_evidence_when_another_remote_fails(
        self,
    ) -> None:
        root = self.make_repo()
        available_remote = root.parent / f"{root.name}-available.git"
        unavailable_remote = root.parent / f"{root.name}-unavailable.git"
        git(root, "remote", "remove", "origin")
        git(root.parent, "init", "-q", "--bare", str(available_remote))
        git(root, "remote", "add", "available", str(available_remote))
        git(root, "push", "-qu", "available", "main")
        git(root, "fetch", "-q", "available")
        git(root, "remote", "add", "offline", str(unavailable_remote))
        git(
            root,
            "update-ref",
            "refs/remotes/offline/previously-seen",
            git(root, "rev-parse", "HEAD"),
        )
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
        refs_before = git(
            root, "for-each-ref", "--format=%(refname) %(objectname)"
        )
        available_sha = git(root, "rev-parse", "HEAD")

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

        self.assertEqual(result.returncode, 1, result.stderr + result.stdout)
        report = json.loads(result.stdout)
        remote_refresh = report["remote_refresh"]
        self.assertEqual(remote_refresh["status"], "unavailable")
        self.assertEqual(
            remote_refresh["classification"], "remote-unavailable"
        )
        self.assertEqual(
            remote_refresh["remote_refs"],
            [{
                "remote": "available",
                "ref": "refs/heads/main",
                "sha": available_sha,
            }],
        )
        self.assertEqual(
            [failure["remote"] for failure in remote_refresh["failures"]],
            ["offline"],
        )
        self.assertIn("offline:", remote_refresh["detail"])
        self.assertFalse(remote_refresh["tracking_refs_changed"])
        self.assertEqual(
            [branch["branch"] for branch in report["branches"]],
            ["main"],
        )
        self.assertEqual(
            git(root, "for-each-ref", "--format=%(refname) %(objectname)"),
            refs_before,
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
            report["remote_refresh"]["classification"],
            "remote-unavailable",
        )
        self.assertEqual([branch["branch"] for branch in report["branches"]], ["main"])
        self.assertIn("naming_violations", report)
        self.assertIn("detritus_folders", report)

    def test_cli_reports_missing_local_branch_evidence_when_listing_cannot_start(
        self,
    ) -> None:
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
        real_run = subprocess.run
        branch_listing = [
            "git",
            "for-each-ref",
            "--format=%(refname:strip=2)",
            "refs/heads/",
        ]

        def mocked_run(args, *positional, **kwargs):
            if args == ["git", "ls-remote", "--heads", "origin"]:
                return subprocess.CompletedProcess(
                    args, 128, stdout=b"", stderr=b"remote unavailable"
                )
            if args == branch_listing:
                raise FileNotFoundError("git executable unavailable")
            return real_run(args, *positional, **kwargs)

        output = io.StringIO()
        with patch.object(
            audit_repo.subprocess, "run", side_effect=mocked_run
        ), patch.object(
            sys,
            "argv",
            [
                str(SCRIPT),
                "--root",
                str(root),
                "--base",
                "main",
                "--decision-ledger",
                str(ledger),
            ],
        ), contextlib.redirect_stdout(output):
            return_code = audit_repo.main()

        self.assertEqual(return_code, 1)
        report = json.loads(output.getvalue())
        self.assertEqual(
            report["error"],
            "local branch evidence could not be collected",
        )
        self.assertEqual(
            report["remote_refresh"]["classification"],
            "remote-unavailable",
        )
        self.assertEqual(
            report["remote_refresh"]["detail"],
            "origin: remote unavailable",
        )
        self.assertEqual(
            report["local_branch_evidence"],
            {
                "status": "unavailable",
                "classification": "local-branch-evidence-unavailable",
                "detail": "FileNotFoundError: git executable unavailable",
            },
        )
        self.assertNotIn("branches", report)

    def test_cli_preserves_branch_inventory_when_current_branch_lookup_fails(
        self,
    ) -> None:
        root = self.make_repo()
        git(root, "checkout", "-qb", "review")
        (root / "review.txt").write_text("review work\n", encoding="utf-8")
        git(root, "add", "review.txt")
        git(root, "commit", "-qm", "review work")
        git(root, "checkout", "-q", "main")

        remote = root.parent / f"{root.name}-origin.git"
        git(root, "init", "--bare", str(remote))
        git(root, "remote", "set-url", "origin", str(remote))
        git(root, "push", "-q", "-u", "origin", "main")

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
        real_run = subprocess.run
        failed_current_lookups = []
        current_lookup = ["git", "branch", "--show-current"]

        def mocked_run(args, *positional, **kwargs):
            if args == current_lookup:
                failed_current_lookups.append(args)
                return subprocess.CompletedProcess(
                    args,
                    128,
                    stdout=b"",
                    stderr=b"current branch unavailable",
                )
            return real_run(args, *positional, **kwargs)

        output = io.StringIO()
        with patch.object(
            audit_repo.subprocess, "run", side_effect=mocked_run
        ), patch.object(
            sys,
            "argv",
            [
                str(SCRIPT),
                "--root",
                str(root),
                "--base",
                "main",
                "--decision-ledger",
                str(ledger),
            ],
        ), contextlib.redirect_stdout(output):
            return_code = audit_repo.main()

        self.assertEqual(failed_current_lookups, [current_lookup])
        self.assertEqual(return_code, 1)
        report = json.loads(output.getvalue())
        self.assertEqual(
            report["error"],
            "current branch evidence could not be determined",
        )
        self.assertEqual(report["remote_refresh"]["status"], "ok")
        self.assertEqual(
            report["remote_refresh"]["classification"],
            "remote-available",
        )
        self.assertEqual(
            [item["ref"] for item in report["remote_refresh"]["remote_refs"]],
            ["refs/heads/main"],
        )
        self.assertEqual(
            {branch["branch"] for branch in report["branches"]},
            {"main", "review"},
        )
        self.assertTrue(
            all(branch["is_current"] is None for branch in report["branches"])
        )
        self.assertEqual(
            report["current_branch_evidence"],
            {
                "status": "unavailable",
                "classification": "current-branch-evidence-unavailable",
                "detail": (
                    "git branch --show-current failed "
                    "(exit 128): current branch unavailable"
                ),
            },
        )

    def test_cli_keeps_merge_status_unavailable_when_git_command_fails(self) -> None:
        root = self.make_repo()
        git(root, "remote", "remove", "origin")
        git(root, "checkout", "-qb", "review")
        (root / "review.txt").write_text("review work\n", encoding="utf-8")
        git(root, "add", "review.txt")
        git(root, "commit", "-qm", "review work")
        review_tip = git(root, "rev-parse", "review")
        git(root, "checkout", "-q", "main")

        ledger = root / "ledger.md"
        ledger.write_text(
            "\n".join([
                "## Branch decisions",
                "| Branch | Decision | Tip SHA | Evidence |",
                "|---|---|---|---|",
                f"| `review` | **keep** | `{review_tip}` | active |",
                "",
                "## Explicit exclusions and holds",
                "- `main` — current branch",
            ]),
            encoding="utf-8",
        )
        real_run = subprocess.run
        failed_merge_commands = []

        def mocked_run(args, *positional, **kwargs):
            if (
                args[:2] == ["git", "for-each-ref"]
                and any(arg.startswith("--merged=") for arg in args)
            ):
                failed_merge_commands.append(args)
                return subprocess.CompletedProcess(
                    args,
                    128,
                    stdout=b"main\nreview\n",
                    stderr=b"merge status unavailable",
                )
            return real_run(args, *positional, **kwargs)

        output = io.StringIO()
        with patch.object(
            audit_repo.subprocess, "run", side_effect=mocked_run
        ), patch.object(
            sys,
            "argv",
            [
                str(SCRIPT),
                "--root",
                str(root),
                "--base",
                "main",
                "--decision-ledger",
                str(ledger),
            ],
        ), contextlib.redirect_stdout(output):
            return_code = audit_repo.main()

        self.assertEqual(len(failed_merge_commands), 1)
        self.assertEqual(return_code, 1)
        report = json.loads(output.getvalue())
        self.assertEqual(
            report["merge_status_evidence"],
            {
                "status": "unavailable",
                "classification": "merged-status-unavailable",
                "detail": (
                    "git for-each-ref --merged="
                    f"{git(root, 'rev-parse', 'main')} failed "
                    "(exit 128): merge status unavailable"
                ),
            },
        )
        self.assertEqual(report["remote_refresh"]["status"], "ok")
        self.assertNotEqual(report["merge_evidence"]["status"], "stale")
        self.assertTrue(report["decision_ledger"]["ok"])
        self.assertTrue(report["archive_equivalents"]["ok"])
        self.assertEqual(
            {
                branch["branch"]: branch["merged_into_base"]
                for branch in report["branches"]
            },
            {"main": None, "review": None},
        )

    def test_cli_keeps_merge_status_unavailable_when_git_cannot_start(self) -> None:
        root = self.make_repo()
        git(root, "remote", "remove", "origin")
        git(root, "checkout", "-qb", "review")
        (root / "review.txt").write_text("review work\n", encoding="utf-8")
        git(root, "add", "review.txt")
        git(root, "commit", "-qm", "review work")
        review_tip = git(root, "rev-parse", "review")
        git(root, "checkout", "-q", "main")

        ledger = root / "ledger.md"
        ledger.write_text(
            "\n".join([
                "## Branch decisions",
                "| Branch | Decision | Tip SHA | Evidence |",
                "|---|---|---|---|",
                f"| `review` | **keep** | `{review_tip}` | active |",
                "",
                "## Explicit exclusions and holds",
                "- `main` — current branch",
            ]),
            encoding="utf-8",
        )
        real_run = subprocess.run
        merge_command = []

        def mocked_run(args, *positional, **kwargs):
            if (
                args[:2] == ["git", "for-each-ref"]
                and any(arg.startswith("--merged=") for arg in args)
            ):
                merge_command.append(args)
                raise FileNotFoundError("git executable unavailable")
            return real_run(args, *positional, **kwargs)

        output = io.StringIO()
        with patch.object(
            audit_repo.subprocess, "run", side_effect=mocked_run
        ), patch.object(
            sys,
            "argv",
            [
                str(SCRIPT),
                "--root",
                str(root),
                "--base",
                "main",
                "--decision-ledger",
                str(ledger),
            ],
        ), contextlib.redirect_stdout(output):
            return_code = audit_repo.main()

        self.assertEqual(len(merge_command), 1)
        self.assertEqual(return_code, 1)
        report = json.loads(output.getvalue())
        self.assertEqual(
            report["merge_status_evidence"]["status"],
            "unavailable",
        )
        self.assertEqual(
            report["merge_status_evidence"]["classification"],
            "merged-status-unavailable",
        )
        self.assertIn(
            "FileNotFoundError: git executable unavailable",
            report["merge_status_evidence"]["detail"],
        )
        self.assertEqual(report["remote_refresh"]["status"], "ok")
        self.assertNotEqual(report["merge_evidence"]["status"], "stale")
        self.assertTrue(report["decision_ledger"]["ok"])
        self.assertTrue(report["archive_equivalents"]["ok"])
        self.assertEqual(
            {
                branch["branch"]: branch["merged_into_base"]
                for branch in report["branches"]
            },
            {"main": None, "review": None},
        )

    def test_cli_reports_unresolvable_base_without_mutating_repository(self) -> None:
        root = self.make_repo()
        git(root, "checkout", "-qb", "review-branch")
        (root / "review.txt").write_text("review work\n", encoding="utf-8")
        git(root, "add", "review.txt")
        git(root, "commit", "-qm", "review work")
        git(root, "checkout", "-q", "main")
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
        refs_before = git(
            root, "for-each-ref", "--format=%(refname) %(objectname)"
        )
        head_ref_before = git(root, "symbolic-ref", "HEAD")
        head_before = git(root, "rev-parse", "HEAD")
        worktree_before = git(
            root, "status", "--porcelain", "--untracked-files=all"
        )

        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--root",
                str(root),
                "--base",
                "missing-base-ref",
                "--decision-ledger",
                str(ledger),
            ],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(
            report,
            {
                "error": "selected base ref does not resolve to a commit",
                "base": "missing-base-ref",
                "root": str(root),
            },
        )
        self.assertEqual(
            git(root, "for-each-ref", "--format=%(refname) %(objectname)"),
            refs_before,
        )
        self.assertEqual(git(root, "symbolic-ref", "HEAD"), head_ref_before)
        self.assertEqual(git(root, "rev-parse", "HEAD"), head_before)
        self.assertEqual(
            git(root, "status", "--porcelain", "--untracked-files=all"),
            worktree_before,
        )

    def test_cli_rejects_tree_and_blob_bases_without_mutating_repository(self) -> None:
        root = self.make_repo()
        git(root, "checkout", "-qb", "review-branch")
        (root / "review.txt").write_text("review work\n", encoding="utf-8")
        git(root, "add", "review.txt")
        git(root, "commit", "-qm", "review work")
        git(root, "checkout", "-q", "main")
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

        tree_sha = git(root, "rev-parse", "HEAD^{tree}")
        blob_sha = git(root, "rev-parse", "HEAD:README.md")
        (root / "README.md").write_text("local unstaged change\n", encoding="utf-8")
        (root / "untracked.txt").write_text("local untracked file\n", encoding="utf-8")

        def repository_state() -> tuple[str, ...]:
            return (
                git(root, "for-each-ref", "--format=%(refname) %(objectname)"),
                git(root, "symbolic-ref", "HEAD"),
                git(root, "rev-parse", "HEAD"),
                git(root, "status", "--porcelain", "--untracked-files=all"),
                git(root, "diff", "--binary"),
                git(root, "diff", "--cached", "--binary"),
                (root / "README.md").read_text(encoding="utf-8"),
                (root / "untracked.txt").read_text(encoding="utf-8"),
            )

        for object_kind, base_sha in (("tree", tree_sha), ("blob", blob_sha)):
            with self.subTest(object_kind=object_kind):
                state_before = repository_state()
                result = subprocess.run(
                    [
                        sys.executable,
                        str(SCRIPT),
                        "--root",
                        str(root),
                        "--base",
                        base_sha,
                        "--decision-ledger",
                        str(ledger),
                    ],
                    cwd=root,
                    capture_output=True,
                    text=True,
                    check=False,
                )

                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                report = json.loads(result.stdout)
                self.assertEqual(
                    report,
                    {
                        "error": "selected base ref does not resolve to a commit",
                        "base": base_sha,
                        "root": str(root),
                    },
                )
                self.assertEqual(repository_state(), state_before)

    def test_cli_preserves_local_evidence_when_fetch_cannot_start(self) -> None:
        root = self.make_repo()
        git(root, "checkout", "-qb", "archive")
        (root / "archive.txt").write_text("archive evidence\n", encoding="utf-8")
        git(root, "add", "archive.txt")
        git(root, "commit", "-qm", "archive evidence")
        archive_tip = git(root, "rev-parse", "HEAD")
        git(root, "checkout", "-q", "main")

        ledger = root / "ledger.md"
        ledger.write_text(
            "\n".join([
                "## Branch decisions",
                "| Branch | Decision | Tip SHA | Evidence |",
                "|---|---|---|---|",
                f"| `archive` | **archive** | `{archive_tip}` | reviewed |",
                "",
                "## Explicit exclusions and holds",
                "- `main` — current branch",
            ]),
            encoding="utf-8",
        )
        real_run = subprocess.run
        fetch_attempts = 0

        def mocked_run(args, *positional, **kwargs):
            nonlocal fetch_attempts
            if args == ["git", "ls-remote", "--heads", "origin"]:
                fetch_attempts += 1
                raise OSError("git executable unavailable")
            return real_run(args, *positional, **kwargs)

        output = io.StringIO()
        with patch.object(
            audit_repo.subprocess, "run", side_effect=mocked_run
        ), patch.object(
            sys,
            "argv",
            [
                str(SCRIPT),
                "--root",
                str(root),
                "--base",
                "main",
                "--decision-ledger",
                str(ledger),
            ],
        ), contextlib.redirect_stdout(output):
            return_code = audit_repo.main()

        self.assertEqual(fetch_attempts, 1)
        self.assertEqual(return_code, 1)
        report = json.loads(output.getvalue())
        self.assertEqual(
            report["remote_refresh"]["classification"],
            "remote-unavailable",
        )
        self.assertEqual(
            report["remote_refresh"]["detail"],
            "origin: OSError: git executable unavailable",
        )
        self.assertEqual(
            {branch["branch"] for branch in report["branches"]},
            {"main", "archive"},
        )
        self.assertEqual(
            report["archive_equivalents"]["archives"][0]["branch"],
            "archive",
        )

    def test_cli_preserves_branch_and_ledger_evidence_when_archive_comparison_cannot_start(
        self,
    ) -> None:
        root = self.make_repo()
        git(root, "checkout", "-qb", "archive")
        (root / "archive.txt").write_text("archive evidence\n", encoding="utf-8")
        git(root, "add", "archive.txt")
        git(root, "commit", "-qm", "archive evidence")
        archive_tip = git(root, "rev-parse", "HEAD")
        git(root, "checkout", "-q", "main")
        ledger = root / "ledger.md"
        ledger.write_text(
            "\n".join([
                "## Branch decisions",
                "| Branch | Decision | Tip SHA | Evidence |",
                "|---|---|---|---|",
                f"| `archive` | **archive** | `{archive_tip}` | reviewed |",
                "",
                "## Explicit exclusions and holds",
                "- `main` — current branch",
            ]),
            encoding="utf-8",
        )
        real_run = subprocess.run
        failed_comparisons = 0

        def mocked_run(args, *positional, **kwargs):
            nonlocal failed_comparisons
            if args[:2] == ["git", "cherry"]:
                failed_comparisons += 1
                raise PermissionError("permission denied starting git cherry")
            return real_run(args, *positional, **kwargs)

        output = io.StringIO()
        with patch.object(
            audit_repo.subprocess, "run", side_effect=mocked_run
        ), patch.object(
            sys,
            "argv",
            [
                str(SCRIPT),
                "--root",
                str(root),
                "--base",
                "main",
                "--decision-ledger",
                str(ledger),
            ],
        ), contextlib.redirect_stdout(output):
            return_code = audit_repo.main()

        self.assertEqual(failed_comparisons, 1)
        self.assertEqual(return_code, 1)
        report = json.loads(output.getvalue())
        self.assertEqual(
            {branch["branch"] for branch in report["branches"]},
            {"main", "archive"},
        )
        self.assertEqual(report["decision_ledger"]["decision_row_count"], 1)
        self.assertEqual(report["decision_ledger"]["missing_branches"], [])
        self.assertTrue(report["decision_ledger"]["ok"])
        archive_equivalents = report["archive_equivalents"]
        self.assertEqual(archive_equivalents["unverifiable"], ["archive"])
        self.assertFalse(archive_equivalents["ok"])
        archive_report = archive_equivalents["archives"][0]
        self.assertEqual(archive_report["branch"], "archive")
        self.assertEqual(archive_report["classification"], "unverifiable")
        self.assertEqual(
            archive_report["error"],
            "Git comparison failed: git cherry (startup failure: "
            "PermissionError: permission denied starting git cherry)",
        )

    def test_cli_preserves_evidence_when_later_archive_diff_cannot_start(
        self,
    ) -> None:
        root = self.make_repo()
        git(root, "checkout", "-qb", "archive")
        (root / "archive.txt").write_text("archive evidence\n", encoding="utf-8")
        git(root, "add", "archive.txt")
        git(root, "commit", "-qm", "archive evidence")
        archive_tip = git(root, "rev-parse", "HEAD")
        git(root, "checkout", "-q", "main")
        ledger = root / "ledger.md"
        ledger.write_text(
            "\n".join([
                "## Branch decisions",
                "| Branch | Decision | Tip SHA | Evidence |",
                "|---|---|---|---|",
                f"| `archive` | **archive** | `{archive_tip}` | reviewed |",
                "",
                "## Explicit exclusions and holds",
                "- `main` — current branch",
            ]),
            encoding="utf-8",
        )
        real_run = subprocess.run
        comparison_steps = []

        def mocked_run(args, *positional, **kwargs):
            if args[:2] == ["git", "cherry"]:
                comparison_steps.append("git cherry")
            if args[:4] == ["git", "diff", "--no-renames", "--name-status"]:
                comparison_steps.append("file diff")
                raise OSError("archive diff unavailable")
            return real_run(args, *positional, **kwargs)

        output = io.StringIO()
        with patch.object(
            audit_repo.subprocess, "run", side_effect=mocked_run
        ), patch.object(
            sys,
            "argv",
            [
                str(SCRIPT),
                "--root",
                str(root),
                "--base",
                "main",
                "--decision-ledger",
                str(ledger),
            ],
        ), contextlib.redirect_stdout(output):
            return_code = audit_repo.main()

        self.assertEqual(comparison_steps, ["git cherry", "file diff"])
        self.assertEqual(return_code, 1)
        report = json.loads(output.getvalue())
        self.assertEqual(
            {branch["branch"] for branch in report["branches"]},
            {"main", "archive"},
        )
        self.assertEqual(report["decision_ledger"]["decision_row_count"], 1)
        self.assertEqual(report["decision_ledger"]["missing_branches"], [])
        self.assertTrue(report["decision_ledger"]["ok"])
        archive_equivalents = report["archive_equivalents"]
        self.assertEqual(archive_equivalents["unverifiable"], ["archive"])
        self.assertFalse(archive_equivalents["ok"])
        archive_report = archive_equivalents["archives"][0]
        self.assertEqual(archive_report["branch"], "archive")
        self.assertEqual(archive_report["classification"], "unverifiable")
        self.assertEqual(
            archive_report["error"],
            "Git comparison failed: file diff (startup failure: "
            "OSError: archive diff unavailable)",
        )

    def test_cli_preserves_local_evidence_when_fetch_fails_without_diagnostic(self) -> None:
        root = self.make_repo()
        git(root, "checkout", "-qb", "archive")
        (root / "archive.txt").write_text("archive evidence\n", encoding="utf-8")
        git(root, "add", "archive.txt")
        git(root, "commit", "-qm", "archive evidence")
        archive_tip = git(root, "rev-parse", "HEAD")
        git(root, "checkout", "-q", "main")

        ledger = root / "ledger.md"
        ledger.write_text(
            "\n".join([
                "## Branch decisions",
                "| Branch | Decision | Tip SHA | Evidence |",
                "|---|---|---|---|",
                f"| `archive` | **archive** | `{archive_tip}` | reviewed |",
                "",
                "## Explicit exclusions and holds",
                "- `main` — current branch",
            ]),
            encoding="utf-8",
        )
        real_run = subprocess.run

        def mocked_run(args, *positional, **kwargs):
            if args == ["git", "ls-remote", "--heads", "origin"]:
                return subprocess.CompletedProcess(
                    args, 128, stdout=b"", stderr=b""
                )
            return real_run(args, *positional, **kwargs)

        output = io.StringIO()
        with patch.object(
            audit_repo.subprocess, "run", side_effect=mocked_run
        ), patch.object(
            sys,
            "argv",
            [
                str(SCRIPT),
                "--root",
                str(root),
                "--base",
                "main",
                "--decision-ledger",
                str(ledger),
            ],
        ), contextlib.redirect_stdout(output):
            return_code = audit_repo.main()

        self.assertEqual(return_code, 1)
        report = json.loads(output.getvalue())
        self.assertEqual(
            report["remote_refresh"]["classification"],
            "remote-unavailable",
        )
        self.assertEqual(
            report["remote_refresh"]["detail"],
            "origin: git ls-remote failed without diagnostic output",
        )
        self.assertEqual(
            {branch["branch"] for branch in report["branches"]},
            {"main", "archive"},
        )
        self.assertEqual(
            report["archive_equivalents"]["archives"][0]["branch"],
            "archive",
        )

    def test_cli_preserves_local_evidence_with_invalid_utf8_fetch_diagnostic(self) -> None:
        root = self.make_repo()
        git(root, "checkout", "-qb", "archive")
        (root / "archive.txt").write_text("archive evidence\n", encoding="utf-8")
        git(root, "add", "archive.txt")
        git(root, "commit", "-qm", "archive evidence")
        archive_tip = git(root, "rev-parse", "HEAD")
        git(root, "checkout", "-q", "main")

        ledger = root / "ledger.md"
        ledger.write_text(
            "\n".join([
                "## Branch decisions",
                "| Branch | Decision | Tip SHA | Evidence |",
                "|---|---|---|---|",
                f"| `archive` | **archive** | `{archive_tip}` | reviewed |",
                "",
                "## Explicit exclusions and holds",
                "- `main` — current branch",
            ]),
            encoding="utf-8",
        )
        diagnostic = b"remote fetch failed: \xff\xfe\n"
        real_run = subprocess.run

        def mocked_run(args, *positional, **kwargs):
            if args == ["git", "ls-remote", "--heads", "origin"]:
                return subprocess.CompletedProcess(
                    args, 128, stdout=diagnostic, stderr=b""
                )
            return real_run(args, *positional, **kwargs)

        output = io.StringIO()
        with patch.object(
            audit_repo.subprocess, "run", side_effect=mocked_run
        ), patch.object(
            sys,
            "argv",
            [
                str(SCRIPT),
                "--root",
                str(root),
                "--base",
                "main",
                "--decision-ledger",
                str(ledger),
            ],
        ), contextlib.redirect_stdout(output):
            return_code = audit_repo.main()

        self.assertEqual(return_code, 1)
        report = json.loads(output.getvalue())
        self.assertEqual(
            report["remote_refresh"]["classification"],
            "remote-unavailable",
        )
        self.assertEqual(
            report["remote_refresh"]["detail"],
            {
                "encoding": "base64",
                "data": base64.b64encode(diagnostic).decode("ascii"),
            },
        )
        self.assertEqual(
            {branch["branch"] for branch in report["branches"]},
            {"main", "archive"},
        )
        self.assertEqual(
            report["archive_equivalents"]["archives"][0]["branch"],
            "archive",
        )


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
