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


class AuditRepoTests(unittest.TestCase):
    def test_hosted_command_forces_noninteractive_ssh_batch_mode(self) -> None:
        commands = (
            "ssh", "", "ssh -o BatchMode=no", "ssh -oBatchMode no",
            "ssh -o batchmode=no -o BatchMode=yes",
        )
        for command in commands:
            with self.subTest(command=command), patch.dict(
                audit_repo.os.environ, {"GIT_SSH_COMMAND": command}, clear=True
            ), patch.object(audit_repo.subprocess, "run") as run:
                audit_repo.hosted_command(["git", "ls-remote", "origin"], Path("."))
                options = run.call_args.kwargs
                self.assertEqual(options["env"]["GIT_TERMINAL_PROMPT"], "0")
                self.assertEqual(options["stdin"], subprocess.DEVNULL)
                normalized = options["env"]["GIT_SSH_COMMAND"].lower()
                self.assertNotIn("batchmode=no", normalized)
                self.assertIn("batchmode=yes", normalized)

    def test_hosted_pull_request_page_limit_retains_unknown_history_hold(self) -> None:
        for count in (99, 100, 101):
            with self.subTest(count=count), patch.object(
                audit_repo, "gh_api_json", side_effect=[
                    ({"protected": False}, None),
                    ([], None),
                    ([{"state": "closed", "merged_at": "2099-01-01"}] * count, None),
                ]
            ):
                evidence = audit_repo.github_hosted_evidence(
                    Path("."), "https://github.com/fixture/repo.git", "feature/work"
                )
            expected = "unknown" if count >= 100 else "available"
            self.assertEqual(evidence["pull_requests"]["status"], expected)
            with patch.object(audit_repo, "remote_url_for_provider", return_value=(
                "origin", "https://github.com/fixture/repo.git"
            )), patch.object(audit_repo, "hosted_command", return_value=(
                subprocess.CompletedProcess([], 0, f"{'a' * 40}\trefs/heads/feature/work\n", "")
            )), patch.object(audit_repo, "github_hosted_evidence", return_value=evidence):
                report = audit_repo.audit_hosted_branches(Path("."), ["origin=feature/work"])
            self.assertEqual(report["deletion_blocked"], count >= 100)
            self.assertEqual(report["cleanup_plan"]["delete"], [])
            if count >= 100:
                self.assertEqual(report["entries"][0]["blocking_reasons"], [
                    "hosted-pull-request-evidence-unknown"
                ])

    def test_check_delete_rejects_hosted_options_before_repository_or_fetch(self) -> None:
        for option in ("--hosted-branch", "--hosted-ref"):
            with self.subTest(option=option), tempfile.TemporaryDirectory() as directory:
                result = subprocess.run([
                    sys.executable, str(SCRIPT), "--root", directory, "--check-delete",
                    "--branch", "feature/work", "--reviewed-head", "a" * 40,
                    "--fetch", option, "origin=feature/work",
                ], capture_output=True, text=True, check=False)
                self.assertEqual(result.returncode, 1)
                report = json.loads(result.stdout)
                self.assertIn("cannot be combined", report["error"])
                self.assertNotIn("commands", report)
                self.assertEqual(list(Path(directory).iterdir()), [])

    def test_every_hosted_hold_has_a_stable_code_and_specific_explanation(self) -> None:
        expected = {
            "hosted-remote-inaccessible": ("review", "The hosted remote could not be accessed; check access before reviewing deletion."),
            "hosted-ref-missing": ("review", "The hosted branch was not found; confirm its location before reviewing deletion."),
            "hosted-ref-protected": ("keep", "The hosted branch is protected; keep it."),
            "hosted-ref-has-deployments": ("keep", "The hosted branch has deployment records; keep it until their use is reviewed."),
            "hosted-open-pull-request": ("keep", "The hosted branch has an open pull request; keep it while that work is pending."),
            "hosted-closed-unmerged-pull-request": ("review", "The hosted branch has a closed pull request that was not merged; review its work before deletion."),
            "hosted-protection-unknown": ("review", "Branch protection could not be confirmed; check protection before reviewing deletion."),
            "hosted-deployment-evidence-unknown": ("review", "Deployment evidence is unavailable; check deployment use before reviewing deletion."),
            "hosted-pull-request-evidence-unknown": ("review", "Pull-request evidence is unavailable; check pull-request history before reviewing deletion."),
            "hosted-evidence-unknown": ("review", "The hosted deletion hold has no supporting reason; review the source evidence."),
        }
        self.assertEqual(set(expected), set(audit_repo.HOSTED_HOLD_EXPLANATIONS))
        for code, (bucket, explanation) in expected.items():
            with self.subTest(code=code):
                plan = audit_repo.hosted_cleanup_plan([{
                    "provider": "fixture", "ref": "feature/work",
                    "deletion_blocked": True, "blocking_reasons": [code],
                }])
                self.assertEqual(plan[bucket], [{
                    "provider": "fixture", "ref": "feature/work",
                    "blocking_reasons": [code],
                    "blocking_reason_explanations": [{
                        "reason_code": code, "explanation": explanation,
                    }],
                }])
                self.assertEqual(plan["merge"], [])
                self.assertEqual(plan["delete"], [])
                self.assertEqual(plan["keep" if bucket == "review" else "review"], [])

    def test_hosted_plan_is_deterministic_and_preserves_all_holds(self) -> None:
        entries = [
            {"provider": "z", "ref": "feature/b", "deletion_blocked": True,
             "blocking_reasons": ["hosted-ref-protected", "hosted-protection-unknown", "hosted-ref-protected"]},
            {"provider": "a", "ref": "feature/a", "deletion_blocked": True,
             "blocking_reasons": ["hosted-open-pull-request"]},
            {"provider": "a", "ref": "feature/b", "deletion_blocked": False,
             "blocking_reasons": []},
        ]
        plan = audit_repo.hosted_cleanup_plan(entries)
        self.assertEqual(plan, audit_repo.hosted_cleanup_plan(reversed(entries)))
        self.assertEqual([item["provider"] for item in plan["keep"]], ["a", "z"])
        item = plan["keep"][1]
        self.assertEqual(item["blocking_reasons"], [
            "hosted-protection-unknown", "hosted-ref-protected",
        ])
        self.assertEqual(
            [reason["reason_code"] for reason in item["blocking_reason_explanations"]],
            item["blocking_reasons"],
        )
        self.assertEqual(plan["review"], [])
        self.assertEqual(plan["merge"], [])
        self.assertEqual(plan["delete"], [])
        self.assertEqual(entries[0]["blocking_reasons"].count("hosted-ref-protected"), 2)

    def test_unknown_or_absent_hosted_reasons_remain_explained_review_holds(self) -> None:
        for reasons in ([], ["future-hold"]):
            with self.subTest(reasons=reasons):
                plan = audit_repo.hosted_cleanup_plan([{
                    "provider": "fixture", "ref": "feature/work",
                    "deletion_blocked": True, "blocking_reasons": reasons,
                }])
                code = reasons[0] if reasons else "hosted-evidence-unknown"
                item = plan["review"][0]
                self.assertEqual(item["blocking_reasons"], [code])
                self.assertEqual(item["blocking_reason_explanations"][0]["reason_code"], code)
                self.assertIn("review", item["blocking_reason_explanations"][0]["explanation"])
                self.assertEqual(plan["delete"], [])

    def test_hosted_audit_produces_explanations_for_every_blocking_reason(self) -> None:
        clear = {
            "protection": {"status": "unprotected"},
            "deployments": {"status": "available", "count": 0},
            "pull_requests": {"status": "available", "items": []},
        }
        fixtures = {
            "protected": {"protection": {"status": "protected"}},
            "deployed": {"deployments": {"status": "available", "count": 1}},
            "open-pr": {"pull_requests": {"status": "available", "items": [{"state": "open"}]}},
            "closed-pr": {"pull_requests": {"status": "available", "items": [{"state": "closed", "merged_at": None}]}},
            "unknown-protection": {"protection": {"status": "unknown"}},
            "unknown-deployments": {"deployments": {"status": "unknown"}},
            "unknown-pr": {"pull_requests": {"status": "unknown"}},
            "clear": {},
        }
        def probe(args, _root):
            output = "" if args[-1].endswith("/missing") else f"{'a' * 40}\t{args[-1]}\n"
            return subprocess.CompletedProcess(args, 0, output, "")

        with patch.object(audit_repo, "remote_url_for_provider", side_effect=lambda _root, provider: (
            provider, None if provider == "inaccessible" else "https://github.com/fixture/repo.git",
        )), patch.object(audit_repo, "hosted_command", side_effect=probe), patch.object(
            audit_repo, "github_hosted_evidence",
            side_effect=lambda _root, _url, branch: {**clear, **fixtures[branch]},
        ):
            report = audit_repo.audit_hosted_branches(Path("."), [
                "origin=missing", "inaccessible=work",
                *(f"origin={branch}" for branch in fixtures),
            ])
        plan = report["cleanup_plan"]
        items = plan["keep"] + plan["review"]
        self.assertEqual(len(items), 9)
        observed = set()
        for item in items:
            for reason in item["blocking_reason_explanations"]:
                code = reason["reason_code"]
                observed.add(code)
                self.assertEqual(reason["explanation"], audit_repo.HOSTED_HOLD_EXPLANATIONS[code])
            self.assertEqual(
                [reason["reason_code"] for reason in item["blocking_reason_explanations"]],
                item["blocking_reasons"],
            )
        self.assertEqual(observed, set(audit_repo.HOSTED_HOLD_EXPLANATIONS) - {"hosted-evidence-unknown"})
        self.assertEqual(plan["merge"], [])
        self.assertEqual(plan["delete"], [])

    def test_cli_hosted_hold_includes_code_and_explanation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._init_repo(root)
            result = subprocess.run([
                sys.executable, str(SCRIPT), "--root", str(root), "--base", "main",
                "--hosted-ref", "missing-remote=feature/work",
            ], capture_output=True, text=True, check=True)
            report = json.loads(result.stdout)
            item = report["hosted_lifecycle"]["cleanup_plan"]["review"][0]
            self.assertEqual(item["blocking_reasons"], ["hosted-remote-inaccessible"])
            self.assertEqual(item["blocking_reason_explanations"], [{
                "reason_code": "hosted-remote-inaccessible",
                "explanation": audit_repo.HOSTED_HOLD_EXPLANATIONS["hosted-remote-inaccessible"],
            }])

    def test_pre_delete_tip_change_holds_and_emits_no_deletion_commands(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._init_repo(root)
            self._git(root, "switch", "-q", "-c", "feature/cleanup")
            (root / "reviewed.txt").write_text("reviewed\n", encoding="utf-8")
            self._git(root, "add", "reviewed.txt")
            self._git(root, "commit", "-qm", "reviewed work")
            reviewed_head = self._git(root, "rev-parse", "HEAD").strip()

            (root / "moved.txt").write_text("changed\n", encoding="utf-8")
            self._git(root, "add", "moved.txt")
            self._git(root, "commit", "-qm", "moved branch tip")

            check = audit_repo.prepare_branch_deletion(
                root,
                "feature/cleanup",
                reviewed_head,
            )
            self.assertEqual(check["bucket"], "review")
            self.assertEqual(check["reviewed_head"], reviewed_head)
            self.assertEqual(
                check["current_head"],
                self._git(root, "rev-parse", "HEAD").strip(),
            )
            self.assertEqual(check["deletion_commands"], [])

    def test_pre_delete_matching_tip_keeps_remote_first_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._init_repo(root)
            self._git(root, "switch", "-q", "-c", "feature/cleanup")
            (root / "reviewed.txt").write_text("reviewed\n", encoding="utf-8")
            self._git(root, "add", "reviewed.txt")
            self._git(root, "commit", "-qm", "reviewed work")
            reviewed_head = self._git(root, "rev-parse", "HEAD").strip()

            check = audit_repo.prepare_branch_deletion(
                root,
                "feature/cleanup",
                reviewed_head,
                remote="upstream",
            )
            self.assertEqual(check["bucket"], "delete")
            self.assertEqual(check["reviewed_head"], reviewed_head)
            self.assertEqual(check["current_head"], reviewed_head)
            self.assertEqual(check["deletion_commands"], [
                ["git", "push", "upstream", "--delete", "feature/cleanup"],
                ["git", "branch", "-d", "feature/cleanup"],
            ])

    def test_cli_rejects_missing_deletion_approval_details(self) -> None:
        invalid_invocations = [
            (["--reviewed-head", "reviewed-sha"], "--check-delete requires --branch"),
            (["--branch", "feature/cleanup"], "--check-delete requires --reviewed-head"),
        ]
        for arguments, expected_error in invalid_invocations:
            with self.subTest(arguments=arguments), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self._init_repo(root)

                result = subprocess.run(
                    [
                        sys.executable,
                        str(SCRIPT),
                        "--root",
                        str(root),
                        "--check-delete",
                        *arguments,
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                )

                self.assertNotEqual(result.returncode, 0)
                error_report = json.loads(result.stdout)
                self.assertEqual(error_report["error"], expected_error)
                self.assertNotIn("deletion_commands", error_report)
                self.assertNotIn('"bucket": "delete"', result.stdout)

    def test_naming_exceptions_and_violations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in [
                "SiteTokens.css", "useDebounce.ts", "ChatPane.tsx",
                "My Document.md", "README.md", "robots.txt", "my_file.json",
                "photo.PNG",
            ]:
                (root / name).write_text("x", encoding="utf-8")
            violations = {
                item["path"]: item["reason"]
                for item in audit_repo.audit_naming(root)
            }
            self.assertEqual(violations["SiteTokens.css"], "mixed/camel/Pascal case")
            self.assertEqual(violations["My Document.md"], "contains spaces")
            self.assertEqual(violations["my_file.json"], "uses underscores instead of hyphens")
            self.assertEqual(violations["photo.PNG"], "uppercase extension")
            self.assertNotIn("useDebounce.ts", violations)
            self.assertNotIn("ChatPane.tsx", violations)
            self.assertNotIn("README.md", violations)
            self.assertNotIn("robots.txt", violations)

    def test_nested_detritus_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            nested = root / "docs" / "attached_assets"
            nested.mkdir(parents=True)
            (nested / "note.txt").write_text("x", encoding="utf-8")
            folders = audit_repo.audit_detritus(root)
            self.assertEqual(folders[0]["folder"], "docs/attached_assets")

    def test_missing_base_fails_visibly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            with self.assertRaises(audit_repo.AuditError):
                audit_repo.ensure_base(root, "origin/main")

    @staticmethod
    def _git(root: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout

    def _init_repo(self, root: Path) -> None:
        self._git(root, "init", "-q", "-b", "main")
        self._git(root, "config", "user.email", "test@example.com")
        self._git(root, "config", "user.name", "Audit Test")
        (root / "README.md").write_text("fixture\n", encoding="utf-8")
        self._git(root, "add", "README.md")
        self._git(root, "commit", "-qm", "initial")


if __name__ == "__main__":
    unittest.main()
