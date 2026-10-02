from __future__ import annotations

import importlib.util
import io
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

MALFORMED_PULL_REQUESTS = (
    {},
    {"merged_at": None},
    {"merged_at": "2099-01-01"},
    {"state": "open"},
    {"state": "closed"},
    {"state": None, "merged_at": None},
    {"state": "", "merged_at": None},
    {"state": "merged", "merged_at": "2099-01-01"},
    {"state": [], "merged_at": None},
    {"state": {}, "merged_at": None},
    {"state": "closed", "merged_at": False},
    {"state": "closed", "merged_at": 0},
    {"state": "closed", "merged_at": []},
    {"state": "closed", "merged_at": {}},
    {"state": "closed", "merged_at": ""},
    {"state": "closed", "merged_at": "  "},
    {"state": "open", "merged_at": False},
)


class AuditRepoTests(unittest.TestCase):
    def test_remote_start_failures_hold_and_continue_each_provider_ref(self) -> None:
        requested = ["origin=work", "mirror=work", "origin=later"]
        for failure in (
            FileNotFoundError(2, "No such file or directory", "git"),
            PermissionError(13, "Permission denied", "git"),
            OSError(8, "Exec format error", "git"),
        ):
            calls = []

            def command(args, **kwargs):
                calls.append(args)
                if len(calls) == 1:
                    raise failure
                return subprocess.CompletedProcess(args, 0, f"{'a' * 40}\t{args[-1]}\n", "")

            with self.subTest(failure=type(failure).__name__), patch.object(
                audit_repo, "remote_url_for_provider",
                side_effect=lambda _root, provider: (provider, "https://github.com/fixture/repo.git"),
            ), patch.object(audit_repo.subprocess, "run", side_effect=command), patch.object(
                audit_repo, "github_hosted_evidence", return_value={
                    "protection": {"status": "unprotected"},
                    "deployments": {"status": "available", "count": 0, "items": []},
                    "pull_requests": {"status": "available", "count": 0, "items": []},
                },
            ) as evidence:
                report = audit_repo.audit_hosted_branches(Path("."), requested)
            self.assertEqual([(args[3], args[-1]) for args in calls], [
                ("origin", "refs/heads/work"), ("mirror", "refs/heads/work"),
                ("origin", "refs/heads/later"),
            ])
            self.assertEqual(evidence.call_count, 2)
            entry = report["entries"][0]
            self.assertEqual(entry["classification"], "inaccessible")
            self.assertEqual(entry["ref_status"], "unknown")
            self.assertIn("command could not start", entry["reason"])
            self.assertIn(str(failure), entry["reason"])
            self.assertNotIn("tip", entry)
            for key in ("protection", "deployments", "pull_requests"):
                self.assertEqual(entry[key]["status"], "unknown")
                self.assertNotIn("count", entry[key])
                self.assertNotIn("items", entry[key])
            for later in report["entries"][1:]:
                self.assertEqual(later["classification"], "present")
                self.assertFalse(later["deletion_blocked"])
            self.assertEqual(report["blocking_entries"], ["origin:work"])
            self.assert_single_hosted_hold(report, "hosted-remote-inaccessible", "review")
            self.assertEqual(report["cleanup_plan"]["delete"], [])
            self.assertEqual(report["cleanup_plan"]["merge"], [])

    def test_api_start_failures_preserve_holds_and_continue_lookups_and_pairs(self) -> None:
        keys = ("protection", "deployments", "pull_requests")
        codes = (
            "hosted-protection-unknown", "hosted-deployment-evidence-unknown",
            "hosted-pull-request-evidence-unknown",
        )
        for failure in (
            FileNotFoundError(2, "No such file or directory", "gh"),
            PermissionError(13, "Permission denied", "gh"),
            OSError(8, "Exec format error", "gh"),
        ):
            for failed in ((0,), (1,), (2,), (0, 1, 2)):
                calls = []

                def command(args, **kwargs):
                    if args[0] == "git":
                        return subprocess.CompletedProcess(args, 0, f"{'a' * 40}\t{args[-1]}\n", "")
                    index = len(calls)
                    calls.append(args[2])
                    if index in failed:
                        raise failure
                    output = '{"protected": false}' if index % 3 == 0 else "[]"
                    return subprocess.CompletedProcess(args, 0, output, "")

                with self.subTest(failure=type(failure).__name__, failed=failed), patch.object(
                    audit_repo, "remote_url_for_provider",
                    side_effect=lambda _root, provider: (provider, "https://github.com/fixture/repo.git"),
                ), patch.object(audit_repo.shutil, "which", return_value="/fixture/gh"), patch.object(
                    audit_repo.subprocess, "run", side_effect=command,
                ):
                    report = audit_repo.audit_hosted_branches(
                        Path("."), ["origin=work", "mirror=work", "origin=later"],
                    )
                self.assertEqual(len(calls), 9)
                self.assertEqual(calls[:3], calls[3:6])
                self.assertIn("/branches/later", calls[6])
                self.assertIn("deployments?ref=later", calls[7])
                self.assertIn("pulls?state=all&head=fixture%3Alater", calls[8])
                entry = report["entries"][0]
                self.assertEqual(entry["classification"], "present")
                self.assertTrue(entry["deletion_blocked"])
                self.assertEqual(entry["blocking_reasons"], sorted(codes[index] for index in failed))
                for index, key in enumerate(keys):
                    if index in failed:
                        self.assertEqual(entry[key]["status"], "unknown")
                        self.assertIn("command could not start", entry[key]["reason"])
                        self.assertIn(str(failure), entry[key]["reason"])
                        self.assertNotIn("count", entry[key])
                        self.assertNotIn("items", entry[key])
                    else:
                        self.assertEqual(entry[key]["status"], "unprotected" if index == 0 else "available")
                for later in report["entries"][1:]:
                    self.assertEqual(later["classification"], "present")
                    self.assertFalse(later["deletion_blocked"])
                self.assertTrue(report["deletion_blocked"])
                self.assertEqual(report["blocking_entries"], ["origin:work"])
                self.assertEqual(len(report["cleanup_plan"]["review"]), 1)
                self.assertEqual(report["cleanup_plan"]["review"][0]["blocking_reasons"], entry["blocking_reasons"])
                self.assertEqual(report["cleanup_plan"]["delete"], [])
                self.assertEqual(report["cleanup_plan"]["merge"], [])

    def test_hosted_commands_have_a_finite_timeout(self) -> None:
        self.assertEqual(audit_repo.HOSTED_COMMAND_TIMEOUT_SECONDS, 30)
        for args in (
            ["git", "ls-remote", "--heads", "origin", "refs/heads/work"],
            ["gh", "api", "repos/fixture/repo/branches/work"],
        ):
            with self.subTest(args=args), patch.object(audit_repo.subprocess, "run") as run:
                audit_repo.hosted_command(args, Path("."))
                self.assertEqual(run.call_args.kwargs["timeout"], 30)
                self.assertEqual(run.call_args.kwargs["stdin"], subprocess.DEVNULL)

    def test_remote_timeout_holds_and_continues_without_trusting_partial_output(self) -> None:
        for partial_output in (None, b"", b"aaaaaaaa\trefs/heads/work\n"):
            def command(args, **kwargs):
                self.assertEqual(kwargs["timeout"], 30)
                if args[3] == "stalled":
                    raise subprocess.TimeoutExpired(args, 30, output=partial_output)
                return subprocess.CompletedProcess(args, 0, "", "")

            with self.subTest(partial_output=partial_output), patch.object(
                audit_repo, "remote_url_for_provider",
                side_effect=lambda _root, provider: (provider, "https://github.com/fixture/repo.git"),
            ), patch.object(audit_repo.subprocess, "run", side_effect=command) as run, patch.object(
                audit_repo, "github_hosted_evidence",
            ) as evidence:
                report = audit_repo.audit_hosted_branches(
                    Path("."), ["stalled=work", "responsive=work"],
                )
            self.assertEqual(run.call_count, 2)
            evidence.assert_not_called()
            entry = report["entries"][0]
            self.assertEqual(entry["classification"], "inaccessible")
            self.assertEqual(entry["ref_status"], "unknown")
            self.assertIn("timed out after 30 seconds", entry["reason"])
            self.assertNotIn("tip", entry)
            self.assertTrue(entry["deletion_blocked"])
            self.assertEqual(entry["blocking_reasons"], ["hosted-remote-inaccessible"])
            for key in ("protection", "deployments", "pull_requests"):
                self.assertEqual(entry[key]["status"], "unknown")
                self.assertNotIn("count", entry[key])
            self.assertEqual(report["entries"][1]["classification"], "missing")
            self.assertTrue(report["deletion_blocked"])
            self.assertEqual(len(report["cleanup_plan"]["review"]), 2)
            self.assertEqual(report["cleanup_plan"]["delete"], [])
            self.assertEqual(report["cleanup_plan"]["merge"], [])

    def test_github_timeouts_preserve_each_unknown_hold_and_continue(self) -> None:
        keys = ("protection", "deployments", "pull_requests")
        codes = (
            "hosted-protection-unknown", "hosted-deployment-evidence-unknown",
            "hosted-pull-request-evidence-unknown",
        )
        for timed_out in ((0,), (1,), (2,), (0, 1, 2)):
            calls = []

            def command(args, **kwargs):
                self.assertEqual(kwargs["timeout"], 30)
                if args[0] == "git":
                    return subprocess.CompletedProcess(args, 0, f"{'a' * 40}\trefs/heads/work\n", "")
                index = len(calls)
                calls.append(args)
                output = '{"protected": false}' if index == 0 else "[]"
                if index in timed_out:
                    # Even apparently complete output cannot be trusted after timeout.
                    raise subprocess.TimeoutExpired(args, 30, output=output.encode())
                return subprocess.CompletedProcess(args, 0, output, "")

            with self.subTest(timed_out=timed_out), patch.object(
                audit_repo, "remote_url_for_provider",
                return_value=("origin", "https://github.com/fixture/repo.git"),
            ), patch.object(audit_repo.shutil, "which", return_value="/fixture/gh"), patch.object(
                audit_repo.subprocess, "run", side_effect=command,
            ):
                report = audit_repo.audit_hosted_branches(Path("."), ["origin=work"])
            self.assertEqual(len(calls), 3)
            entry = report["entries"][0]
            self.assertEqual(entry["classification"], "present")
            self.assertTrue(entry["deletion_blocked"])
            self.assertEqual(entry["blocking_reasons"], sorted(codes[index] for index in timed_out))
            for index, key in enumerate(keys):
                if index in timed_out:
                    self.assertEqual(entry[key]["status"], "unknown")
                    self.assertIn("timed out after 30 seconds", entry[key]["reason"])
                    self.assertNotIn("count", entry[key])
                    self.assertNotIn("items", entry[key])
                else:
                    self.assertEqual(entry[key]["status"], "unprotected" if index == 0 else "available")
            self.assertTrue(report["deletion_blocked"])
            self.assertEqual(report["blocking_entries"], ["origin:work"])
            self.assertEqual(report["cleanup_plan"]["review"][0]["blocking_reasons"], entry["blocking_reasons"])
            self.assertEqual(report["cleanup_plan"]["delete"], [])
            self.assertEqual(report["cleanup_plan"]["merge"], [])

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

    def hosted_pages_fixture(self, deployment_pages, pr_pages):
        """Exercise the real JSON adapter and hold projection without network access."""
        deployment_endpoint = "repos/fixture/repo/deployments?ref=feature%2Fwork"
        pr_endpoint = "repos/fixture/repo/pulls?state=all&head=fixture%3Afeature%2Fwork"
        fixtures = {"repos/fixture/repo/branches/feature%2Fwork": {"protected": False}}
        for endpoint, pages in ((deployment_endpoint, deployment_pages), (pr_endpoint, pr_pages)):
            fixtures.update({
                f"{endpoint}&per_page=100&page={page}": data
                for page, data in enumerate(pages, 1)
            })
        calls = []

        def command(args, _root):
            if args[0] == "git":
                return subprocess.CompletedProcess(
                    args, 0, f"{'a' * 40}\trefs/heads/feature/work\n", "",
                )
            self.assertEqual(args[:2], ["gh", "api"])
            endpoint = args[2]
            calls.append(endpoint)
            self.assertIn(endpoint, fixtures, "unexpected or unfiltered pagination request")
            data = fixtures[endpoint]
            if isinstance(data, Exception):
                raise data
            if isinstance(data, subprocess.CompletedProcess):
                return data
            return subprocess.CompletedProcess(
                args, 0, data if isinstance(data, str) else json.dumps(data), "",
            )

        with patch.object(audit_repo, "remote_url_for_provider", return_value=(
            "origin", "https://github.com/fixture/repo.git",
        )), patch.object(audit_repo.shutil, "which", return_value="/fixture/gh"), patch.object(
            audit_repo, "hosted_command", side_effect=command,
        ):
            report = audit_repo.audit_hosted_branches(Path("."), ["origin=feature/work"])
        self.assertEqual(calls, list(fixtures))
        self.assertEqual(report["cleanup_plan"]["delete"], [])
        self.assertEqual(report["cleanup_plan"]["merge"], [])
        return report

    def assert_single_hosted_hold(self, report, code, bucket):
        self.assertTrue(report["deletion_blocked"])
        self.assertTrue(report["entries"][0]["deletion_blocked"])
        self.assertEqual(report["entries"][0]["blocking_reasons"], [code])
        item = report["cleanup_plan"][bucket][0]
        self.assertEqual(item["blocking_reasons"], [code])
        self.assertEqual(item["blocking_reason_explanations"], [{
            "reason_code": code,
            "explanation": audit_repo.HOSTED_HOLD_EXPLANATIONS[code],
        }])
        other_bucket = "keep" if bucket == "review" else "review"
        self.assertEqual(report["cleanup_plan"][other_bucket], [])

    def test_later_pr_pages_preserve_open_and_closed_unmerged_holds(self) -> None:
        merged = [{"number": n, "state": "closed", "merged_at": "2099-01-01"}
                  for n in range(1, 201)]
        for state, code, bucket in (
            ("open", "hosted-open-pull-request", "keep"),
            ("closed", "hosted-closed-unmerged-pull-request", "review"),
        ):
            for page in (2, 3):
                with self.subTest(state=state, page=page):
                    later_pr = {"number": 201, "state": state, "merged_at": None}
                    pages = [merged[:100]]
                    if page == 3:
                        pages.append(merged[100:])
                    pages.append([later_pr])
                    report = self.hosted_pages_fixture([[]], pages)
                    evidence = report["entries"][0]["pull_requests"]
                    self.assertEqual(evidence["status"], "available")
                    self.assertEqual(evidence["count"], 100 * (page - 1) + 1)
                    self.assertEqual(evidence["items"], sum(pages, []))
                    self.assert_single_hosted_hold(report, code, bucket)

    def test_pr_history_completes_only_after_short_or_empty_page(self) -> None:
        for count in (0, 99, 100, 101, 200):
            with self.subTest(count=count):
                items = [{"number": n, "state": "closed", "merged_at": "2099-01-01"}
                         for n in range(count)]
                pages = [items[n:n + 100] for n in range(0, count, 100)]
                if count % 100 == 0:
                    pages.append([])
                report = self.hosted_pages_fixture([[]], pages)
                evidence = report["entries"][0]["pull_requests"]
                self.assertEqual(evidence["status"], "available")
                self.assertEqual(evidence["count"], count)
                self.assertEqual(evidence["items"], items)
                self.assertFalse(report["deletion_blocked"])
                self.assertEqual(report["cleanup_plan"]["keep"], [])
                self.assertEqual(report["cleanup_plan"]["review"], [])

    def test_malformed_pr_fields_discard_history_and_retain_unknown_hold(self) -> None:
        merged = {"state": "closed", "merged_at": "2099-01-01"}
        for record in MALFORMED_PULL_REQUESTS:
            for page in (1, 2):
                with self.subTest(record=record, page=page):
                    pages = [[merged] * 100] if page == 2 else []
                    pages.append([merged, record])
                    report = self.hosted_pages_fixture([[]], pages)
                    entry = report["entries"][0]
                    evidence = entry["pull_requests"]
                    self.assertEqual(evidence, {
                        "status": "unknown",
                        "reason": f"GitHub pull-request response on page {page} contained invalid classification fields",
                    })
                    self.assertEqual(entry["protection"]["status"], "unprotected")
                    self.assertEqual(entry["deployments"]["status"], "available")
                    self.assert_single_hosted_hold(
                        report, "hosted-pull-request-evidence-unknown", "review",
                    )

    def test_hosted_audit_does_not_trust_malformed_available_pr_records(self) -> None:
        for record in (*MALFORMED_PULL_REQUESTS, None, "not an object"):
            with self.subTest(record=record), patch.object(
                audit_repo, "remote_url_for_provider",
                return_value=("origin", "https://github.com/fixture/repo.git"),
            ), patch.object(
                audit_repo, "hosted_command",
                return_value=subprocess.CompletedProcess(
                    [], 0, f"{'a' * 40}\trefs/heads/work\n", "",
                ),
            ), patch.object(audit_repo, "github_hosted_evidence", return_value={
                "protection": {"status": "unprotected"},
                "deployments": {"status": "available", "count": 0, "items": []},
                "pull_requests": {"status": "available", "count": 1, "items": [record]},
            }):
                report = audit_repo.audit_hosted_branches(Path("."), ["origin=work"])
            self.assert_single_hosted_hold(
                report, "hosted-pull-request-evidence-unknown", "review",
            )
            self.assertEqual(report["cleanup_plan"]["delete"], [])
            self.assertEqual(report["cleanup_plan"]["merge"], [])

    def test_explicit_null_and_merged_pr_fields_preserve_legitimate_results(self) -> None:
        for record, code, bucket in (
            ({"state": "open", "merged_at": None}, "hosted-open-pull-request", "keep"),
            ({"state": "closed", "merged_at": None}, "hosted-closed-unmerged-pull-request", "review"),
            ({"state": "closed", "merged_at": "2099-01-01"}, None, None),
        ):
            with self.subTest(record=record):
                report = self.hosted_pages_fixture([[]], [[record]])
                self.assertEqual(report["entries"][0]["pull_requests"], {
                    "status": "available", "source": "github-api",
                    "count": 1, "items": [record],
                })
                if code:
                    self.assert_single_hosted_hold(report, code, bucket)
                else:
                    self.assertFalse(report["deletion_blocked"])
                    self.assertEqual(report["entries"][0]["blocking_reasons"], [])
                    self.assertEqual(report["cleanup_plan"]["keep"], [])
                    self.assertEqual(report["cleanup_plan"]["review"], [])

    def test_deployment_pages_are_aggregated_before_classifying_holds(self) -> None:
        for count in (0, 99, 100, 101, 200, 201):
            with self.subTest(count=count):
                items = [{"id": n, "ref": "feature/work", "environment": "preview"}
                         for n in range(count)]
                pages = [items[n:n + 100] for n in range(0, count, 100)]
                if count % 100 == 0:
                    pages.append([])
                report = self.hosted_pages_fixture(pages, [[]])
                evidence = report["entries"][0]["deployments"]
                self.assertEqual(evidence["status"], "available")
                self.assertEqual(evidence["count"], count)
                self.assertEqual(evidence["items"], items)
                if count:
                    self.assert_single_hosted_hold(report, "hosted-ref-has-deployments", "keep")
                else:
                    self.assertFalse(report["deletion_blocked"])

    def test_partial_page_failures_discard_history_and_retain_unknown_holds(self) -> None:
        failures = (
            (subprocess.CompletedProcess([], 1, "", "rate limited"), "request failed"),
            (subprocess.TimeoutExpired(["gh", "api"], 30, output=b"[]"), "timed out"),
            (FileNotFoundError(2, "No such file or directory", "gh"), "command could not start"),
            (PermissionError(13, "Permission denied", "gh"), "command could not start"),
            ("not JSON", "invalid JSON"),
            ({"message": "unexpected object"}, "not a list"),
            ([None], "invalid records"),
            ([{}] * 101, "invalid records"),
        )
        for key, code in (
            ("deployments", "hosted-deployment-evidence-unknown"),
            ("pull_requests", "hosted-pull-request-evidence-unknown"),
        ):
            for failure, reason in failures:
                with self.subTest(key=key, reason=reason):
                    first_page = ([{"id": n} for n in range(100)] if key == "deployments"
                                  else [{"number": n, "state": "closed", "merged_at": "2099-01-01"}
                                        for n in range(100)])
                    pages = [first_page, failure]
                    report = self.hosted_pages_fixture(
                        pages if key == "deployments" else [[]],
                        pages if key == "pull_requests" else [[]],
                    )
                    evidence = report["entries"][0][key]
                    self.assertEqual(evidence["status"], "unknown")
                    self.assertIn("page 2", evidence["reason"])
                    self.assertIn(reason, evidence["reason"])
                    self.assertNotIn("count", evidence)
                    self.assertNotIn("items", evidence)
                    other = "pull_requests" if key == "deployments" else "deployments"
                    self.assertEqual(report["entries"][0][other]["status"], "available")
                    self.assert_single_hosted_hold(report, code, "review")

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
            "open-pr": {"pull_requests": {"status": "available", "items": [{"state": "open", "merged_at": None}]}},
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

    def test_local_git_start_failure_still_aborts_main_visibly(self) -> None:
        failure = FileNotFoundError(2, "No such file or directory", "git")
        with tempfile.TemporaryDirectory() as directory, patch.object(
            sys, "argv", [str(SCRIPT), "--root", directory, "--hosted-branch", "origin=work"],
        ), patch.object(audit_repo.subprocess, "run", side_effect=failure) as run, patch.object(
            audit_repo, "audit_hosted_branches",
        ) as hosted, patch("sys.stdout", new_callable=io.StringIO) as output:
            status = audit_repo.main()
        self.assertEqual(status, 1)
        self.assertEqual(json.loads(output.getvalue())["error"], str(failure))
        self.assertNotIn("hosted_lifecycle", json.loads(output.getvalue()))
        self.assertEqual(run.call_args.args[0], ["git", "rev-parse", "--is-inside-work-tree"])
        hosted.assert_not_called()

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
