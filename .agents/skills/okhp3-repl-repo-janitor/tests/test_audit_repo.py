from __future__ import annotations

import base64
import importlib.util
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

        for active_line in ("main", active_tip):
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
                    active_tip,
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
                    [("D", "active-only.txt"), ("A", "archive-only.txt")],
                )
                self.assertEqual(
                    refs_before,
                    git(root, "for-each-ref", "--format=%(refname) %(objectname)"),
                )
                self.assertEqual(
                    git(root, "branch", "--show-current"), "checkout-only-line"
                )
                self.assertEqual(git(root, "rev-parse", "HEAD"), checkout_tip)

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
