from __future__ import annotations

import importlib.util
import hashlib
import io
import itertools
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import tracemalloc
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote


SCRIPT = Path(__file__).parents[1] / "scripts" / "audit-repo.py"
SPEC = importlib.util.spec_from_file_location("audit_repo", SCRIPT)
assert SPEC and SPEC.loader
audit_repo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit_repo)

ASCII_LOCALE = {
    "LC_ALL": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0",
}

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


def pull_request_fixture(**fields):
    """A classified PR returned for this fixture's exact head repository/ref."""
    return {
        "state": "closed", "merged_at": "2099-01-01",
        "head": {"ref": "feature/work", "repo": {"full_name": "fixture/repo"}},
        **fields,
    }


def dense_hosted_page(shape: str) -> str:
    """Build wire text, not a Python tree: fixture setup must not mask expansion."""
    unit = {
        "containers": '{"v":[[],{}]}',
        "international": json.dumps(
            {"é": ["東京", "😀", [], {}]}, ensure_ascii=True, separators=(",", ":"),
        ),
    }[shape]
    # Leave room for 100 record envelopes, staying close to the real 4-MiB cap.
    repeats = (audit_repo.HOSTED_COMMAND_MAX_OUTPUT_BYTES - 8192) // (100 * (len(unit) + 1))
    payload = "[" + ",".join(itertools.repeat(unit, repeats)) + "]"
    return "[" + ",".join(
        f'{{"id":{n},"ref":"work","payload":{payload}}}' for n in range(100)
    ) + "]"


def traced_call(callback):
    """Measure one phase; pre-existing fixture objects are intentionally untracked."""
    tracemalloc.start()
    try:
        result = callback()
        current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return result, current, peak


class AuditRepoTests(unittest.TestCase):
    def test_streamed_cli_error_preserves_compact_json_and_failure_status(self) -> None:
        stdout = io.StringIO()
        with patch.object(audit_repo.sys, "argv", [str(SCRIPT)]), patch.object(
            audit_repo.sys, "stdout", stdout,
        ), patch.object(audit_repo, "ensure_repository", side_effect=audit_repo.AuditError("café")):
            status = audit_repo.main()
        self.assertEqual(status, 1)
        self.assertEqual(stdout.getvalue(), json.dumps({
            "error": "café", "root": str(Path(".").resolve()),
        }) + "\n")

    def test_streamed_json_preserves_format_and_ascii_encoding(self) -> None:
        report = {
            "refs": ["café", "cafe\u0301", "東京", "😀", "\udcff"],
            "escaped": "\"\\\n\t\x00",
            "values": [None, True, False, 1, 1.25, [], {}],
        }
        for indent in (None, 2):
            with self.subTest(indent=indent):
                output = io.BytesIO()
                # Strict ASCII stdout must still accept every escaped JSON value.
                with io.TextIOWrapper(output, encoding="ascii", errors="strict") as stdout:
                    with patch.object(audit_repo.sys, "stdout", stdout):
                        audit_repo.print_json(report, indent=indent)
                    stdout.flush()
                    self.assertEqual(
                        output.getvalue(),
                        (json.dumps(report, indent=indent) + "\n").encode("ascii"),
                    )

    def test_near_budget_report_printing_has_bounded_serialization_memory(self) -> None:
        # Build retained histories and the reference BEFORE tracing serialization.
        histories = [
            [{"id": n, "ref": "work", "body": f"{pair}:{n}:" + "x" * 39000}
             for n in range(99)]
            for pair in range(4)
        ]
        budget = audit_repo.HostedHistoryBudget()
        for history in histories:
            size = budget.page_size(history, 0)
            self.assertIsNotNone(size)
            budget.used += size
        self.assertGreater(budget.used, audit_repo.HOSTED_HISTORY_MAX_RETAINED_BYTES * 0.9)
        hosted = {"entries": [
            {
                "provider": f"remote-{pair}", "ref": "work",
                "deployments": {"status": "available", "count": 99, "items": histories[pair * 2]},
                "pull_requests": {"status": "available", "count": 99, "items": histories[pair * 2 + 1]},
            } for pair in range(2)
        ]}
        report = {
            "root": str(Path(".").resolve()), "base": "origin/main",
            "fetch_performed": False, "current_branch": "work", "detached_head": False,
            "branches": [], "naming_violations": [], "detritus_folders": [],
            "hosted_lifecycle": hosted,
        }
        reference = (json.dumps(report, indent=2) + "\n").encode("ascii")
        expected_digest = hashlib.sha256(reference).digest()
        expected_bytes = len(reference)
        del reference

        class DigestSink:
            """Consume stdout without buffering the serialized report in the test."""

            def __init__(self):
                self.digest = hashlib.sha256()
                self.bytes_written = 0

            def write(self, text):
                encoded = text.encode("ascii")
                self.digest.update(encoded)
                self.bytes_written += len(encoded)
                return len(text)

        sink = DigestSink()
        with patch.object(audit_repo.sys, "argv", [
            str(SCRIPT), "--hosted-branch", "origin=work",
        ]), patch.object(audit_repo.sys, "stdout", sink), patch.object(
            audit_repo, "ensure_repository",
        ), patch.object(audit_repo, "ensure_base"), patch.object(
            audit_repo, "audit_branches", return_value=([], "work"),
        ), patch.object(audit_repo, "audit_naming", return_value=[]), patch.object(
            audit_repo, "audit_detritus", return_value=[],
        ), patch.object(audit_repo, "audit_hosted_branches", return_value=hosted):
            status, retained, peak = traced_call(audit_repo.main)
        self.assertEqual(status, 0)
        self.assertEqual(sink.bytes_written, expected_bytes)
        self.assertEqual(sink.digest.digest(), expected_digest)
        # Full-report dumps allocates tens of MiB; chunks need well under 1 MiB.
        self.assertLess(peak, 1024 * 1024)
        self.assertLess(retained, 256 * 1024)

    def test_dense_hosted_pages_have_shape_specific_parse_and_hash_bounds(self) -> None:
        # CPython traced allocations, not RSS or a universal 16-MiB process cap.
        # Tiny containers expand far more than escaped multilingual strings.
        # Bounds include headroom, but would catch a second whole tree during
        # parsing or a whole serialized page during streaming fingerprinting.
        for shape, parse_mib, minimum_tree_mib in (
            ("containers", 180, 64),
            ("international", 64, 16),
        ):
            with self.subTest(shape=shape):
                raw = dense_hosted_page(shape)
                wire_bytes = len(raw.encode("utf-8"))
                self.assertGreater(wire_bytes, 3.9 * 1024 * 1024)
                self.assertLess(wire_bytes, audit_repo.HOSTED_COMMAND_MAX_OUTPUT_BYTES)
                if shape == "international":
                    self.assertIn(r"\u00e9", raw)
                    self.assertIn(r"\ud83d\ude00", raw)
                budget = audit_repo.HostedHistoryBudget()
                with patch.object(
                    audit_repo.shutil, "which", return_value="/fixture/gh",
                ), patch.object(
                    audit_repo, "hosted_command",
                    return_value=subprocess.CompletedProcess([], 0, raw, ""),
                ):
                    (data, error), retained, parse_peak = traced_call(
                        lambda: audit_repo.gh_api_json(Path("."), "fixture"),
                    )
                self.assertIsNone(error)
                self.assertEqual(len(data), 100)
                self.assertEqual(budget.used, 0, "parsing is not retained-history accounting")
                self.assertGreater(retained, minimum_tree_mib * 1024 * 1024)
                self.assertLess(parse_peak, parse_mib * 1024 * 1024)
                if shape == "international":
                    self.assertEqual(data[0]["payload"][0], {"é": ["東京", "😀", [], {}]})

                # Reject the real expanded tree at the production retention
                # limit, independently of how little wire text represented it.
                with patch.object(
                    audit_repo, "gh_api_json", return_value=(data, None),
                ), patch.object(audit_repo.hashlib, "sha256") as fingerprint:
                    (discarded, error), _, accounting_peak = traced_call(
                        lambda: audit_repo.gh_api_list(
                            Path("."), "history?ref=work", "deployments", history_budget=budget,
                        ),
                    )
                self.assertIsNone(discarded)
                self.assertIn("retained history memory budget exhausted", error)
                self.assertLess(accounting_peak, 64 * 1024)
                self.assertEqual(budget.used, 0)
                fingerprint.assert_not_called()

                # A test-only larger budget lets the SAME tree reach hashing.
                # Parsing is outside this trace; a full-page dump during hashing
                # would breach the 1-MiB incremental allocation bound.
                with patch.object(
                    audit_repo, "HOSTED_HISTORY_MAX_RETAINED_BYTES", 512 * 1024 * 1024,
                ), patch.object(
                    audit_repo, "gh_api_json", side_effect=[(data, None), ([], None)],
                ), patch.object(
                    audit_repo.hashlib, "sha256", wraps=audit_repo.hashlib.sha256,
                ) as fingerprint:
                    (items, error), hash_retained, hash_peak = traced_call(
                        lambda: audit_repo.gh_api_list(
                            Path("."), "history?ref=work", "deployments", history_budget=budget,
                        ),
                    )
                self.assertIsNone(error)
                self.assertEqual(len(items), 100)
                self.assertIs(items[0], data[0])
                fingerprint.assert_called_once()
                self.assertGreater(budget.used, minimum_tree_mib * 1024 * 1024)
                self.assertLess(hash_peak, 1024 * 1024)
                self.assertLess(hash_retained, 64 * 1024)
                del data, items, raw

    def test_dense_hosted_pages_keep_unknown_holds_and_continue_later_pairs(self) -> None:
        for shape in ("containers", "international"):
            with self.subTest(shape=shape):
                raw = dense_hosted_page(shape)
                calls = []

                def command(args, root):
                    if args[0] == "git":
                        return subprocess.CompletedProcess(args, 0, "a" * 40 + "\t" + args[-1], "")
                    endpoint = args[-1]
                    calls.append(endpoint)
                    output = '{"protected":false}' if "/branches/" in endpoint else (
                        raw if "ref=work" in endpoint or "head=fixture%3Awork" in endpoint else "[]"
                    )
                    return subprocess.CompletedProcess(args, 0, output, "")

                with patch.object(
                    audit_repo, "remote_url_for_provider",
                    return_value=("origin", "https://github.com/fixture/repo.git"),
                ), patch.object(audit_repo.shutil, "which", return_value="/fixture/gh"), patch.object(
                    audit_repo, "hosted_command", side_effect=command,
                ):
                    report = audit_repo.audit_hosted_branches(Path("."), ["origin=work", "origin=later"])
                self.assertEqual(len(calls), 6)
                first, later = report["entries"]
                self.assertEqual(first["blocking_reasons"], [
                    "hosted-deployment-evidence-unknown", "hosted-pull-request-evidence-unknown",
                ])
                for key in ("deployments", "pull_requests"):
                    self.assertEqual(first[key]["status"], "unknown")
                    self.assertIn("memory budget exhausted", first[key]["reason"])
                    self.assertNotIn("items", first[key])
                    self.assertNotIn("count", first[key])
                    self.assertEqual(later[key]["count"], 0)
                self.assertTrue(first["deletion_blocked"])
                self.assertFalse(later["deletion_blocked"])
                self.assertEqual(len(report["cleanup_plan"]["review"]), 1)
                self.assertEqual(report["cleanup_plan"]["delete"], [])
                self.assertEqual(report["cleanup_plan"]["merge"], [])

    def test_hosted_resource_failures_discard_partial_history_and_preserve_holds(self) -> None:
        # Inject deterministically rather than starving the test process itself.
        for phase in ("parsing", "accounting", "fingerprint"):
            for failure in (MemoryError, RecursionError):
                for kind in ("deployments", "pull_requests"):
                    with self.subTest(phase=phase, failure=failure.__name__, kind=kind):
                        calls = []
                        budget = audit_repo.HostedHistoryBudget()
                        loads = audit_repo.json.loads
                        page_size = audit_repo.HostedHistoryBudget.page_size
                        iterencode = audit_repo.json.JSONEncoder.iterencode

                        def failing_page(data):
                            return isinstance(data, list) and data and data[0].get("id") == 200

                        def parse(raw):
                            if '"id": 200' in raw and phase == "parsing":
                                raise failure()
                            return loads(raw)

                        def account(owner, data, pending):
                            if failing_page(data) and phase == "accounting":
                                raise failure()
                            return page_size(owner, data, pending)

                        def encode(encoder, data, *args, **kwargs):
                            # Fail after yielding a chunk, exercising cleanup of
                            # an in-progress streaming hash as well as parsing.
                            for chunk in iterencode(encoder, data, *args, **kwargs):
                                yield chunk
                                if (
                                    failing_page(data) and phase == "fingerprint"
                                    and encoder.sort_keys and not encoder.ensure_ascii
                                ):
                                    raise failure()

                        def command(args, root):
                            if args[0] == "git":
                                return subprocess.CompletedProcess(args, 0, "a" * 40 + "\t" + args[-1], "")
                            endpoint = args[-1]
                            calls.append(endpoint)
                            affected = "/deployments?" if kind == "deployments" else "/pulls?"
                            if "/branches/" in endpoint:
                                data = {"protected": False}
                            elif affected in endpoint and (
                                "ref=work" in endpoint or "head=fixture%3Awork" in endpoint
                            ):
                                page = int(endpoint.rsplit("=", 1)[1])
                                data = [
                                    pull_request_fixture(id=page * 100 + n, ref="work",
                                                         head={"ref": "work", "repo": {"full_name": "fixture/repo"}})
                                    for n in range(100)
                                ]
                            else:
                                data = []
                            return subprocess.CompletedProcess(args, 0, json.dumps(data), "")

                        with patch.object(
                            audit_repo, "remote_url_for_provider",
                            return_value=("origin", "https://github.com/fixture/repo.git"),
                        ), patch.object(audit_repo.shutil, "which", return_value="/fixture/gh"), patch.object(
                            audit_repo, "hosted_command", side_effect=command,
                        ), patch.object(audit_repo.json, "loads", side_effect=parse), patch.object(
                            audit_repo.HostedHistoryBudget, "page_size", new=account,
                        ), patch.object(audit_repo.json.JSONEncoder, "iterencode", new=encode):
                            first = audit_repo.github_hosted_evidence(
                                Path("."), "https://github.com/fixture/repo.git", "work",
                                history_budget=budget,
                            )
                            self.assertEqual(budget.used, sys.getsizeof([]))
                            report = audit_repo.audit_hosted_branches(
                                Path("."), ["origin=work", "mirror=later"],
                            )
                        self.assertEqual(first[kind]["status"], "unknown")
                        entry, later = report["entries"]
                        evidence = entry[kind]
                        self.assertEqual(evidence["status"], "unknown")
                        self.assertIn("page 2", evidence["reason"])
                        self.assertIn(phase, evidence["reason"])
                        self.assertIn(failure.__name__, evidence["reason"])
                        self.assertIn("discarded", evidence["reason"])
                        self.assertNotIn("items", evidence)
                        self.assertNotIn("count", evidence)
                        code = ("hosted-deployment-evidence-unknown" if kind == "deployments"
                                else "hosted-pull-request-evidence-unknown")
                        self.assertEqual(entry["blocking_reasons"], [code])
                        self.assertTrue(entry["deletion_blocked"])
                        self.assertFalse(later["deletion_blocked"])
                        self.assertEqual(later["deployments"]["count"], 0)
                        self.assertEqual(later["pull_requests"]["count"], 0)
                        self.assertEqual(report["cleanup_plan"]["delete"], [])
                        self.assertEqual(report["cleanup_plan"]["merge"], [])
                        self.assertEqual(len(report["cleanup_plan"]["review"]), 1)
                        self.assertEqual(len(calls), 11)

    def test_branch_json_resource_failure_keeps_protection_unknown(self) -> None:
        loads = audit_repo.json.loads
        for failure in (MemoryError, RecursionError):
            def parse(raw):
                if raw == '{"protected":false}':
                    raise failure()
                return loads(raw)

            def command(args, root):
                output = (
                    "a" * 40 + "\t" + args[-1] if args[0] == "git" else
                    '{"protected":false}' if "/branches/work" in args[-1] else
                    '{"protected": false}' if "/branches/" in args[-1] else "[]"
                )
                return subprocess.CompletedProcess(args, 0, output, "")

            with self.subTest(failure=failure.__name__), patch.object(
                audit_repo, "remote_url_for_provider",
                return_value=("origin", "https://github.com/fixture/repo.git"),
            ), patch.object(audit_repo.shutil, "which", return_value="/fixture/gh"), patch.object(
                audit_repo, "hosted_command", side_effect=command,
            ), patch.object(audit_repo.json, "loads", side_effect=parse):
                report = audit_repo.audit_hosted_branches(Path("."), ["origin=work", "mirror=later"])
            entry, later = report["entries"]
            self.assertEqual(entry["protection"]["status"], "unknown")
            self.assertIn(failure.__name__, entry["protection"]["reason"])
            self.assertEqual(entry["blocking_reasons"], ["hosted-protection-unknown"])
            self.assertTrue(entry["deletion_blocked"])
            self.assertEqual(entry["deployments"]["count"], 0)
            self.assertEqual(entry["pull_requests"]["count"], 0)
            self.assertFalse(later["deletion_blocked"])
            self.assertEqual(len(report["cleanup_plan"]["review"]), 1)
            self.assertEqual(report["cleanup_plan"]["delete"], [])
            self.assertEqual(report["cleanup_plan"]["merge"], [])

    def test_large_history_pages_have_bounded_aggregate_memory(self) -> None:
        """Generate ten distinct near-wire-limit pages lazily, without a provider."""
        for budget_mib, expected_calls, reason, peak_mib in (
            (16, 5, "retained history memory budget exhausted", 40),
            (64, 10, "pagination budget exhausted", 64),
        ):
            calls = []

            def command(args, root):
                page = int(args[-1].rsplit("=", 1)[1])
                calls.append(page)
                raw = json.dumps([
                    {"id": page * 100 + n, "payload": f"{page}:" + "x" * 41000}
                    for n in range(100)
                ])
                self.assertGreater(len(raw), 3.9 * 1024 * 1024)
                self.assertLess(len(raw), audit_repo.HOSTED_COMMAND_MAX_OUTPUT_BYTES)
                return subprocess.CompletedProcess(args, 0, raw, "")

            with self.subTest(budget_mib=budget_mib), patch.object(
                audit_repo, "HOSTED_HISTORY_MAX_RETAINED_BYTES", budget_mib * 1024 * 1024,
            ), patch.object(audit_repo.shutil, "which", return_value="/fixture/gh"), patch.object(
                audit_repo, "hosted_command", side_effect=command,
            ):
                tracemalloc.start()
                try:
                    data, error = audit_repo.gh_api_list(
                        Path("."), "history?ref=work", "deployments",
                    )
                    current, peak = tracemalloc.get_traced_memory()
                finally:
                    tracemalloc.stop()
            self.assertIsNone(data)
            self.assertIn(reason, error)
            self.assertEqual(calls, list(range(1, expected_calls + 1)))
            self.assertLess(peak, peak_mib * 1024 * 1024)
            self.assertLess(current, 1024 * 1024, "discarded pages must not stay alive")

    def test_retained_histories_share_budget_across_provider_ref_pairs(self) -> None:
        requested = ["origin=work", "mirror=work", "origin=later", "mirror=later", "origin=empty"]
        calls = []

        def command(args, root):
            if args[0] == "git":
                return subprocess.CompletedProcess(args, 0, "a" * 40 + "\t" + args[-1], "")
            endpoint = args[-1]
            calls.append(endpoint)
            if "/branches/" in endpoint:
                data = {"protected": False}
            elif "empty" in endpoint:
                data = []
            elif "/deployments?" in endpoint:
                branch = "later" if "later" in endpoint else "work"
                data = [
                    {"id": n, "ref": branch, "environment": f"{n}:" + "x" * 39000}
                    for n in range(99)
                ]
            else:
                branch = "later" if "later" in endpoint else "work"
                data = [
                    pull_request_fixture(
                        number=n, title=f"{n}:" + "x" * 39000,
                        head={"ref": branch, "repo": {"full_name": "fixture/repo"}},
                    ) for n in range(99)
                ]
            raw = json.dumps(data)
            self.assertLess(len(raw), audit_repo.HOSTED_COMMAND_MAX_OUTPUT_BYTES)
            return subprocess.CompletedProcess(args, 0, raw, "")

        with patch.object(
            audit_repo, "remote_url_for_provider",
            side_effect=lambda root, provider: (provider, "https://github.com/fixture/repo.git"),
        ), patch.object(audit_repo.shutil, "which", return_value="/fixture/gh"), patch.object(
            audit_repo, "hosted_command", side_effect=command,
        ):
            tracemalloc.start()
            try:
                report = audit_repo.audit_hosted_branches(Path("."), requested)
                current, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
        self.assertLess(current, 17 * 1024 * 1024)
        self.assertLess(peak, 40 * 1024 * 1024)
        self.assertEqual(len(calls), 3 * len(requested), "later evidence must still run")
        self.assertEqual(
            [(entry["provider"], entry["ref"]) for entry in report["entries"]],
            [tuple(value.split("=")) for value in requested],
        )
        for entry in report["entries"][:2]:
            for key in ("deployments", "pull_requests"):
                self.assertEqual(entry[key]["status"], "available")
                self.assertEqual(entry[key]["count"], 99)
                self.assertEqual(len(entry[key]["items"]), 99)
        for entry in report["entries"][2:4]:
            self.assertTrue(entry["deletion_blocked"])
            self.assertEqual(entry["blocking_reasons"], [
                "hosted-deployment-evidence-unknown", "hosted-pull-request-evidence-unknown",
            ])
            for key in ("deployments", "pull_requests"):
                self.assertEqual(entry[key]["status"], "unknown")
                self.assertIn("memory budget exhausted", entry[key]["reason"])
                self.assertNotIn("items", entry[key])
                self.assertNotIn("count", entry[key])
        self.assertFalse(report["entries"][-1]["deletion_blocked"])
        self.assertEqual(len(report["cleanup_plan"]["review"]), 2)
        self.assertEqual(report["cleanup_plan"]["delete"], [])
        self.assertEqual(report["cleanup_plan"]["merge"], [])

    def test_discarded_history_releases_budget_for_later_complete_history(self) -> None:
        budget = audit_repo.HostedHistoryBudget()
        page = [{"id": n} for n in range(100)]
        with patch.object(
            audit_repo, "gh_api_json", side_effect=[(page, None), (page, None), ([{"id": 1}], None)],
        ):
            data, error = audit_repo.gh_api_list(
                Path("."), "history?ref=work", "deployments", history_budget=budget,
            )
            self.assertIsNone(data)
            self.assertIn("repeated full page", error)
            self.assertEqual(budget.used, 0)
            data, error = audit_repo.gh_api_list(
                Path("."), "history?ref=later", "deployments", history_budget=budget,
            )
        self.assertIsNone(error)
        self.assertEqual(data, [{"id": 1}])
        self.assertGreater(budget.used, 0)

    def test_memory_budget_counts_nested_objects_and_rolls_back_partial_history(self) -> None:
        budget = audit_repo.HostedHistoryBudget()
        complete = [{"id": 1}]
        full = [{"id": n, "nested": {"values": ["x" * 1000, n]}} for n in range(100)]
        complete_bytes = budget.page_size(complete, 0)
        full_bytes = budget.page_size(full, 0)
        # Allow a complete history and one full page, but not its short last page.
        limit = complete_bytes + full_bytes
        with patch.object(audit_repo, "HOSTED_HISTORY_MAX_RETAINED_BYTES", limit), patch.object(
            audit_repo, "gh_api_json",
            side_effect=[(complete, None), (full, None), (complete, None), (complete, None)],
        ):
            retained, error = audit_repo.gh_api_list(
                Path("."), "history?ref=first", "deployments", history_budget=budget,
            )
            self.assertIsNone(error)
            self.assertEqual(budget.used, complete_bytes)
            data, error = audit_repo.gh_api_list(
                Path("."), "history?ref=oversize", "deployments", history_budget=budget,
            )
            self.assertIsNone(data)
            self.assertIn("incomplete at page 2", error)
            self.assertIn("memory budget exhausted", error)
            self.assertEqual(budget.used, complete_bytes)
            self.assertEqual(retained, complete)
            data, error = audit_repo.gh_api_list(
                Path("."), "history?ref=later", "pull-request", history_budget=budget,
            )
        self.assertIsNone(error)
        self.assertEqual(data, complete)
        self.assertEqual(budget.used, 2 * complete_bytes)

    def test_remote_start_or_decode_failures_hold_and_continue_each_provider_ref(self) -> None:
        requested = ["origin=work", "mirror=work", "origin=later"]
        for failure, reason in (
            (FileNotFoundError(2, "No such file or directory", "git"), "command could not start"),
            (PermissionError(13, "Permission denied", "git"), "command could not start"),
            (OSError(8, "Exec format error", "git"), "command could not start"),
            (UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"), "output could not be decoded"),
        ):
            calls = []

            def command(args, _root):
                calls.append(args)
                if len(calls) == 1:
                    raise failure
                return subprocess.CompletedProcess(args, 0, f"{'a' * 40}\t{args[-1]}\n", "")

            with self.subTest(failure=type(failure).__name__), patch.object(
                audit_repo, "remote_url_for_provider",
                side_effect=lambda _root, provider: (provider, "https://github.com/fixture/repo.git"),
            ), patch.object(audit_repo, "hosted_command", side_effect=command), patch.object(
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
            self.assertIn(reason, entry["reason"])
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

    def test_api_start_or_decode_failures_preserve_holds_and_continue_lookups_and_pairs(self) -> None:
        keys = ("protection", "deployments", "pull_requests")
        codes = (
            "hosted-protection-unknown", "hosted-deployment-evidence-unknown",
            "hosted-pull-request-evidence-unknown",
        )
        for failure, reason in (
            (FileNotFoundError(2, "No such file or directory", "gh"), "command could not start"),
            (PermissionError(13, "Permission denied", "gh"), "command could not start"),
            (OSError(8, "Exec format error", "gh"), "command could not start"),
            (UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"), "output could not be decoded"),
        ):
            for failed in ((0,), (1,), (2,), (0, 1, 2)):
                calls = []

                def command(args, _root):
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
                    audit_repo, "hosted_command", side_effect=command,
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
                        self.assertIn(reason, entry[key]["reason"])
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

    def test_hosted_command_strictly_decodes_both_output_streams(self) -> None:
        for stream in ("stdout", "stderr"):
            with self.subTest(stream=stream):
                with self.assertRaises(UnicodeDecodeError):
                    audit_repo.hosted_command([
                        sys.executable, "-c",
                        f"import sys; sys.{stream}.buffer.write(b'\\xff')",
                    ], Path("."))

    def test_hosted_command_preserves_valid_utf8_on_both_streams(self) -> None:
        text = "café/cafe\u0301/東京/исправление/مرحبا"
        payload = text.encode("utf-8")
        result = audit_repo.hosted_command([
            sys.executable, "-c",
            f"import sys; sys.stdout.buffer.write({payload!r}); "
            f"sys.stderr.buffer.write({payload!r})",
        ], Path("."))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, text)
        self.assertEqual(result.stderr, text)

    def test_hosted_command_uses_strict_utf8_under_ascii_locale(self) -> None:
        text = "café/cafe\u0301/東京/исправление/مرحبا\r\n"
        script = f"""
import importlib.util, json, locale, sys
from pathlib import Path
assert sys.flags.utf8_mode == 0
assert locale.getpreferredencoding(False).lower() in ("ascii", "ansi_x3.4-1968")
spec = importlib.util.spec_from_file_location("audit_repo", {str(SCRIPT.resolve())!r})
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
payload = {text.encode("utf-8")!r}
result = audit.hosted_command([
    sys.executable, "-c",
    f"import sys; sys.stdout.buffer.write({{payload!r}}); "
    f"sys.stderr.buffer.write({{payload!r}})",
], Path("."))
print(json.dumps([result.returncode, result.stdout, result.stderr]))
for stream in ("stdout", "stderr"):
    for payload in (b"\\xff", b"\\xe9", b"\\xe2\\x82"):
        try:
            audit.hosted_command([
                sys.executable, "-c",
                f"import sys; sys.{{stream}}.buffer.write({{payload!r}})",
            ], Path("."))
        except UnicodeDecodeError as exc:
            assert exc.encoding == "utf-8", exc
        else:
            raise AssertionError((stream, payload))
"""
        result = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, timeout=30,
            env=ASCII_LOCALE,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = text.replace("\r\n", "\n")
        self.assertEqual(json.loads(result.stdout), [0, expected, expected])
        self.assertEqual(result.stderr, b"")

    def test_international_remote_refs_require_exact_unicode_matches(self) -> None:
        branch = "修正/café"
        full_ref = f"refs/heads/{branch}"
        # Visually equivalent Unicode spellings are distinct Git refs.
        near_matches = [
            "refs/heads/修正/cafe\u0301", f"{full_ref}-later",
            f"refs/heads/prefix/{branch}",
        ]
        evidence = {
            "protection": {"status": "unprotected"},
            "deployments": {"status": "available", "count": 0, "items": []},
            "pull_requests": {"status": "available", "count": 0, "items": []},
        }
        for exact in (False, True):
            output = "".join(f"{'b' * 40}\t{ref}\n" for ref in near_matches)
            if exact:
                output += f"{'a' * 40}\t{full_ref}\n"
            with self.subTest(exact=exact), patch.object(
                audit_repo, "remote_url_for_provider",
                return_value=("origin", "https://github.com/fixture/repo.git"),
            ), patch.object(
                audit_repo, "hosted_command",
                return_value=subprocess.CompletedProcess([], 0, output, ""),
            ) as command, patch.object(
                audit_repo, "github_hosted_evidence", return_value=evidence,
            ) as github:
                report = audit_repo.audit_hosted_branches(
                    Path("."), [f"origin=refs/heads/{branch}"],
                )
            command.assert_called_once_with([
                "git", "ls-remote", "--heads", "origin", full_ref,
            ], Path("."))
            entry = report["entries"][0]
            self.assertEqual(entry["ref"], branch)
            self.assertEqual(entry["full_ref"], full_ref)
            self.assertEqual(entry["classification"], "present" if exact else "missing")
            self.assertEqual(entry["ref_status"], "present" if exact else "missing")
            self.assertEqual(entry["deletion_blocked"], not exact)
            if exact:
                self.assertEqual(entry["tip"], "a" * 40)
                self.assertEqual(entry["blocking_reasons"], [])
                github.assert_called_once_with(
                    Path("."), "https://github.com/fixture/repo.git", branch,
                    history_budget=unittest.mock.ANY,
                )
            else:
                self.assertNotIn("tip", entry)
                github.assert_not_called()
                self.assert_single_hosted_hold(report, "hosted-ref-missing", "review")

    def test_cli_preserves_international_refs_and_github_json_fields(self) -> None:
        self._check_cli_preserves_international_fields(ascii_locale=False)

    def test_cli_preserves_international_github_json_fields_under_ascii_locale(self) -> None:
        self._check_cli_preserves_international_fields(ascii_locale=True)

    @unittest.skipUnless(os.name == "posix", "POSIX byte argv policy")
    def test_cli_preserves_international_refs_under_ascii_locale(self) -> None:
        self._check_cli_preserves_international_fields(
            ascii_locale=True, international_args=True,
        )

    @unittest.skipUnless(os.name == "posix", "POSIX byte argv policy")
    def test_cli_rejects_invalid_utf8_hosted_arguments_before_commands(self) -> None:
        for option in ("--hosted-ref", "--hosted-branch"):
            for payload in (b"origin=caf\xe9", b"origin=\xff", b"origin=\xe2\x82"):
                with self.subTest(option=option, payload=payload), tempfile.TemporaryDirectory() as directory:
                    fixture = Path(directory)
                    calls = fixture / "calls"
                    # Catch even local repository commands and opt-in fetch;
                    # no command may start when argument encoding is invalid.
                    executable = (
                        f"#!{sys.executable}\n"
                        "from pathlib import Path\n"
                        f"Path({str(calls)!r}).write_text('unexpected command')\n"
                        "raise SystemExit(91)\n"
                    )
                    for tool in ("git", "gh"):
                        path = fixture / tool
                        path.write_text(executable, encoding="utf-8")
                        path.chmod(0o755)
                    result = subprocess.run([
                        os.fsencode(sys.executable), os.fsencode(SCRIPT),
                        b"--root", os.fsencode(fixture), b"--fetch",
                        option.encode("ascii"), payload,
                    ], capture_output=True, timeout=30, env={
                        **ASCII_LOCALE, "PATH": str(fixture),
                    })
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertIn(b"hosted provider/branch arguments must be valid UTF-8", result.stderr)
                    self.assertEqual(result.stdout, b"")
                    self.assertFalse(calls.exists())

    def _check_cli_preserves_international_fields(
        self, *, ascii_locale: bool, international_args: bool = False,
    ) -> None:
        real_git = shutil.which("git")
        self.assertIsNotNone(real_git)
        # Literal encodings catch changes to quoting, including slash and % handling.
        branches = [
            ("feature/café", "feature%2Fcaf%C3%A9"),
            ("feature/cafe\u0301", "feature%2Fcafe%CC%81"),
            ("修正/東京", "%E4%BF%AE%E6%AD%A3%2F%E6%9D%B1%E4%BA%AC"),
            ("исправление/100%", "%D0%B8%D1%81%D0%BF%D1%80%D0%B0%D0%B2%D0%BB"
             "%D0%B5%D0%BD%D0%B8%D0%B5%2F100%25"),
        ]
        if ascii_locale and not international_args:
            # OS argv decoding is separate from hosted output decoding. Keep
            # ASCII CLI refs here, but retain literal UTF-8 JSON and stderr.
            branches = [
                ("feature/first", "feature%2Ffirst"),
                ("feature/second", "feature%2Fsecond"),
                ("fix/tokyo", "fix%2Ftokyo"),
                ("fix/100%", "fix%2F100%25"),
            ]
        title = "Réparer 東京 — исправление مرحبا"
        environment = "本番/produção"
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory)
            root = fixture / "repo"
            root.mkdir()
            self._init_repo(root)
            bin_dir = fixture / "bin"
            bin_dir.mkdir()
            calls_path = fixture / "calls.jsonl"
            responses = {}
            expected_calls = []
            expected_prs = []
            expected_deployments = []

            def respond(args, output):
                responses[json.dumps(args)] = output
                expected_calls.append(args)

            for index, (branch, encoded) in enumerate(branches):
                pr = pull_request_fixture(
                    number=index + 1, title=title,
                    head={"ref": branch, "repo": {"full_name": "fixture/repo"}},
                    base={"ref": "主分支", "repo": {"full_name": "fixture/repo"}},
                    html_url=f"https://github.com/fixture/repo/pull/{index + 1}",
                )
                deployments = ([{
                    "id": index, "sha": "a" * 40, "ref": branch,
                    "environment": environment,
                }] if index == 2 else [])
                expected_prs.append(pr)
                expected_deployments.append(deployments)
                respond(["git", "remote", "get-url", "origin"],
                        "https://github.com/fixture/repo.git\n")
                respond(["git", "ls-remote", "--heads", "origin", f"refs/heads/{branch}"],
                        f"{'b' * 40}\trefs/heads/{branch}-later\n"
                        f"{'a' * 40}\trefs/heads/{branch}\n")
                respond(["gh", "api", f"repos/fixture/repo/branches/{encoded}"],
                        json.dumps({"name": branch, "protected": False}, ensure_ascii=False))
                respond(["gh", "api", f"repos/fixture/repo/deployments?ref={encoded}"
                         "&per_page=100&page=1"],
                        json.dumps(deployments, ensure_ascii=False))
                respond(["gh", "api", "repos/fixture/repo/pulls?state=all"
                         f"&head=fixture%3A{encoded}&per_page=100&page=1"],
                        json.dumps([pr], ensure_ascii=False))
            responses_path = fixture / "responses.json"
            responses_path.write_text(json.dumps(responses, ensure_ascii=False), encoding="utf-8")
            executable = (
                f"#!{sys.executable}\n"
                "import json, locale, os, sys\n"
                "from pathlib import Path\n"
                f"REAL_GIT = {real_git!r}\n"
                r'''
args = sys.argv[1:]
if os.environ.get("CLI_FIXTURE_ASCII") == "1":
    assert sys.flags.utf8_mode == 0
    assert sys.getfilesystemencoding().lower() == "ascii"
    assert locale.getpreferredencoding(False).lower() in ("ascii", "ansi_x3.4-1968")
# The stand-in observes the raw UTF-8 argv bytes, not locale-decoded text.
if os.name == "posix":
    args = [os.fsencode(arg).decode("utf-8", errors="strict") for arg in args]
tool = Path(sys.argv[0]).name
if tool == "git" and args[0] in ("rev-parse", "branch", "for-each-ref", "log"):
    os.execv(REAL_GIT, [REAL_GIT, *args])
call = [tool, *args]
with open(os.environ["CLI_FIXTURE_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(call) + "\n")
responses = json.loads(Path(os.environ["CLI_FIXTURE_RESPONSES"]).read_text(encoding="utf-8"))
# Unexpected commands fail locally; never fall through to a real hosted command.
output = responses[json.dumps(call)]
sys.stdout.buffer.write(output.encode("utf-8"))
# remote get-url is a local Git read, outside the hosted decoding policy.
if call[:3] != ["git", "remote", "get-url"]:
    sys.stderr.buffer.write("診断: succès\n".encode("utf-8"))
'''
            )
            for tool in ("git", "gh"):
                path = bin_dir / tool
                path.write_text(executable, encoding="utf-8")
                path.chmod(0o755)
            command = [
                sys.executable, str(SCRIPT), "--root", str(root), "--base", "main",
                *[arg for branch, _ in branches for arg in ("--hosted-ref", f"origin={branch}")],
            ]
            if international_args:
                # Specify actual OS argv bytes independently of the parent's locale.
                command = [arg.encode("utf-8") for arg in command]
            result = subprocess.run(command, capture_output=True, timeout=30, env={
                "PATH": str(bin_dir), "PYTHONUTF8": "1",
                **(ASCII_LOCALE if ascii_locale else {}),
                "CLI_FIXTURE_LOG": str(calls_path),
                "CLI_FIXTURE_RESPONSES": str(responses_path),
                "CLI_FIXTURE_ASCII": "1" if ascii_locale else "0",
            })
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, b"")
            report = json.loads(result.stdout.decode("utf-8"))
            self.assertNotIn("error", report)
            hosted = report["hosted_lifecycle"]
            self.assertEqual([entry["ref"] for entry in hosted["entries"]],
                             [branch for branch, _ in branches])
            for index, entry in enumerate(hosted["entries"]):
                branch = branches[index][0]
                self.assertEqual(entry["provider"], "origin")
                self.assertEqual(entry["full_ref"], f"refs/heads/{branch}")
                self.assertEqual(entry["classification"], "present")
                self.assertEqual(entry["ref_status"], "present")
                self.assertEqual(entry["tip"], "a" * 40)
                self.assertEqual(entry["protection"]["status"], "unprotected")
                self.assertEqual(entry["protection"]["ref"], branch)
                self.assertEqual(entry["pull_requests"]["status"], "available")
                self.assertEqual(entry["pull_requests"]["count"], 1)
                self.assertEqual(entry["pull_requests"]["items"], [expected_prs[index]])
                self.assertEqual(entry["deployments"]["status"], "available")
                self.assertEqual(entry["deployments"]["count"], len(expected_deployments[index]))
                self.assertEqual(entry["deployments"]["items"], expected_deployments[index])
                self.assertEqual(entry["deletion_blocked"], index == 2)
                self.assertEqual(entry["blocking_reasons"],
                                 ["hosted-ref-has-deployments"] if index == 2 else [])
            self.assertEqual(hosted["blocking_entries"], [f"origin:{branches[2][0]}"])
            self.assertTrue(hosted["deletion_blocked"])
            plan = hosted["cleanup_plan"]
            self.assertEqual(plan["review"], [])
            self.assertEqual(plan["merge"], [])
            self.assertEqual(plan["delete"], [])
            self.assertEqual(len(plan["keep"]), 1)
            self.assertEqual(plan["keep"][0]["ref"], branches[2][0])
            self.assertEqual(plan["keep"][0]["blocking_reasons"], ["hosted-ref-has-deployments"])
            self.assertNotIn("deletion_commands", result.stdout.decode("utf-8"))
            calls = [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(calls, expected_calls)

    def test_cli_unusable_hosted_output_preserves_holds_and_later_pairs(self) -> None:
        self._check_cli_unusable_hosted_output(returncode=0)

    def test_cli_failed_hosted_commands_with_invalid_utf8_preserve_decode_holds(self) -> None:
        for returncode in (1, 128):
            with self.subTest(returncode=returncode):
                self._check_cli_unusable_hosted_output(
                    returncode=returncode, modes=("decode",),
                )

    def _check_cli_unusable_hosted_output(
        self, *, returncode: int, modes: tuple[str, ...] = ("decode", "oversized"),
    ) -> None:
        real_git = shutil.which("git")
        self.assertIsNotNone(real_git)
        for mode, stream in ((mode, stream) for mode in modes for stream in ("stdout", "stderr")):
            reason = (
                "output could not be decoded" if mode == "decode"
                else f"output exceeded {audit_repo.HOSTED_COMMAND_MAX_OUTPUT_BYTES} bytes"
            )
            with self.subTest(mode=mode, stream=stream), tempfile.TemporaryDirectory() as directory:
                fixture = Path(directory)
                root = fixture / "repo"
                root.mkdir()
                self._init_repo(root)
                bin_dir = fixture / "bin"
                bin_dir.mkdir()
                calls_path = fixture / "calls.jsonl"
                # Only allow known local Git reads through to the real executable.
                # All hosted commands are handled here, with no network fallback.
                executable = (
                    f"#!{sys.executable}\n"
                    "import json, locale, os, sys\n"
                    "from pathlib import Path\n"
                    f"REAL_GIT = {real_git!r}\n"
                    r'''
args = sys.argv[1:]
tool = Path(sys.argv[0]).name
assert sys.flags.utf8_mode == 0
assert locale.getpreferredencoding(False).lower() in ("ascii", "ansi_x3.4-1968")
with open(os.environ["CLI_FIXTURE_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps([tool, *args]) + "\n")
bad = False
if tool == "git":
    if args[:2] == ["remote", "get-url"]:
        assert args[2] in ("origin", "mirror"), args
        output = "https://github.com/fixture/" + args[2] + ".git\n"
    elif args[:2] == ["ls-remote", "--heads"]:
        assert args[2] in ("origin", "mirror"), args
        output = "a" * 40 + "\t" + args[3] + "\n"
        bad = args[2:] == ["origin", "refs/heads/unreadable"]
    elif args[0] in ("rev-parse", "branch", "for-each-ref", "log"):
        os.execv(REAL_GIT, [REAL_GIT, *args])
    else:
        raise AssertionError(args)
else:
    assert tool == "gh" and args[0] == "api", (tool, args)
    endpoint = args[1]
    if "/branches/" in endpoint:
        output = '{"protected": false}\n'
        bad = endpoint == "repos/fixture/origin/branches/work"
    elif "/deployments?" in endpoint:
        output = "[]\n"
        bad = endpoint.startswith("repos/fixture/origin/deployments?ref=work&")
    elif "/pulls?" in endpoint:
        output = "[]\n"
        bad = endpoint.startswith("repos/fixture/origin/pulls?state=all&head=fixture%3Awork&")
    else:
        raise AssertionError(args)
sys.stdout.buffer.write(output.encode("utf-8"))
if bad:
    payload = b"\xff" if os.environ["CLI_FIXTURE_FAILURE"] == "decode" else b"x" * (4 * 1024 * 1024 + 1)
    getattr(sys, os.environ["CLI_FIXTURE_STREAM"]).buffer.write(payload)
    sys.exit(int(os.environ["CLI_FIXTURE_RETURNCODE"]))
'''
                )
                for tool in ("git", "gh"):
                    path = bin_dir / tool
                    path.write_text(executable, encoding="utf-8")
                    path.chmod(0o755)
                result = subprocess.run([
                    sys.executable, str(SCRIPT), "--root", str(root), "--base", "main",
                    "--hosted-ref", "origin=unreadable",
                    "--hosted-ref", "origin=work",
                    "--hosted-ref", "mirror=work",
                    "--hosted-ref", "origin=later",
                ], capture_output=True, timeout=30, env={
                    "PATH": str(bin_dir),
                    **ASCII_LOCALE,
                    "CLI_FIXTURE_LOG": str(calls_path),
                    "CLI_FIXTURE_STREAM": stream,
                    "CLI_FIXTURE_FAILURE": mode,
                    "CLI_FIXTURE_RETURNCODE": str(returncode),
                })
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, b"")
                report = json.loads(result.stdout.decode("utf-8"))
                self.assertNotIn("error", report)
                hosted = report["hosted_lifecycle"]
                entries = hosted["entries"]
                self.assertEqual([(entry["provider"], entry["ref"]) for entry in entries], [
                    ("origin", "unreadable"), ("origin", "work"),
                    ("mirror", "work"), ("origin", "later"),
                ])
                remote, api = entries[:2]
                self.assertEqual(remote["classification"], "inaccessible")
                self.assertEqual(remote["ref_status"], "unknown")
                self.assertNotIn("tip", remote)
                self.assertIn(reason, remote["reason"])
                if mode == "decode":
                    self.assertIn("utf-8", remote["reason"])
                self.assertEqual(remote["blocking_reasons"], ["hosted-remote-inaccessible"])
                api_codes = sorted([
                    "hosted-protection-unknown", "hosted-deployment-evidence-unknown",
                    "hosted-pull-request-evidence-unknown",
                ])
                self.assertEqual(api["classification"], "present")
                self.assertEqual(api["ref_status"], "present")
                self.assertEqual(api["blocking_reasons"], api_codes)
                for entry in (remote, api):
                    self.assertTrue(entry["deletion_blocked"])
                    for key in ("protection", "deployments", "pull_requests"):
                        self.assertEqual(entry[key]["status"], "unknown")
                        self.assertNotIn("count", entry[key])
                        self.assertNotIn("items", entry[key])
                        if entry is api:
                            self.assertIn(reason, entry[key]["reason"])
                            self.assertNotIn("request failed", entry[key]["reason"])
                            if mode == "decode":
                                self.assertIn("utf-8", entry[key]["reason"])
                for later in entries[2:]:
                    self.assertEqual(later["classification"], "present")
                    self.assertFalse(later["deletion_blocked"])
                    self.assertEqual(later["blocking_reasons"], [])
                    self.assertEqual(later["protection"]["status"], "unprotected")
                    for key in ("deployments", "pull_requests"):
                        self.assertEqual(later[key]["status"], "available")
                        self.assertEqual(later[key]["count"], 0)
                self.assertTrue(hosted["deletion_blocked"])
                self.assertEqual(hosted["blocking_entries"], ["origin:unreadable", "origin:work"])
                plan = hosted["cleanup_plan"]
                self.assertEqual(plan["keep"], [])
                self.assertEqual(plan["delete"], [])
                self.assertEqual(plan["merge"], [])
                self.assertEqual([
                    (item["provider"], item["ref"], item["blocking_reasons"])
                    for item in plan["review"]
                ], [
                    ("origin", "unreadable", ["hosted-remote-inaccessible"]),
                    ("origin", "work", api_codes),
                ])
                for item in plan["review"]:
                    self.assertEqual(item["blocking_reason_explanations"], [
                        {"reason_code": code, "explanation": audit_repo.HOSTED_HOLD_EXPLANATIONS[code]}
                        for code in item["blocking_reasons"]
                    ])
                self.assertNotIn("deletion_commands", result.stdout.decode("utf-8"))
                self.assertNotIn('"bucket": "delete"', result.stdout.decode("utf-8"))
                calls = [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines()]
                self.assertEqual([call[3:] for call in calls if call[:3] == [
                    "git", "ls-remote", "--heads",
                ]], [
                    ["origin", "refs/heads/unreadable"], ["origin", "refs/heads/work"],
                    ["mirror", "refs/heads/work"], ["origin", "refs/heads/later"],
                ])
                self.assertEqual([call[1:] for call in calls if call[0] == "gh"], [
                    ["api", endpoint]
                    for provider, branch in (("origin", "work"), ("mirror", "work"), ("origin", "later"))
                    for endpoint in (
                        f"repos/fixture/{provider}/branches/{branch}",
                        f"repos/fixture/{provider}/deployments?ref={branch}&per_page=100&page=1",
                        f"repos/fixture/{provider}/pulls?state=all&head=fixture%3A{branch}&per_page=100&page=1",
                    )
                ])

    def test_hosted_output_limit_counts_combined_bytes_and_accepts_exact_limit(self) -> None:
        limit = 128 * 1024
        for stdout_size, stderr_size in (
            (limit, 0), (0, limit), (limit // 2, limit // 2),
            (limit + 1, 0), (0, limit + 1), (limit // 2, limit // 2 + 1),
        ):
            with self.subTest(stdout=stdout_size, stderr=stderr_size), patch.object(
                audit_repo, "HOSTED_COMMAND_MAX_OUTPUT_BYTES", limit,
            ):
                args = [
                    sys.executable, "-c",
                    f"import sys; sys.stdout.buffer.write(b'a' * {stdout_size}); "
                    f"sys.stderr.buffer.write(b'b' * {stderr_size})",
                ]
                if stdout_size + stderr_size <= limit:
                    result = audit_repo.hosted_command(args, Path("."))
                    self.assertEqual(result.returncode, 0)
                    self.assertEqual(result.stdout, "a" * stdout_size)
                    self.assertEqual(result.stderr, "b" * stderr_size)
                else:
                    with self.assertRaises(audit_repo.HostedOutputLimitExceeded) as caught:
                        audit_repo.hosted_command(args, Path("."))
                    self.assertIn(f"exceeded {limit} bytes", str(caught.exception))
                    self.assertIn("output discarded", str(caught.exception))
                    self.assertFalse(hasattr(caught.exception, "stdout"))
                    self.assertFalse(hasattr(caught.exception, "stderr"))

    def test_hosted_output_limit_stops_continuously_noisy_processes(self) -> None:
        real_popen = subprocess.Popen
        for stream in ("stdout", "stderr", "both"):
            script = "import os\nwhile True:\n"
            if stream in ("stdout", "both"):
                script += " os.write(1, b'x' * 4096)\n"
            if stream in ("stderr", "both"):
                script += " os.write(2, b'x' * 4096)\n"
            children = []

            def popen(*args, **kwargs):
                child = real_popen(*args, **kwargs)
                children.append(child)
                return child

            with self.subTest(stream=stream), patch.object(
                audit_repo, "HOSTED_COMMAND_MAX_OUTPUT_BYTES", 16384,
            ), patch.object(audit_repo, "HOSTED_COMMAND_TIMEOUT_SECONDS", 3), patch.object(
                audit_repo.subprocess, "Popen", side_effect=popen,
            ):
                with self.assertRaises(audit_repo.HostedOutputLimitExceeded):
                    audit_repo.hosted_command([sys.executable, "-c", script], Path("."))
            self.assertEqual(len(children), 1)
            self.assertIsNotNone(children[0].returncode)
            self.assertTrue(children[0].stdout.closed)
            self.assertTrue(children[0].stderr.closed)

    def test_oversized_output_preserves_only_affected_hold_and_continues(self) -> None:
        real_hosted_command = audit_repo.hosted_command
        keys = ("protection", "deployments", "pull_requests")
        codes = (
            "hosted-remote-inaccessible", "hosted-protection-unknown",
            "hosted-deployment-evidence-unknown", "hosted-pull-request-evidence-unknown",
        )
        for stream in ("stdout", "stderr"):
            for failed in range(4):
                calls = []

                def command(args, root):
                    calls.append(args)
                    if args[0] == "git":
                        output = f"{'a' * 40}\t{args[-1]}\n"
                    else:
                        output = '{"protected": false}' if "/branches/" in args[2] else "[]"
                    if len(calls) - 1 == failed:
                        # Emit plausible evidence before overflowing either pipe.
                        # The actual bounded reader must reject the whole result.
                        return real_hosted_command([
                            sys.executable, "-c",
                            f"import sys; sys.stdout.write({output!r}); sys.stdout.flush(); "
                            f"sys.{stream}.buffer.write(b' ' * 257)",
                        ], root)
                    return subprocess.CompletedProcess(args, 0, output, "")

                with self.subTest(stream=stream, failed=codes[failed]), patch.object(
                    audit_repo, "HOSTED_COMMAND_MAX_OUTPUT_BYTES", 256,
                ), patch.object(
                    audit_repo, "remote_url_for_provider",
                    side_effect=lambda _root, provider: (provider, "https://github.com/fixture/repo.git"),
                ), patch.object(audit_repo.shutil, "which", return_value="/fixture/gh"), patch.object(
                    audit_repo, "hosted_command", side_effect=command,
                ):
                    report = audit_repo.audit_hosted_branches(
                        Path("."), ["origin=work", "mirror=work", "origin=later"],
                    )
                first = report["entries"][0]
                self.assertTrue(first["deletion_blocked"])
                self.assertEqual(first["classification"], "inaccessible" if failed == 0 else "present")
                self.assertEqual(first["ref_status"], "unknown" if failed == 0 else "present")
                reason = first["reason"] if failed == 0 else first[keys[failed - 1]]["reason"]
                self.assertIn("exceeded 256 bytes", reason)
                self.assertIn("output discarded", reason)
                for index, key in enumerate(keys, 1):
                    if failed in (0, index):
                        self.assertEqual(first[key]["status"], "unknown")
                        self.assertNotIn("count", first[key])
                        self.assertNotIn("items", first[key])
                    else:
                        self.assertEqual(first[key]["status"], "unprotected" if index == 1 else "available")
                for later in report["entries"][1:]:
                    self.assertEqual(later["classification"], "present")
                    self.assertFalse(later["deletion_blocked"])
                self.assertEqual(report["blocking_entries"], ["origin:work"])
                self.assert_single_hosted_hold(report, codes[failed], "review")
                self.assertEqual(report["cleanup_plan"]["delete"], [])
                self.assertEqual(report["cleanup_plan"]["merge"], [])
                self.assertEqual(len(calls), 9 if failed == 0 else 12)

    def test_hosted_commands_have_a_finite_timeout(self) -> None:
        self.assertEqual(audit_repo.HOSTED_COMMAND_TIMEOUT_SECONDS, 30)
        for script in (
            "import time; time.sleep(10)",
            "import os, time; os.close(1); os.close(2); time.sleep(10)",
        ):
            with self.subTest(script=script), patch.object(
                audit_repo, "HOSTED_COMMAND_TIMEOUT_SECONDS", 0.1,
            ):
                with self.assertRaises(subprocess.TimeoutExpired) as caught:
                    audit_repo.hosted_command([sys.executable, "-c", script], Path("."))
                self.assertIsNone(caught.exception.output)
                self.assertIsNone(caught.exception.stderr)

    @unittest.skipUnless(sys.platform == "linux", "uses Linux orphan adoption and /proc")
    def test_interrupted_hosted_reader_kills_helpers_and_propagates_interrupt(self) -> None:
        worker = """
import ctypes, runpy, sys
libc = ctypes.CDLL(None, use_errno=True)
if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
    raise OSError(ctypes.get_errno(), "could not enable child subreaper")
namespace = runpy.run_path(sys.argv[1])
namespace["AuditRepoTests"]().check_hosted_descendant_pipe_case(
    "interrupt", sys.argv[2], int(sys.argv[3]),
)
"""
        ready_read, ready_write = os.pipe()
        try:
            with tempfile.TemporaryDirectory() as directory, subprocess.Popen(
                [sys.executable, "-c", worker, str(Path(__file__).resolve()),
                 directory, str(ready_write)],
                pass_fds=(ready_write,), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                start_new_session=True,
            ) as process:
                os.close(ready_write)
                ready_write = None
                try:
                    # Independent outer deadlines also bound regressions in the
                    # reader's timeout, interrupt handler, and cleanup wait().
                    with audit_repo.selectors.DefaultSelector() as selector:
                        selector.register(ready_read, audit_repo.selectors.EVENT_READ)
                        self.assertTrue(selector.select(10), "audit reader never became ready")
                        self.assertEqual(os.read(ready_read, 1), b"R")
                    # Signal only the audit worker, not its command's separate
                    # session: command/helper deaths must come from audit cleanup.
                    os.kill(process.pid, signal.SIGINT)
                    stdout, stderr = process.communicate(timeout=10)
                    self.assertEqual(
                        process.returncode, 0,
                        (stdout + stderr).decode("utf-8", errors="replace"),
                    )
                finally:
                    # Recover fixtures even if interruption is swallowed or the
                    # worker gets stuck. Give its finally block time to reap.
                    pid_file = Path(directory) / "parent-pid"
                    if process.poll() is None:
                        if pid_file.exists():
                            try:
                                os.killpg(int(pid_file.read_text()), signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                        try:
                            process.communicate(timeout=5)
                        except subprocess.TimeoutExpired:
                            os.killpg(process.pid, signal.SIGKILL)
                            process.communicate(timeout=5)
        finally:
            os.close(ready_read)
            if ready_write is not None:
                os.close(ready_write)

    @unittest.skipUnless(sys.platform == "linux", "uses Linux orphan adoption and /proc")
    def test_hosted_descendant_owned_pipes_are_cleaned_up_and_later_pairs_run(self) -> None:
        for mode in (
            "exited-parent", "timeout", "stdout-limit", "stderr-limit",
            "stdout-decode", "stderr-decode",
        ):
            with self.subTest(mode=mode):
                # Isolate orphan adoption from the test runner. The worker is a
                # subreaper so even helpers killed after their parent exits are
                # collected, rather than left to the container's PID 1.
                worker = """
import ctypes, runpy, sys
libc = ctypes.CDLL(None, use_errno=True)
if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
    raise OSError(ctypes.get_errno(), "could not enable child subreaper")
namespace = runpy.run_path(sys.argv[1])
namespace["AuditRepoTests"]().check_hosted_descendant_pipe_case(sys.argv[2], sys.argv[3])
"""
                real_popen = subprocess.Popen
                with tempfile.TemporaryDirectory() as directory, real_popen(
                    [sys.executable, "-c", worker, str(Path(__file__).resolve()), mode, directory],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    start_new_session=True,
                ) as process:
                    try:
                        stdout, stderr = process.communicate(timeout=15)
                    except subprocess.TimeoutExpired:
                        # The hosted parent has its own session. Kill that group
                        # first so the worker can unwind and reap its helper.
                        pid_file = Path(directory) / "parent-pid"
                        if pid_file.exists():
                            try:
                                os.killpg(int(pid_file.read_text()), signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                        process.communicate(timeout=5)
                        raise
                    finally:
                        # Independent watchdog: a regressed reader or wait()
                        # must fail the test, not hang the whole suite.
                        if process.poll() is None:
                            os.killpg(process.pid, signal.SIGKILL)
                            process.wait()
                    self.assertEqual(
                        process.returncode, 0,
                        (stdout + stderr).decode("utf-8", errors="replace"),
                    )

    def check_hosted_descendant_pipe_case(
        self, mode: str, directory: str, ready_fd: int | None = None,
    ) -> None:
        """Worker-only fixture: all hosted calls are replaced by local Python."""
        real_hosted_command = audit_repo.hosted_command
        real_popen = subprocess.Popen
        real_read = os.read
        real_selector = audit_repo.selectors.DefaultSelector
        children = []
        calls = []
        captured_streams = set()
        timeout = 2
        limit = 256
        parent_exits = mode == "exited-parent" or mode.endswith("-decode")
        with tempfile.TemporaryDirectory(dir=directory) as fixture_directory:
            root = Path(fixture_directory)
            helper_pid_file = root / "helper-pid"
            helper_script = """
import os, sys, time
from pathlib import Path
# Assert the helper really owns both inherited capture pipes, then acknowledge
# readiness over a separate pipe (never through the evidence under test).
assert os.fstat(1).st_ino == int(sys.argv[3])
assert os.fstat(2).st_ino == int(sys.argv[4])
if sys.argv[5].endswith("-decode"):
    # A live helper must not delay EOF: decoding happens only after both
    # captured streams finish and the direct parent has been reaped.
    os.close(1)
    os.close(2)
Path(sys.argv[1]).write_text(str(os.getpid()))
os.write(int(sys.argv[2]), b"R")
os.close(int(sys.argv[2]))
time.sleep(60)
"""
            parent_script = """
import os, subprocess, sys, time
ready_read, ready_write = os.pipe()
helper = subprocess.Popen(
    [sys.executable, "-c", sys.argv[1], sys.argv[2], str(ready_write),
     str(os.fstat(1).st_ino), str(os.fstat(2).st_ino), sys.argv[3]],
    pass_fds=(ready_write,),
)
os.close(ready_write)
assert os.read(ready_read, 1) == b"R"
os.close(ready_read)
# Plausible complete evidence must not survive an unfinished pipe capture.
os.write(1, sys.argv[4].encode())
if sys.argv[3] == "interrupt":
    os.write(2, b"helper owns stderr too")
if sys.argv[3].endswith("-decode"):
    fd = 1 if sys.argv[3] == "stdout-decode" else 2
    os.write(fd, b"\\xff")
    sys.exit(0)
if sys.argv[3] == "exited-parent":
    sys.exit(0)
if sys.argv[3].endswith("-limit"):
    fd = 1 if sys.argv[3] == "stdout-limit" else 2
    while True:
        os.write(fd, b" " * 4096)
time.sleep(60)
"""

            def popen(*args, **kwargs):
                child = real_popen(*args, **kwargs)
                children.append(child)
                (Path(directory) / "parent-pid").write_text(str(child.pid))
                if parent_exits:
                    # Ensure the direct child has already exited successfully
                    # before the real hosted reader receives the process.
                    self.assertEqual(child.wait(timeout=5), 0)
                    with self.assertRaises(ChildProcessError):
                        os.waitpid(child.pid, os.WNOHANG)
                    helper_pid = int(helper_pid_file.read_text())
                    self.assertEqual(os.waitpid(helper_pid, os.WNOHANG), (0, 0))
                    if mode.endswith("-decode"):
                        for fd in (1, 2):
                            self.assertFalse(Path(f"/proc/{helper_pid}/fd/{fd}").exists())
                return child

            def read(fd, size):
                chunk = real_read(fd, size)
                if mode == "interrupt" and chunk:
                    child = children[0]
                    self.assertIn(fd, (child.stdout.fileno(), child.stderr.fileno()))
                    captured_streams.add(fd)
                return chunk

            class ReadySelector(real_selector):
                def select(selector, timeout=None):
                    nonlocal ready_fd
                    if mode == "interrupt" and len(captured_streams) == 2 and ready_fd is not None:
                        # Both real pipes have been read after the helper's
                        # ownership handshake; neither writer has exited.
                        self.assertIsNone(children[0].poll())
                        helper_pid = int(helper_pid_file.read_text())
                        self.assertTrue(Path(f"/proc/{helper_pid}/fd/1").exists())
                        self.assertTrue(Path(f"/proc/{helper_pid}/fd/2").exists())
                        os.kill(helper_pid, 0)
                        notify_fd = ready_fd
                        ready_fd = None
                        try:
                            os.write(notify_fd, b"R")
                        finally:
                            os.close(notify_fd)
                    return super().select(timeout)

            def assert_cleaned_up():
                self.assertEqual(len(children), 1)
                child = children[0]
                self.assertEqual(
                    child.returncode, 0 if parent_exits else -signal.SIGKILL,
                )
                # returncode alone can be cached; prove waitpid already reaped it.
                with self.assertRaises(ChildProcessError):
                    os.waitpid(child.pid, os.WNOHANG)
                self.assertTrue(child.stdout.closed)
                self.assertTrue(child.stderr.closed)
                helper_pid = int(helper_pid_file.read_text())
                deadline = time.monotonic() + 2
                while True:
                    pid, status = os.waitpid(helper_pid, os.WNOHANG)
                    if pid:
                        self.assertTrue(os.WIFSIGNALED(status))
                        self.assertEqual(os.WTERMSIG(status), signal.SIGKILL)
                        break
                    self.assertLess(time.monotonic(), deadline, "helper survived group cleanup")
                    time.sleep(0.01)
                with self.assertRaises(ChildProcessError):
                    os.waitpid(helper_pid, os.WNOHANG)

            def command(args, cwd):
                calls.append(args)
                if args[0] == "git":
                    output = f"{'a' * 40}\t{args[-1]}\n"
                else:
                    self.assertEqual(args[:2], ["gh", "api"])
                    output = '{"protected": false}' if "/branches/" in args[2] else "[]"
                if len(calls) == 1:
                    return real_hosted_command([
                        sys.executable, "-c", parent_script, helper_script,
                        str(helper_pid_file), mode, output,
                    ], cwd)
                if len(calls) == 2:
                    # Cleanup must be complete before the next pair starts,
                    # not merely deferred until the audit or test finishes.
                    assert_cleaned_up()
                return subprocess.CompletedProcess(args, 0, output, "")

            try:
                with patch.object(
                    audit_repo, "HOSTED_COMMAND_TIMEOUT_SECONDS", 60 if mode == "interrupt" else timeout,
                ), patch.object(
                    audit_repo, "HOSTED_COMMAND_MAX_OUTPUT_BYTES", limit,
                ), patch.object(
                    audit_repo, "remote_url_for_provider",
                    side_effect=lambda _root, provider: (
                        provider, "https://github.com/fixture/repo.git",
                    ),
                ), patch.object(
                    audit_repo.shutil, "which", return_value="/fixture/gh",
                ), patch.object(
                    audit_repo.subprocess, "Popen", side_effect=popen,
                ), patch.object(
                    audit_repo.os, "read", side_effect=read,
                ), patch.object(
                    audit_repo.selectors, "DefaultSelector", ReadySelector,
                ), patch.object(audit_repo, "hosted_command", side_effect=command):
                    if mode == "interrupt":
                        # Interruption must escape the public hosted audit,
                        # never return partial evidence or an ordinary hold.
                        with self.assertRaises(KeyboardInterrupt):
                            audit_repo.audit_hosted_branches(
                                root, ["origin=work", "origin=later"],
                            )
                        self.assertEqual(len(calls), 1, "audit continued after interruption")
                        self.assertIsNone(ready_fd, "interrupted before reader readiness")
                        assert_cleaned_up()
                        return
                    started = time.monotonic()
                    report = audit_repo.audit_hosted_branches(
                        root, ["origin=work", "mirror=work", "origin=later"],
                    )
                    self.assertLess(time.monotonic() - started, 8)
                self.assertEqual(len(calls), 9)
                self.assertEqual(
                    [(entry["provider"], entry["ref"]) for entry in report["entries"]],
                    [("origin", "work"), ("mirror", "work"), ("origin", "later")],
                )
                first, *later = report["entries"]
                self.assertEqual(first["classification"], "inaccessible")
                self.assertEqual(first["ref_status"], "unknown")
                self.assertNotIn("tip", first)
                reason = (
                    f"exceeded {limit} bytes" if mode.endswith("-limit")
                    else "output could not be decoded" if mode.endswith("-decode")
                    else f"timed out after {timeout} seconds"
                )
                self.assertIn(reason, first["reason"])
                if mode.endswith("-limit"):
                    self.assertIn("output discarded", first["reason"])
                for key in ("protection", "deployments", "pull_requests"):
                    self.assertEqual(first[key]["status"], "unknown")
                for entry in later:
                    self.assertEqual(entry["classification"], "present")
                    self.assertFalse(entry["deletion_blocked"])
                self.assertEqual(report["blocking_entries"], ["origin:work"])
                self.assert_single_hosted_hold(report, "hosted-remote-inaccessible", "review")
                self.assertEqual(report["cleanup_plan"]["delete"], [])
                self.assertEqual(report["cleanup_plan"]["merge"], [])
            finally:
                # Always recover local fixtures, including after assertions or
                # watchdog failures. The audit's parent has a separate session.
                for child in children:
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    child.wait()
                    child.stdout.close()
                    child.stderr.close()
                if helper_pid_file.exists():
                    try:
                        os.waitpid(int(helper_pid_file.read_text()), 0)
                    except ChildProcessError:
                        pass

    def test_remote_timeout_holds_and_continues_without_trusting_partial_output(self) -> None:
        for partial_output in (None, b"", b"aaaaaaaa\trefs/heads/work\n"):
            def command(args, _root):
                if args[3] == "stalled":
                    raise subprocess.TimeoutExpired(args, 30, output=partial_output)
                return subprocess.CompletedProcess(args, 0, "", "")

            with self.subTest(partial_output=partial_output), patch.object(
                audit_repo, "remote_url_for_provider",
                side_effect=lambda _root, provider: (provider, "https://github.com/fixture/repo.git"),
            ), patch.object(audit_repo, "hosted_command", side_effect=command) as run, patch.object(
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

            def command(args, _root):
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
                audit_repo, "hosted_command", side_effect=command,
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
        real_popen = subprocess.Popen
        commands = (
            "ssh", "", "ssh -o BatchMode=no", "ssh -oBatchMode no",
            "ssh -o batchmode=no -o BatchMode=yes",
        )
        for command in commands:
            with self.subTest(command=command), patch.dict(
                audit_repo.os.environ, {"GIT_SSH_COMMAND": command}, clear=True
            ), patch.object(audit_repo.subprocess, "Popen", wraps=real_popen) as popen:
                audit_repo.hosted_command([sys.executable, "-c", "pass"], Path("."))
                options = popen.call_args.kwargs
                self.assertEqual(options["env"]["GIT_TERMINAL_PROMPT"], "0")
                self.assertEqual(options["stdin"], subprocess.DEVNULL)
                normalized = options["env"]["GIT_SSH_COMMAND"].lower()
                self.assertNotIn("batchmode=no", normalized)
                self.assertIn("batchmode=yes", normalized)

    def hosted_pages_fixture(
        self, deployment_pages, pr_pages, branch="feature/work",
        branch_response='{"protected": false}',
    ):
        """Exercise the real JSON adapter and hold projection without network access."""
        encoded_branch = quote(branch, safe="")
        encoded_head = quote(f"fixture:{branch}", safe="")
        deployment_endpoint = f"repos/fixture/repo/deployments?ref={encoded_branch}"
        pr_endpoint = f"repos/fixture/repo/pulls?state=all&head={encoded_head}"
        fixtures = {f"repos/fixture/repo/branches/{encoded_branch}": branch_response}
        for endpoint, pages in ((deployment_endpoint, deployment_pages), (pr_endpoint, pr_pages)):
            fixtures.update({
                f"{endpoint}&per_page=100&page={page}": data
                for page, data in enumerate(pages, 1)
            })
        calls = []

        def command(args, _root):
            if args[0] == "git":
                return subprocess.CompletedProcess(
                    args, 0, f"{'a' * 40}\trefs/heads/{branch}\n", "",
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
            report = audit_repo.audit_hosted_branches(Path("."), [f"origin={branch}"])
        self.assertEqual(calls, list(fixtures))
        self.assertEqual(report["cleanup_plan"]["delete"], [])
        self.assertEqual(report["cleanup_plan"]["merge"], [])
        return report

    def test_unrelated_protection_names_keep_cleanup_blocked(self) -> None:
        for name in (
            "other", "feature/work-extra", "Feature/work",
            "refs/heads/feature/work", "refs/tags/feature/work", " feature/work",
            "feature/work ", "a" * 40,
        ):
            for protected in (False, True):
                with self.subTest(name=name, protected=protected):
                    report = self.hosted_pages_fixture(
                        [[]], [[]], branch_response={"name": name, "protected": protected},
                    )
                    entry = report["entries"][0]
                    self.assertEqual(entry["protection"], {
                        "status": "unknown",
                        "reason": "GitHub branch response: "
                                  "branch association does not match the requested branch",
                    })
                    self.assertEqual(entry["deployments"]["status"], "available")
                    self.assertEqual(entry["pull_requests"]["status"], "available")
                    self.assert_single_hosted_hold(report, "hosted-protection-unknown", "review")

    def test_cli_wrong_branch_protection_keeps_cleanup_blocked(self) -> None:
        real_git = shutil.which("git")
        self.assertIsNotNone(real_git)
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory)
            root = fixture / "repo"
            root.mkdir()
            self._init_repo(root)
            branch = "feature/work"
            self._git(root, "branch", branch)
            tip = self._git(root, "rev-parse", "HEAD").strip()
            remote = fixture / "origin.git"
            self._git(root, "clone", "-q", "--bare", str(root), str(remote))
            self._git(root, "remote", "add", "origin",
                      "https://github.com/fixture/repo.git")
            bin_dir = fixture / "bin"
            bin_dir.mkdir()
            # Report the real hosted URL, but use a real local remote for the
            # ref probe. All other Git commands run unchanged, without network.
            git_path = bin_dir / "git"
            git_path.write_text(
                f"#!{sys.executable}\n"
                "import os, sys\n"
                f"real_git = {real_git!r}\n"
                "args = sys.argv[1:]\n"
                "if args[:3] == ['ls-remote', '--heads', 'origin']:\n"
                f"    args[2] = {str(remote)!r}\n"
                "os.execv(real_git, [real_git, *args])\n",
                encoding="utf-8",
            )
            git_path.chmod(0o755)
            calls_path = fixture / "calls.jsonl"
            responses = {
                "repos/fixture/repo/branches/feature%2Fwork": {
                    "name": "feature/other", "protected": False,
                },
                "repos/fixture/repo/deployments?ref=feature%2Fwork"
                "&per_page=100&page=1": [],
                "repos/fixture/repo/pulls?state=all&head=fixture%3Afeature%2Fwork"
                "&per_page=100&page=1": [],
            }
            gh_path = bin_dir / "gh"
            gh_path.write_text(
                f"#!{sys.executable}\n"
                "import json, sys\n"
                "assert sys.argv[1] == 'api' and len(sys.argv) == 3, sys.argv\n"
                "endpoint = sys.argv[2]\n"
                f"with open({str(calls_path)!r}, 'a', encoding='utf-8') as log:\n"
                "    log.write(json.dumps(endpoint) + '\\n')\n"
                f"responses = {responses!r}\n"
                # An unexpected endpoint fails rather than returning empty data.
                "print(json.dumps(responses[endpoint]))\n",
                encoding="utf-8",
            )
            gh_path.chmod(0o755)
            result = subprocess.run([
                sys.executable, str(SCRIPT), "--root", str(root), "--base", "main",
                "--hosted-branch", f"origin={branch}",
            ], capture_output=True, timeout=30, env={
                "PATH": str(bin_dir), "HOME": str(fixture),
                "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_ALLOW_PROTOCOL": "file", "GIT_TERMINAL_PROMPT": "0",
                "PYTHONUTF8": "1",
            })
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, b"")
            report = json.loads(result.stdout)
            self.assertNotIn("error", report)
            self.assertFalse(report["fetch_performed"])
            hosted = report["hosted_lifecycle"]
            self.assertEqual(len(hosted["entries"]), 1)
            entry = hosted["entries"][0]
            self.assertEqual((entry["provider"], entry["ref"]), ("origin", branch))
            self.assertEqual(entry["classification"], "present")
            self.assertEqual(entry["ref_status"], "present")
            self.assertEqual(entry["tip"], tip)
            self.assertEqual(entry["protection"], {
                "status": "unknown",
                "reason": "GitHub branch response: "
                          "branch association does not match the requested branch",
            })
            for key in ("deployments", "pull_requests"):
                self.assertEqual(entry[key], {
                    "status": "available", "source": "github-api",
                    "count": 0, "items": [],
                })
            self.assertTrue(entry["deletion_blocked"])
            self.assertEqual(entry["blocking_reasons"], ["hosted-protection-unknown"])
            self.assertTrue(hosted["deletion_blocked"])
            self.assertEqual(hosted["blocking_entries"], [f"origin:{branch}"])
            self.assertEqual(hosted["cleanup_plan"], {
                "merge": [], "delete": [], "keep": [],
                "review": [{
                    "provider": "origin", "ref": branch,
                    "blocking_reasons": ["hosted-protection-unknown"],
                    "blocking_reason_explanations": [{
                        "reason_code": "hosted-protection-unknown",
                        "explanation": "Branch protection could not be confirmed; "
                                       "check protection before reviewing deletion.",
                    }],
                }],
            })
            self.assertEqual(
                [json.loads(line) for line in calls_path.read_text().splitlines()],
                list(responses),
            )

    def test_invalid_supplied_protection_names_stay_unknown(self) -> None:
        for name in (None, "", " \t", False, 4, [], {}):
            for protected in (False, True):
                with self.subTest(name=name, protected=protected):
                    report = self.hosted_pages_fixture(
                        [[]], [[]], branch_response={"name": name, "protected": protected},
                    )
                    self.assertEqual(report["entries"][0]["protection"], {
                        "status": "unknown",
                        "reason": "GitHub branch response: "
                                  "missing or invalid branch association metadata",
                    })
                    self.assert_single_hosted_hold(report, "hosted-protection-unknown", "review")

    def test_matching_and_absent_protection_names_preserve_boolean_results(self) -> None:
        for identity in ({}, {"name": "feature/work"}):
            for protected in (False, True):
                with self.subTest(identity=identity, protected=protected):
                    report = self.hosted_pages_fixture(
                        [[]], [[]], branch_response={**identity, "protected": protected},
                    )
                    self.assertEqual(report["entries"][0]["protection"], {
                        "status": "protected" if protected else "unprotected",
                        "source": "github-api", "repository": "fixture/repo",
                        "ref": "feature/work",
                    })
                    if protected:
                        self.assert_single_hosted_hold(report, "hosted-ref-protected", "keep")
                    else:
                        self.assertFalse(report["deletion_blocked"])
                        self.assertEqual(report["entries"][0]["blocking_reasons"], [])

    def test_protection_names_require_exact_unicode_spelling(self) -> None:
        composed, decomposed = "feature/caf\u00e9", "feature/cafe\u0301"
        for branch, other in ((composed, decomposed), (decomposed, composed)):
            for name in (branch, other):
                with self.subTest(branch=branch, name=name):
                    report = self.hosted_pages_fixture(
                        [[]], [[]], branch=branch,
                        branch_response={"name": name, "protected": False},
                    )
                    if name == branch:
                        self.assertEqual(report["entries"][0]["protection"]["status"], "unprotected")
                        self.assertFalse(report["deletion_blocked"])
                    else:
                        self.assert_single_hosted_hold(report, "hosted-protection-unknown", "review")

    def test_unusable_protection_status_keeps_existing_explanations(self) -> None:
        responses = [([], "GitHub branch response was not an object"),
                     (None, "GitHub branch response was not an object")]
        for identity in ({}, {"name": "feature/work"}):
            responses.append((identity, "GitHub branch response did not include protection status"))
            responses.extend(
                ({**identity, "protected": value},
                 "GitHub branch response did not include protection status")
                for value in (None, 0, 1, "false", [], {})
            )
        for response, reason in responses:
            with self.subTest(response=response):
                report = self.hosted_pages_fixture([[]], [[]], branch_response=response)
                entry = report["entries"][0]
                self.assertEqual(entry["protection"], {"status": "unknown", "reason": reason})
                self.assertEqual(entry["deployments"]["status"], "available")
                self.assertEqual(entry["pull_requests"]["status"], "available")
                self.assert_single_hosted_hold(report, "hosted-protection-unknown", "review")

    def test_bad_protection_association_does_not_hide_other_holds(self) -> None:
        report = self.hosted_pages_fixture(
            [[{"id": 1, "ref": "feature/work"}]],
            [[pull_request_fixture(state="open", merged_at=None)]],
            branch_response={"name": "other", "protected": False},
        )
        entry = report["entries"][0]
        self.assertEqual(entry["protection"]["status"], "unknown")
        self.assertEqual(entry["deployments"]["count"], 1)
        self.assertEqual(entry["pull_requests"]["count"], 1)
        self.assertEqual(entry["blocking_reasons"], [
            "hosted-open-pull-request", "hosted-protection-unknown",
            "hosted-ref-has-deployments",
        ])
        self.assertEqual(report["cleanup_plan"]["keep"][0]["blocking_reasons"],
                         entry["blocking_reasons"])
        self.assertTrue(report["deletion_blocked"])

    def test_bad_protection_association_does_not_hide_later_branch_checks(self) -> None:
        branches = ("feature/work", "feature/later")
        tips = ("a" * 40, "b" * 40)
        bad_names = (
            (branches[1], "branch association does not match the requested branch"),
            (None, "missing or invalid branch association metadata"),
        )
        for name, reason in bad_names:
            for protected in (False, True):
                with self.subTest(name=name, later_protected=protected):
                    deployments = [
                        [{"id": 11, "ref": branches[0], "sha": tips[0]}],
                        [{"id": 22, "ref": branches[1], "sha": tips[1]}],
                    ]
                    pull_requests = [
                        [pull_request_fixture(number=31, state="open", merged_at=None)],
                        [pull_request_fixture(
                            number=42,
                            head={"ref": branches[1], "repo": {"full_name": "fixture/repo"}},
                        )],
                    ]
                    responses = {}
                    expected_calls = []
                    for index, branch in enumerate(branches):
                        full_ref = f"refs/heads/{branch}"
                        probe = ("git", "ls-remote", "--heads", "origin", full_ref)
                        responses[probe] = f"{tips[index]}\t{full_ref}\n"
                        expected_calls.append(probe)
                        encoded_branch = quote(branch, safe="")
                        encoded_head = quote(f"fixture:{branch}", safe="")
                        api_responses = {
                            f"repos/fixture/repo/branches/{encoded_branch}": {
                                "name": name if index == 0 else branch,
                                "protected": not protected if index == 0 else protected,
                            },
                            f"repos/fixture/repo/deployments?ref={encoded_branch}"
                            "&per_page=100&page=1": deployments[index],
                            f"repos/fixture/repo/pulls?state=all&head={encoded_head}"
                            "&per_page=100&page=1": pull_requests[index],
                        }
                        for endpoint, data in api_responses.items():
                            call = ("gh", "api", endpoint)
                            responses[call] = json.dumps(data)
                            expected_calls.append(call)
                    calls = []

                    def command(args, _root):
                        call = tuple(args)
                        calls.append(call)
                        self.assertIn(call, responses, "unexpected hosted request")
                        return subprocess.CompletedProcess(args, 0, responses[call], "")

                    with patch.object(audit_repo, "remote_url_for_provider", return_value=(
                        "origin", "https://github.com/fixture/repo.git",
                    )), patch.object(audit_repo.shutil, "which", return_value="/fixture/gh"), \
                            patch.object(audit_repo, "hosted_command", side_effect=command):
                        report = audit_repo.audit_hosted_branches(
                            Path("."), [f"origin={branch}" for branch in branches],
                        )

                    self.assertEqual(calls, expected_calls)
                    self.assertEqual(len(report["entries"]), 2)
                    first, later = report["entries"]
                    for index, entry in enumerate(report["entries"]):
                        self.assertEqual((entry["provider"], entry["ref"], entry["full_ref"]), (
                            "origin", branches[index], f"refs/heads/{branches[index]}",
                        ))
                        self.assertEqual(entry["classification"], "present")
                        self.assertEqual(entry["ref_status"], "present")
                        self.assertEqual(entry["tip"], tips[index])
                        for key, histories in (
                            ("deployments", deployments), ("pull_requests", pull_requests),
                        ):
                            self.assertEqual(entry[key], {
                                "status": "available", "source": "github-api",
                                "count": len(histories[index]), "items": histories[index],
                            })
                        self.assertTrue(entry["deletion_blocked"])
                    self.assertEqual(first["protection"], {
                        "status": "unknown", "reason": f"GitHub branch response: {reason}",
                    })
                    self.assertEqual(first["blocking_reasons"], [
                        "hosted-open-pull-request", "hosted-protection-unknown",
                        "hosted-ref-has-deployments",
                    ])
                    self.assertEqual(later["protection"], {
                        "status": "protected" if protected else "unprotected",
                        "source": "github-api", "repository": "fixture/repo",
                        "ref": branches[1],
                    })
                    self.assertEqual(later["blocking_reasons"], sorted(
                        ["hosted-ref-has-deployments"]
                        + (["hosted-ref-protected"] if protected else [])
                    ))
                    self.assertTrue(report["deletion_blocked"])
                    self.assertEqual(report["blocking_entries"],
                                     [f"origin:{branch}" for branch in branches])
                    self.assertEqual(report["cleanup_plan"], {
                        "merge": [], "delete": [], "review": [],
                        "keep": [{
                            "provider": entry["provider"], "ref": entry["ref"],
                            "blocking_reasons": entry["blocking_reasons"],
                            "blocking_reason_explanations": [{
                                "reason_code": code,
                                "explanation": audit_repo.HOSTED_HOLD_EXPLANATIONS[code],
                            } for code in entry["blocking_reasons"]],
                        } for entry in (later, first)],
                    })

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
        merged = [pull_request_fixture(number=n)
                  for n in range(1, 201)]
        for state, code, bucket in (
            ("open", "hosted-open-pull-request", "keep"),
            ("closed", "hosted-closed-unmerged-pull-request", "review"),
        ):
            for page in (2, 3):
                with self.subTest(state=state, page=page):
                    later_pr = pull_request_fixture(number=201, state=state, merged_at=None)
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
                items = [pull_request_fixture(number=n)
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
        merged = pull_request_fixture()
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

    def test_unrelated_pr_heads_discard_history_and_retain_unknown_hold(self) -> None:
        for ref, repository in (
            ("feature/other", "fixture/repo"),
            ("Feature/work", "fixture/repo"),
            ("refs/heads/feature/work", "fixture/repo"),
            ("feature/work", "fork-owner/repo"),
            ("feature/work", "fixture/another-repo"),
            ("feature/work", "fork-owner/another-repo"),
        ):
            for state, merged_at in (
                ("open", None), ("closed", None), ("closed", "2099-01-01"),
            ):
                for page in (1, 2):
                    with self.subTest(ref=ref, repository=repository, state=state,
                                      merged_at=merged_at, page=page):
                        record = pull_request_fixture(
                            state=state, merged_at=merged_at,
                            head={"ref": ref, "repo": {"full_name": repository}},
                            base={"ref": "main", "repo": {"full_name": "fixture/repo"}},
                        )
                        pages = [[pull_request_fixture()] * 100] if page == 2 else []
                        pages.append([pull_request_fixture(), record])
                        report = self.hosted_pages_fixture([[]], pages)
                        entry = report["entries"][0]
                        self.assertEqual(entry["pull_requests"], {
                            "status": "unknown",
                            "reason": f"GitHub pull-request response on page {page}: "
                                      "head association does not match the requested repository and branch",
                        })
                        self.assertEqual(entry["protection"]["status"], "unprotected")
                        self.assertEqual(entry["deployments"]["status"], "available")
                        self.assert_single_hosted_hold(
                            report, "hosted-pull-request-evidence-unknown", "review",
                        )

    def test_missing_or_invalid_pr_head_metadata_is_unknown_not_no_history(self) -> None:
        records = [
            {"state": "closed", "merged_at": "2099-01-01"},
            *(pull_request_fixture(head=head) for head in (
                None, [], "fixture:feature/work", {}, {"repo": {"full_name": "fixture/repo"}},
                {"ref": None, "repo": {"full_name": "fixture/repo"}},
                {"ref": [], "repo": {"full_name": "fixture/repo"}},
                {"ref": "", "repo": {"full_name": "fixture/repo"}},
                {"ref": "feature/work"}, {"ref": "feature/work", "repo": None},
                {"ref": "feature/work", "repo": []},
                {"ref": "feature/work", "repo": {}},
                {"ref": "feature/work", "repo": {"full_name": None}},
                {"ref": "feature/work", "repo": {"full_name": ""}},
                {"ref": "feature/work", "repo": {"full_name": ["fixture/repo"]}},
                {"ref": "feature/work", "label": "fixture:feature/work",
                 "repo": {"name": "repo", "owner": {"login": "fixture"}}},
            )),
        ]
        for record in records:
            for page in (1, 2):
                with self.subTest(record=record, page=page):
                    pages = [[pull_request_fixture()] * 100] if page == 2 else []
                    pages.append([record])
                    report = self.hosted_pages_fixture([[]], pages)
                    self.assertEqual(report["entries"][0]["pull_requests"], {
                        "status": "unknown",
                        "reason": f"GitHub pull-request response on page {page}: "
                                  "missing or invalid head association metadata",
                    })
                    self.assert_single_hosted_hold(
                        report, "hosted-pull-request-evidence-unknown", "review",
                    )

    def test_pr_heads_require_exact_unicode_branch_spelling(self) -> None:
        composed = "feature/caf\u00e9"
        decomposed = "feature/cafe\u0301"
        self.assertNotEqual(composed, decomposed)
        for branch, other_spelling in ((composed, decomposed), (decomposed, composed)):
            for exact in (False, True):
                for state, merged_at, code, bucket in (
                    ("open", None, "hosted-open-pull-request", "keep"),
                    ("closed", None, "hosted-closed-unmerged-pull-request", "review"),
                    ("closed", "2099-01-01", None, None),
                ):
                    for page in (1, 2):
                        with self.subTest(branch=branch, exact=exact, state=state,
                                          merged_at=merged_at, page=page):
                            head = {"ref": branch, "repo": {"full_name": "fixture/repo"}}
                            prior = [
                                pull_request_fixture(number=n, head=head)
                                for n in range(1, 101)
                            ] if page == 2 else []
                            record = pull_request_fixture(
                                number=101, state=state, merged_at=merged_at,
                                head={
                                    "ref": branch if exact else other_spelling,
                                    "repo": {"full_name": "fixture/repo"},
                                },
                            )
                            pages = ([prior] if prior else []) + [[record]]
                            report = self.hosted_pages_fixture([[]], pages, branch=branch)
                            entry = report["entries"][0]
                            self.assertEqual(entry["ref"], branch)
                            self.assertEqual(entry["classification"], "present")
                            self.assertEqual(entry["protection"]["status"], "unprotected")
                            self.assertEqual(entry["deployments"]["status"], "available")
                            if not exact:
                                self.assertEqual(entry["pull_requests"], {
                                    "status": "unknown",
                                    "reason": f"GitHub pull-request response on page {page}: "
                                              "head association does not match the requested repository and branch",
                                })
                                self.assertEqual(report["blocking_entries"], [f"origin:{branch}"])
                                self.assert_single_hosted_hold(
                                    report, "hosted-pull-request-evidence-unknown", "review",
                                )
                                held = report["cleanup_plan"]["review"][0]
                                self.assertEqual(held["provider"], "origin")
                                self.assertEqual(held["ref"], branch)
                            else:
                                self.assertEqual(entry["pull_requests"], {
                                    "status": "available", "source": "github-api",
                                    "count": len(prior) + 1, "items": prior + [record],
                                })
                                if code:
                                    self.assert_single_hosted_hold(report, code, bucket)
                                else:
                                    self.assertFalse(entry["deletion_blocked"])
                                    self.assertFalse(report["deletion_blocked"])
                                    self.assertEqual(entry["blocking_reasons"], [])
                                    self.assertEqual(report["blocking_entries"], [])
                                    self.assertEqual(report["cleanup_plan"]["keep"], [])
                                    self.assertEqual(report["cleanup_plan"]["review"], [])

    def test_pr_heads_require_exact_unicode_case_with_case_insensitive_repository(self) -> None:
        for left, right in (
            ("feature/CAF\u00c9", "feature/caf\u00e9"),
            ("feature/Stra\u00dfe", "feature/STRASSE"),
            ("feature/\u03c3", "feature/\u03c2"),
        ):
            self.assertNotEqual(left, right)
            self.assertEqual(left.casefold(), right.casefold())
            for branch, other_case in ((left, right), (right, left)):
                for exact in (False, True):
                    for state, merged_at, code, bucket in (
                        ("open", None, "hosted-open-pull-request", "keep"),
                        ("closed", None, "hosted-closed-unmerged-pull-request", "review"),
                        ("closed", "2099-01-01", None, None),
                    ):
                        for page in (1, 2):
                            with self.subTest(
                                branch=branch, other_case=other_case, exact=exact,
                                state=state, merged_at=merged_at, page=page,
                            ):
                                # Repository case may differ even though branch case must not.
                                head = {"ref": branch, "repo": {"full_name": "Fixture/REPO"}}
                                prior = [
                                    pull_request_fixture(number=n, head=head)
                                    for n in range(1, 101)
                                ] if page == 2 else []
                                record = pull_request_fixture(
                                    number=101, state=state, merged_at=merged_at,
                                    head={
                                        "ref": branch if exact else other_case,
                                        "repo": {"full_name": "Fixture/REPO"},
                                    },
                                )
                                pages = ([prior] if prior else []) + [[record]]
                                report = self.hosted_pages_fixture([[]], pages, branch=branch)
                                entry = report["entries"][0]
                                self.assertEqual(entry["ref"], branch)
                                self.assertEqual(entry["full_ref"], f"refs/heads/{branch}")
                                self.assertEqual(entry["classification"], "present")
                                self.assertEqual(entry["protection"]["status"], "unprotected")
                                self.assertEqual(entry["deployments"]["status"], "available")
                                if not exact:
                                    self.assertEqual(entry["pull_requests"], {
                                        "status": "unknown",
                                        "reason": f"GitHub pull-request response on page {page}: "
                                                  "head association does not match the requested repository and branch",
                                    })
                                    self.assertEqual(report["blocking_entries"], [f"origin:{branch}"])
                                    self.assert_single_hosted_hold(
                                        report, "hosted-pull-request-evidence-unknown", "review",
                                    )
                                    held = report["cleanup_plan"]["review"][0]
                                    self.assertEqual(held["provider"], "origin")
                                    self.assertEqual(held["ref"], branch)
                                else:
                                    self.assertEqual(entry["pull_requests"], {
                                        "status": "available", "source": "github-api",
                                        "count": len(prior) + 1, "items": prior + [record],
                                    })
                                    if code:
                                        self.assert_single_hosted_hold(report, code, bucket)
                                    else:
                                        self.assertFalse(entry["deletion_blocked"])
                                        self.assertFalse(report["deletion_blocked"])
                                        self.assertEqual(entry["blocking_reasons"], [])
                                        self.assertEqual(report["blocking_entries"], [])
                                        self.assertEqual(report["cleanup_plan"]["keep"], [])
                                        self.assertEqual(report["cleanup_plan"]["review"], [])

    def test_unicode_branches_remain_distinct_in_one_hosted_report(self) -> None:
        composed = "feature/caf\u00e9"
        decomposed = "feature/cafe\u0301"
        encoded_refs = {
            composed: "feature%2Fcaf%C3%A9",
            decomposed: "feature%2Fcafe%CC%81",
        }
        histories = {
            composed: [pull_request_fixture(
                number=101, state="open", merged_at=None,
                head={"ref": composed, "repo": {"full_name": "fixture/repo"}},
            )],
            decomposed: [pull_request_fixture(
                number=202, state="closed", merged_at=None,
                head={"ref": decomposed, "repo": {"full_name": "fixture/repo"}},
            )],
        }
        holds = {
            composed: ("hosted-open-pull-request", "keep"),
            decomposed: ("hosted-closed-unmerged-pull-request", "review"),
        }
        for branches in ((composed, decomposed), (decomposed, composed)):
            with self.subTest(branches=branches):
                fixtures = {}
                expected_calls = []
                for branch in branches:
                    encoded = encoded_refs[branch]
                    probe = ["git", "ls-remote", "--heads", "origin", f"refs/heads/{branch}"]
                    expected_calls.append(probe)
                    endpoints = {
                        f"repos/fixture/repo/branches/{encoded}": {
                            "name": branch, "protected": False,
                        },
                        f"repos/fixture/repo/deployments?ref={encoded}&per_page=100&page=1": [],
                        f"repos/fixture/repo/pulls?state=all&head=fixture%3A{encoded}"
                        "&per_page=100&page=1": histories[branch],
                    }
                    fixtures.update(endpoints)
                    expected_calls.extend(["gh", "api", endpoint] for endpoint in endpoints)
                calls = []

                def command(args, _root):
                    calls.append(args)
                    if args[0] == "git":
                        self.assertIn(args, expected_calls)
                        return subprocess.CompletedProcess(
                            args, 0, f"{'a' * 40}\t{args[-1]}\n", "",
                        )
                    self.assertEqual(args[:2], ["gh", "api"])
                    self.assertIn(args[2], fixtures, "unexpected or normalized branch request")
                    return subprocess.CompletedProcess(args, 0, json.dumps(fixtures[args[2]]), "")

                with patch.object(
                    audit_repo, "remote_url_for_provider",
                    return_value=("origin", "https://github.com/fixture/repo.git"),
                ), patch.object(audit_repo.shutil, "which", return_value="/fixture/gh"), patch.object(
                    audit_repo, "hosted_command", side_effect=command,
                ):
                    report = audit_repo.audit_hosted_branches(
                        Path("."), [f"origin={branch}" for branch in branches],
                    )

                self.assertEqual(calls, expected_calls)
                self.assertEqual([entry["ref"] for entry in report["entries"]], list(branches))
                self.assertTrue(report["deletion_blocked"])
                self.assertEqual(report["blocking_entries"], [
                    f"origin:{branch}" for branch in branches
                ])
                for entry, branch in zip(report["entries"], branches):
                    code, bucket = holds[branch]
                    self.assertEqual(entry["provider"], "origin")
                    self.assertEqual(entry["full_ref"], f"refs/heads/{branch}")
                    self.assertEqual(entry["classification"], "present")
                    self.assertEqual(entry["protection"]["status"], "unprotected")
                    self.assertEqual(entry["deployments"], {
                        "status": "available", "source": "github-api", "count": 0, "items": [],
                    })
                    self.assertEqual(entry["pull_requests"], {
                        "status": "available", "source": "github-api",
                        "count": 1, "items": histories[branch],
                    })
                    self.assertTrue(entry["deletion_blocked"])
                    self.assertEqual(entry["blocking_reasons"], [code])
                    self.assertEqual(report["cleanup_plan"][bucket], [{
                        "provider": "origin", "ref": branch,
                        "blocking_reasons": [code],
                        "blocking_reason_explanations": [{
                            "reason_code": code,
                            "explanation": audit_repo.HOSTED_HOLD_EXPLANATIONS[code],
                        }],
                    }])
                self.assertEqual(report["cleanup_plan"]["merge"], [])
                self.assertEqual(report["cleanup_plan"]["delete"], [])

    def test_matching_pr_heads_preserve_results_with_case_insensitive_repository(self) -> None:
        for repository in ("fixture/repo", "Fixture/REPO"):
            for state, merged_at, code, bucket in (
                ("open", None, "hosted-open-pull-request", "keep"),
                ("closed", None, "hosted-closed-unmerged-pull-request", "review"),
                ("closed", "2099-01-01", None, None),
            ):
                with self.subTest(repository=repository, state=state, merged_at=merged_at):
                    record = pull_request_fixture(
                        state=state, merged_at=merged_at,
                        head={"ref": "feature/work", "repo": {"full_name": repository}},
                        # A PR targeting another repository still belongs to this head.
                        base={"ref": "release", "repo": {"full_name": "upstream/project"}},
                    )
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

    def test_unrelated_pr_head_does_not_interrupt_later_provider_ref_lookups(self) -> None:
        calls = []

        def command(args, _root):
            if args[0] == "git":
                return subprocess.CompletedProcess(args, 0, f"{'a' * 40}\t{args[-1]}\n", "")
            endpoint = args[2]
            calls.append(endpoint)
            if "/branches/" in endpoint:
                data = {"protected": False}
            elif "/deployments?" in endpoint:
                data = []
            else:
                data = [pull_request_fixture(head={
                    "ref": "unrelated" if len(calls) == 3 else "feature/work",
                    "repo": {"full_name": "fixture/repo"},
                })]
            return subprocess.CompletedProcess(args, 0, json.dumps(data), "")

        with patch.object(
            audit_repo, "remote_url_for_provider",
            return_value=("origin", "https://github.com/fixture/repo.git"),
        ), patch.object(audit_repo.shutil, "which", return_value="/fixture/gh"), patch.object(
            audit_repo, "hosted_command", side_effect=command,
        ):
            report = audit_repo.audit_hosted_branches(
                Path("."), ["origin=feature/work", "mirror=feature/work"],
            )
        self.assertEqual(len(calls), 6)
        self.assertEqual(calls[:3], calls[3:])
        self.assert_single_hosted_hold(report, "hosted-pull-request-evidence-unknown", "review")
        self.assertEqual(report["blocking_entries"], ["origin:feature/work"])
        self.assertFalse(report["entries"][1]["deletion_blocked"])
        self.assertEqual(report["entries"][1]["pull_requests"]["status"], "available")

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
            (pull_request_fixture(state="open", merged_at=None), "hosted-open-pull-request", "keep"),
            (pull_request_fixture(merged_at=None), "hosted-closed-unmerged-pull-request", "review"),
            (pull_request_fixture(), None, None),
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

    def test_unrelated_deployments_leave_association_unknown(self) -> None:
        for ref in (
            "other", "feature/work-extra", "Feature/work", "refs/heads/feature/work",
            "refs/tags/feature/work", "a" * 40,
        ):
            for sha in (None, "a" * 40, "b" * 40):
                record = {"id": 1, "ref": ref}
                if sha is not None:
                    record["sha"] = sha
                with self.subTest(ref=ref, sha=sha):
                    report = self.hosted_pages_fixture([[record]], [[]])
                    self.assertEqual(report["entries"][0]["deployments"], {
                        "status": "unknown",
                        "reason": "GitHub deployments response on page 1: "
                                  "deployment association does not match the requested branch",
                    })
                    self.assertEqual(report["entries"][0]["protection"]["status"], "unprotected")
                    self.assertEqual(report["entries"][0]["pull_requests"]["status"], "available")
                    self.assert_single_hosted_hold(
                        report, "hosted-deployment-evidence-unknown", "review",
                    )

    def test_missing_or_malformed_deployment_metadata_stays_unknown(self) -> None:
        records = [{}, {"sha": "a" * 40}]
        records += [{"ref": ref} for ref in (None, "", " ", False, 4, [], {})]
        records += [{"ref": "feature/work", "sha": sha}
                    for sha in (None, "", " ", False, 4, [], {}, "a" * 39,
                                "a" * 41, "g" * 40, "a" * 63, "a" * 65)]
        for record in records:
            with self.subTest(record=record):
                report = self.hosted_pages_fixture([[record]], [[]])
                self.assertEqual(report["entries"][0]["deployments"], {
                    "status": "unknown",
                    "reason": "GitHub deployments response on page 1: "
                              "missing or invalid deployment association metadata",
                })
                self.assert_single_hosted_hold(
                    report, "hosted-deployment-evidence-unknown", "review",
                )

    def test_matching_deployments_accept_optional_and_historical_shas(self) -> None:
        for fields in ({}, {"sha": "a" * 40}, {"sha": "b" * 40}, {"sha": "B" * 64}):
            record = {"id": 1, "ref": "feature/work", **fields}
            with self.subTest(fields=fields):
                report = self.hosted_pages_fixture([[record]], [[]])
                evidence = report["entries"][0]["deployments"]
                self.assertEqual(evidence["status"], "available")
                self.assertEqual(evidence["count"], 1)
                self.assertEqual(evidence["items"], [record])
                self.assert_single_hosted_hold(report, "hosted-ref-has-deployments", "keep")

    def test_bad_deployment_association_discards_earlier_pages_and_mixed_records(self) -> None:
        valid = [{"id": n, "ref": "feature/work", "sha": "b" * 40}
                 for n in range(100)]
        for bad in ({"ref": "unrelated"}, {"sha": "a" * 40},
                    {"ref": "feature/work", "sha": None}):
            for pages, page in (([[valid[0], bad]], 1), ([valid, [bad]], 2)):
                with self.subTest(bad=bad, page=page):
                    report = self.hosted_pages_fixture(pages, [[]])
                    evidence = report["entries"][0]["deployments"]
                    self.assertEqual(evidence["status"], "unknown")
                    self.assertIn(f"page {page}", evidence["reason"])
                    self.assertNotIn("count", evidence)
                    self.assertNotIn("items", evidence)
                    self.assert_single_hosted_hold(
                        report, "hosted-deployment-evidence-unknown", "review",
                    )

    def test_cli_bad_deployment_association_holds_and_continues_pairs(self) -> None:
        self._check_cli_bad_deployment_association()

    def test_cli_deployment_association_requires_exact_unicode_normalization(self) -> None:
        # Canonically equivalent spellings are not the same Git ref. Exercise
        # both directions, without conflating this with report-entry identity.
        for branch, returned_ref in (
            ("feature/caf\u00e9", "feature/cafe\u0301"),
            ("feature/cafe\u0301", "feature/caf\u00e9"),
        ):
            with self.subTest(branch=branch, returned_ref=returned_ref):
                self.assertNotEqual(branch, returned_ref)
                self._check_cli_bad_deployment_association(
                    branch=branch, returned_ref=returned_ref,
                )

    def _check_cli_bad_deployment_association(
        self, *, branch: str = "work", returned_ref: str | None = None,
    ) -> None:
        real_git = shutil.which("git")
        self.assertIsNotNone(real_git)
        tip = "a" * 40
        historical_fields = ({}, {"sha": "b" * 40}, {"sha": "b" * 64}, {"sha": "B" * 64})
        valid = [{"id": n, "ref": branch, "sha": tip} for n in range(100)]
        pairs = (("origin", branch), ("mirror", branch), ("origin", "later"))
        code = "hosted-deployment-evidence-unknown"
        explanation = (
            "Deployment evidence is unavailable; check deployment use before reviewing deletion."
        )
        bad_records = (
            ({"ref": "other", "sha": tip},
             "deployment association does not match the requested branch"),
            ({"sha": tip}, "missing or invalid deployment association metadata"),
            ({"ref": branch, "sha": None},
             "missing or invalid deployment association metadata"),
        )
        if returned_ref is not None:
            bad_records = (({"ref": returned_ref, "sha": tip},
                            "deployment association does not match the requested branch"),)
        for (bad, reason), fields in itertools.product(bad_records, historical_fields):
            # The mirror's exact spelling is a positive international control.
            historical = {"id": 200, "ref": branch, **fields}
            for pages, failing_page in (([[valid[0], bad]], 1), ([valid, [bad]], 2)):
                with self.subTest(bad=bad, page=failing_page, historical_fields=fields), \
                        tempfile.TemporaryDirectory() as directory:
                    fixture = Path(directory)
                    root = fixture / "repo"
                    root.mkdir()
                    self._init_repo(root)
                    bin_dir = fixture / "bin"
                    bin_dir.mkdir()
                    calls_path = fixture / "calls.jsonl"
                    responses_path = fixture / "responses.json"
                    responses = {}
                    expected_calls = []

                    def respond(args, output):
                        responses[json.dumps(args)] = output
                        expected_calls.append(args)

                    for index, (provider, ref) in enumerate(pairs):
                        prefix = f"repos/fixture/{provider}"
                        encoded_branch = quote(ref, safe="")
                        respond(["git", "remote", "get-url", provider],
                                f"https://github.com/fixture/{provider}.git\n")
                        respond(["git", "ls-remote", "--heads", provider,
                                 f"refs/heads/{ref}"], f"{tip}\trefs/heads/{ref}\n")
                        respond(["gh", "api", f"{prefix}/branches/{encoded_branch}"],
                                json.dumps({"name": ref, "protected": False}))
                        deployment_pages = pages if index == 0 else (
                            [[historical]] if index == 1 else [[]]
                        )
                        for page, records in enumerate(deployment_pages, 1):
                            respond(["gh", "api", f"{prefix}/deployments?ref={encoded_branch}"
                                     f"&per_page=100&page={page}"], json.dumps(records))
                        respond(["gh", "api", f"{prefix}/pulls?state=all"
                                 f"&head=fixture%3A{encoded_branch}&per_page=100&page=1"], "[]")
                    responses_path.write_text(json.dumps(responses), encoding="utf-8")
                    executable = (
                        f"#!{sys.executable}\n"
                        "import json, os, sys\n"
                        "from pathlib import Path\n"
                        f"REAL_GIT = {real_git!r}\n"
                        r'''
args = sys.argv[1:]
tool = Path(sys.argv[0]).name
if tool == "git" and args[0] in ("rev-parse", "branch", "for-each-ref", "log"):
    os.execv(REAL_GIT, [REAL_GIT, *args])
call = [tool, *args]
with open(os.environ["CLI_FIXTURE_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(call) + "\n")
responses = json.loads(Path(os.environ["CLI_FIXTURE_RESPONSES"]).read_text(encoding="utf-8"))
# Unknown commands fail locally instead of falling through to the network.
sys.stdout.write(responses[json.dumps(call)])
'''
                    )
                    for tool in ("git", "gh"):
                        path = bin_dir / tool
                        path.write_text(executable, encoding="utf-8")
                        path.chmod(0o755)
                    result = subprocess.run([
                        sys.executable, str(SCRIPT), "--root", str(root), "--base", "main",
                        *[arg for provider, branch in pairs
                          for arg in ("--hosted-branch", f"{provider}={branch}")],
                    ], capture_output=True, timeout=30, env={
                        "PATH": str(bin_dir), "HOME": str(fixture),
                        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
                        "GIT_ALLOW_PROTOCOL": "file", "GIT_TERMINAL_PROMPT": "0",
                        "PYTHONUTF8": "1",
                        "CLI_FIXTURE_LOG": str(calls_path),
                        "CLI_FIXTURE_RESPONSES": str(responses_path),
                    })
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stderr, b"")
                    report = json.loads(result.stdout)
                    self.assertNotIn("error", report)
                    self.assertFalse(report["fetch_performed"])
                    self.assertNotIn("deletion_commands", result.stdout.decode("utf-8"))
                    hosted = report["hosted_lifecycle"]
                    self.assertEqual([(entry["provider"], entry["ref"])
                                      for entry in hosted["entries"]], list(pairs))
                    for index, entry in enumerate(hosted["entries"]):
                        self.assertEqual(entry["classification"], "present")
                        self.assertEqual(entry["ref_status"], "present")
                        self.assertEqual(entry["tip"], tip)
                        self.assertEqual(entry["protection"]["status"], "unprotected")
                        self.assertEqual(entry["pull_requests"], {
                            "status": "available", "source": "github-api",
                            "count": 0, "items": [],
                        })
                        self.assertEqual(entry["deletion_blocked"], index != 2)
                        self.assertEqual(entry["blocking_reasons"], (
                            [code] if index == 0 else
                            ["hosted-ref-has-deployments"] if index == 1 else []
                        ))
                    # Equality also proves neither partial items nor a count leaked.
                    self.assertEqual(hosted["entries"][0]["deployments"], {
                        "status": "unknown",
                        "reason": f"GitHub deployments response on page {failing_page}: {reason}",
                    })
                    if "sha" in historical:
                        self.assertNotEqual(historical["sha"], tip)
                    self.assertEqual(hosted["entries"][1]["deployments"], {
                        "status": "available", "source": "github-api",
                        "count": 1, "items": [historical],
                    })
                    self.assertEqual(hosted["entries"][2]["deployments"], {
                        "status": "available", "source": "github-api",
                        "count": 0, "items": [],
                    })
                    self.assertTrue(hosted["deletion_blocked"])
                    self.assertEqual(hosted["blocking_entries"],
                                     [f"{provider}:{ref}" for provider, ref in pairs[:2]])
                    self.assertEqual(hosted["cleanup_plan"], {
                        "merge": [], "delete": [],
                        "review": [{
                            "provider": "origin", "ref": pairs[0][1],
                            "blocking_reasons": [code],
                            "blocking_reason_explanations": [{
                                "reason_code": code, "explanation": explanation,
                            }],
                        }],
                        "keep": [{
                            "provider": "mirror", "ref": pairs[1][1],
                            "blocking_reasons": ["hosted-ref-has-deployments"],
                            "blocking_reason_explanations": [{
                                "reason_code": "hosted-ref-has-deployments",
                                "explanation": "The hosted branch has deployment records; "
                                               "keep it until their use is reviewed.",
                            }],
                        }],
                    })
                    self.assertEqual([json.loads(line) for line in
                                      calls_path.read_text(encoding="utf-8").splitlines()],
                                     expected_calls)

    def test_partial_page_failures_discard_history_and_retain_unknown_holds(self) -> None:
        failures = (
            (subprocess.CompletedProcess([], 1, "", "rate limited"), "request failed"),
            (subprocess.TimeoutExpired(["gh", "api"], 30, output=b"[]"), "timed out"),
            (FileNotFoundError(2, "No such file or directory", "gh"), "command could not start"),
            (PermissionError(13, "Permission denied", "gh"), "command could not start"),
            (UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"), "output could not be decoded"),
            ("not JSON", "invalid JSON"),
            (audit_repo.HostedOutputLimitExceeded("hosted command output exceeded 256 bytes; output discarded"), "output exceeded"),
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
                    first_page = ([{"id": n, "ref": "feature/work"} for n in range(100)] if key == "deployments"
                                  else [pull_request_fixture(number=n)
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

    def test_history_repeated_full_pages_discard_all_partial_evidence(self) -> None:
        first = [{"id": n, "details": {"ref": "work", "environment": "preview"}}
                 for n in range(100)]
        distinct = [{"id": n + 100, "details": item["details"]}
                    for n, item in enumerate(first)]
        reordered_keys = [{"details": {"environment": "preview", "ref": "work"}, "id": n}
                          for n in range(100)]
        for label in ("deployments", "pull-request"):
            for pages, matching in (
                ([first, first], 1),
                ([first, distinct, reordered_keys], 1),
                ([first, distinct, distinct], 2),
            ):
                with self.subTest(label=label, pages=len(pages), matching=matching), patch.object(
                    audit_repo, "gh_api_json", side_effect=[(page, None) for page in pages],
                ) as api:
                    data, error = audit_repo.gh_api_list(Path("."), "history?ref=work", label)
                self.assertIsNone(data)
                self.assertEqual(error, (
                    f"GitHub {label} history incomplete at page {len(pages)}: "
                    f"repeated full page (matches page {matching}); "
                    "no short or empty page confirmed completion"
                ))
                self.assertEqual([request.args for request in api.call_args_list], [
                    (Path("."), f"history?ref=work&per_page=100&page={page}")
                    for page in range(1, len(pages) + 1)
                ])

    def test_history_distinct_full_pages_complete_with_all_records(self) -> None:
        # Same IDs do not make pages identical: changed record values are distinct.
        pages = [[{"id": n, "value": page} for n in range(100)] for page in range(3)]
        for label in ("deployments", "pull-request"):
            with self.subTest(label=label), patch.object(
                audit_repo, "gh_api_json", side_effect=[(page, None) for page in [*pages, []]],
            ) as api:
                data, error = audit_repo.gh_api_list(Path("."), "history?ref=work", label)
            self.assertIsNone(error)
            self.assertEqual(data, sum(pages, []))
            self.assertEqual(api.call_count, 4)

    def test_history_page_budget_discards_distinct_full_pages(self) -> None:
        self.assertEqual(audit_repo.HOSTED_HISTORY_MAX_PAGES, 10)
        for label in ("deployments", "pull-request"):
            calls = []

            def response(_root, endpoint):
                calls.append(endpoint)
                start = (len(calls) - 1) * 100
                return [{"id": n} for n in range(start, start + 100)], None

            with self.subTest(label=label), patch.object(
                audit_repo, "gh_api_json", side_effect=response,
            ):
                data, error = audit_repo.gh_api_list(Path("."), "history?ref=work", label)
            self.assertIsNone(data)
            self.assertEqual(error, (
                f"GitHub {label} history incomplete after page 10: "
                "pagination budget exhausted (10 pages); "
                "no short or empty page confirmed completion"
            ))
            self.assertEqual(calls, [
                f"history?ref=work&per_page=100&page={page}" for page in range(1, 11)
            ])

    def test_history_completes_on_short_or_empty_last_budgeted_page(self) -> None:
        for final_count in (0, 99):
            pages = [[{"id": page * 100 + n} for n in range(100)] for page in range(9)]
            pages.append([{"id": 900 + n} for n in range(final_count)])
            with self.subTest(final_count=final_count), patch.object(
                audit_repo, "gh_api_json", side_effect=[(page, None) for page in pages],
            ) as api:
                data, error = audit_repo.gh_api_list(Path("."), "history?ref=work", "deployments")
            self.assertIsNone(error)
            self.assertEqual(data, sum(pages, []))
            self.assertEqual(api.call_count, 10)

    def test_history_repeat_and_budget_holds_continue_other_evidence_and_provider_ref_pairs(self) -> None:
        keys = ("deployments", "pull_requests")
        codes = (
            "hosted-deployment-evidence-unknown", "hosted-pull-request-evidence-unknown",
        )
        for repeated, exhausted in (
            (repeated, exhausted)
            for repeated in (False, True)
            for exhausted in ((0,), (1,), (0, 1))
        ):
            calls = []
            pair = -1
            full_page = [pull_request_fixture(
                id=n, number=n, head={"ref": "work", "repo": {"full_name": "fixture/repo"}},
                ref="work",
            )
                         for n in range(100)]

            def command(args, _root):
                nonlocal pair
                if args[0] == "git":
                    pair += 1
                    return subprocess.CompletedProcess(args, 0, f"{'a' * 40}\t{args[-1]}\n", "")
                endpoint = args[2]
                calls.append((pair, endpoint))
                if "/branches/" in endpoint:
                    data = {"protected": False}
                else:
                    index = 0 if "/deployments?" in endpoint else 1
                    page = int(endpoint.rsplit("page=", 1)[1])
                    data = [
                        {**record, "id": record["id"] + (0 if repeated else (page - 1) * 100)}
                        for record in full_page
                    ] if pair == 0 and index in exhausted else []
                return subprocess.CompletedProcess(args, 0, json.dumps(data), "")

            with self.subTest(repeated=repeated, exhausted=exhausted), patch.object(
                audit_repo, "remote_url_for_provider",
                side_effect=lambda _root, provider: (provider, "https://github.com/fixture/repo.git"),
            ), patch.object(audit_repo.shutil, "which", return_value="/fixture/gh"), patch.object(
                audit_repo, "hosted_command", side_effect=command,
            ):
                report = audit_repo.audit_hosted_branches(
                    Path("."), ["origin=work", "mirror=work", "origin=later"],
                )
            first = report["entries"][0]
            self.assertEqual(first["protection"]["status"], "unprotected")
            expected_codes = sorted(codes[index] for index in exhausted)
            self.assertEqual(first["blocking_reasons"], expected_codes)
            self.assertTrue(first["deletion_blocked"])
            for index, key in enumerate(keys):
                evidence = first[key]
                if index in exhausted:
                    self.assertEqual(evidence["status"], "unknown")
                    reason = ("repeated full page (matches page 1)" if repeated
                              else "pagination budget exhausted (10 pages)")
                    self.assertIn(reason, evidence["reason"])
                    self.assertNotIn("items", evidence)
                    self.assertNotIn("count", evidence)
                else:
                    self.assertEqual(evidence["status"], "available")
                    self.assertEqual(evidence["count"], 0)
                fragment = "/deployments?" if index == 0 else "/pulls?"
                endpoints = [endpoint for current, endpoint in calls
                             if current == 0 and fragment in endpoint]
                expected_pages = (2 if repeated else 10) if index in exhausted else 1
                self.assertEqual(len(endpoints), expected_pages)
                self.assertTrue(endpoints[-1].endswith(f"page={len(endpoints)}"))
            for current, later in enumerate(report["entries"][1:], 1):
                self.assertEqual(later["classification"], "present")
                self.assertFalse(later["deletion_blocked"])
                self.assertEqual(len([endpoint for pair_index, endpoint in calls
                                      if pair_index == current]), 3)
                for key in keys:
                    self.assertEqual(later[key]["status"], "available")
                    self.assertEqual(later[key]["count"], 0)
            self.assertEqual(report["blocking_entries"], ["origin:work"])
            self.assertEqual(len(report["cleanup_plan"]["review"]), 1)
            self.assertEqual(report["cleanup_plan"]["review"][0]["blocking_reasons"], expected_codes)
            self.assertEqual(report["cleanup_plan"]["delete"], [])
            self.assertEqual(report["cleanup_plan"]["merge"], [])

    def test_cli_repeated_history_holds_continue_pairs_without_repository_writes(self) -> None:
        self._assert_cli_history_pages_without_repository_writes(
            (repeated, repeat_page, matching_page, 100)
            for repeated in ((0,), (1,), (0, 1))
            for repeat_page, matching_page in ((2, 1), (3, 1), (3, 2))
        )

    def test_cli_identical_short_first_pages_complete_without_repository_writes(self) -> None:
        self._assert_cli_history_pages_without_repository_writes(
            ((), 1, None, count) for count in (0, 1, 99)
        )

    def test_cli_sha256_history_pages_without_repository_writes(self) -> None:
        scenarios = [
            (repeated, repeat_page, matching_page, 100)
            for repeated in ((0,), (1,), (0, 1))
            for repeat_page, matching_page in ((2, 1), (3, 1), (3, 2))
        ]
        scenarios.extend(((), 1, None, count) for count in (0, 1, 99))
        self._assert_cli_history_pages_without_repository_writes(
            scenarios, object_format="sha256",
        )

    def _assert_cli_history_pages_without_repository_writes(
        self, scenarios, *, object_format="sha1",
    ) -> None:
        real_git = shutil.which("git")
        self.assertIsNotNone(real_git)
        keys = ("deployments", "pull_requests")
        codes = ("hosted-deployment-evidence-unknown",
                 "hosted-pull-request-evidence-unknown")
        pairs = (("origin", "work"), ("mirror", "work"), ("origin", "later"))
        # GitHub repository names are case-insensitive. Distinct endpoint spellings
        # let fake gh return different pagination outcomes for identical records.
        hosted_repos = {"origin": "origin", "mirror": "ORIGIN"}

        def reverse_object_keys(value):
            if isinstance(value, dict):
                return {key: reverse_object_keys(item)
                        for key, item in reversed(value.items())}
            if isinstance(value, list):
                return [reverse_object_keys(item) for item in value]
            return value

        for repeated, repeat_page, matching_page, first_count in scenarios:
            with self.subTest(
                repeated=repeated, repeat_page=repeat_page, matching_page=matching_page,
                first_count=first_count, object_format=object_format,
            ), tempfile.TemporaryDirectory() as directory:
                fixture = Path(directory)
                root = fixture / "repo"
                root.mkdir()
                bin_dir = fixture / "bin"
                bin_dir.mkdir()
                calls_path = fixture / "calls.jsonl"
                responses_path = fixture / "responses.json"
                # No inherited credentials, Git configuration, or network transports.
                env = {
                    "PATH": str(bin_dir), "HOME": str(fixture),
                    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
                    "GIT_ALLOW_PROTOCOL": "file", "GIT_TERMINAL_PROMPT": "0",
                    "PYTHONUTF8": "1",
                    "CLI_FIXTURE_LOG": str(calls_path),
                    "CLI_FIXTURE_RESPONSES": str(responses_path),
                }
                remotes = {provider: fixture / f"{provider}.git"
                           for provider in ("origin", "mirror")}
                # Keep real Git's hosted URL reporting, but redirect only ls-remote's
                # transport to bare local remotes. No Git output is fabricated.
                git_path = bin_dir / "git"
                git_path.write_text(
                    f"#!{sys.executable}\n"
                    "import os, sys\n"
                    f"real_git = {real_git!r}\n"
                    f"remotes = { {name: str(path) for name, path in remotes.items()}!r}\n"
                    f"hosted_repos = {hosted_repos!r}\n"
                    "args = sys.argv[1:]\n"
                    "config = []\n"
                    "if args[:2] == ['ls-remote', '--heads']:\n"
                    "    for name, path in remotes.items():\n"
                    "        config += ['-c', 'url.' + path + '.insteadOf=' +\n"
                    "                   'https://github.com/fixture/' + hosted_repos[name] + '.git']\n"
                    "os.execv(real_git, [real_git, *config, *args])\n",
                    encoding="utf-8",
                )
                git_path.chmod(0o755)

                def git(path, *args):
                    return subprocess.run(
                        ["git", *args], cwd=path, env=env, capture_output=True,
                        text=True, check=True, timeout=10,
                    ).stdout.strip()

                initialized = subprocess.run(
                    ["git", "init", "-q", "-b", "main", f"--object-format={object_format}"],
                    cwd=root, env=env, capture_output=True, text=True, timeout=10,
                )
                if object_format == "sha256" and initialized.returncode:
                    unsupported = (
                        "unknown option `object-format=sha256'",
                        "unknown hash algorithm 'sha256'",
                        "the hash algorithm 'sha256' is not supported in this build",
                    )
                    if any(message in initialized.stderr for message in unsupported):
                        self.skipTest("Installed Git lacks SHA-256 repository support")
                self.assertEqual(initialized.returncode, 0, initialized.stderr)
                git(root, "config", "user.name", "Audit Test")
                git(root, "config", "user.email", "test@example.com")
                git(root, "config", "gc.auto", "0")
                git(root, "config", "maintenance.auto", "false")
                (root / "README.md").write_text("fixture\n", encoding="utf-8")
                git(root, "add", "README.md")
                git(root, "commit", "-qm", "initial")
                tip = git(root, "rev-parse", "HEAD")
                self.assertRegex(tip, rf"^[0-9a-f]{{{64 if object_format == 'sha256' else 40}}}$")
                self.assertEqual(git(root, "rev-parse", "--show-object-format"), object_format)
                for provider, remote in remotes.items():
                    git(root, "clone", "-q", "--bare", str(root), str(remote))
                    self.assertEqual(
                        git(remote, "rev-parse", "--show-object-format"), object_format,
                    )
                    git(remote, "config", "gc.auto", "0")
                    git(remote, "config", "maintenance.auto", "false")
                    for branch in ("work", "later"):
                        git(remote, "update-ref", f"refs/heads/{branch}", tip)
                    git(root, "remote", "add", provider,
                        f"https://github.com/fixture/{hosted_repos[provider]}.git")
                git(root, "branch", "work")
                git(root, "update-ref", "refs/archive/recovery", tip)
                (root / "README.md").write_text("stashed\n", encoding="utf-8")
                git(root, "stash", "push", "-qm", "retain recovery evidence")
                (root / "README.md").write_text("staged\n", encoding="utf-8")
                git(root, "add", "README.md")
                (root / "README.md").write_text("unstaged\n", encoding="utf-8")
                (root / "untracked.txt").write_text("retain\n", encoding="utf-8")

                responses = {}
                expected_calls = []
                expected_histories = {}
                expected_pages = {}
                for pair_index, (provider, branch) in enumerate(pairs):
                    prefix = f"repos/fixture/{hosted_repos[provider]}"
                    responses[f"{prefix}/branches/{branch}"] = {"protected": False}
                    expected_calls.append(f"{prefix}/branches/{branch}")
                    # The same page is valid for both history types and, for
                    # "work", both providers. A different ref needs its own
                    # association metadata, but still shares pages across types.
                    records = [
                        pull_request_fixture(
                            id=n, number=n, ref=branch, sha=tip,
                            head={"ref": branch, "repo": {"full_name": "fixture/origin"}},
                        ) for n in range(first_count)
                    ]
                    if pair_index == 1:
                        self.assertEqual(
                            records, responses[
                                "repos/fixture/origin/deployments?ref=work&per_page=100&page=1"
                            ],
                        )
                    for index, query in enumerate((
                        f"{prefix}/deployments?ref={branch}",
                        f"{prefix}/pulls?state=all&head=fixture%3A{branch}",
                    )):
                        held = pair_index == 0 and index in repeated
                        distinct = [{**record, "id" if index == 0 else "number": n + 100}
                                    for n, record in enumerate(records)]
                        matching_records = records if matching_page == 1 else distinct
                        reordered = reverse_object_keys(matching_records)
                        if records:
                            self.assertNotEqual(records, distinct)
                        self.assertEqual(matching_records, reordered)
                        if records:
                            self.assertNotEqual(json.dumps(matching_records), json.dumps(reordered))
                        # Exercise both kinds of completion after a full page;
                        # the short page contributes a new record, not a duplicate.
                        tail = distinct[:1] if index == 0 else []
                        # A, B, B must name page 2, even when the final B's
                        # object keys differ in order on the wire.
                        if first_count < 100:
                            # Terminal pages must not share repeat-detection state
                            # across evidence types or local provider aliases.
                            pages = [records]
                            expected_histories[pair_index, index] = records
                        else:
                            pages = ([records, reordered] if repeat_page == 2 else
                                     [records, distinct, reordered]) if held else [records, tail]
                            expected_histories[pair_index, index] = records + tail
                        expected_pages[pair_index, index] = pages
                        for page, page_records in enumerate(pages, start=1):
                            endpoint = f"{query}&per_page=100&page={page}"
                            responses[endpoint] = page_records
                            expected_calls.append(endpoint)
                    self.assertEqual(
                        expected_pages[pair_index, 0][0],
                        expected_pages[pair_index, 1][0],
                    )
                responses_path.write_text(json.dumps(responses), encoding="utf-8")
                gh_path = bin_dir / "gh"
                gh_path.write_text(
                    f"#!{sys.executable}\n"
                    "import json, os, sys\n"
                    "from pathlib import Path\n"
                    "assert sys.argv[1] == 'api' and len(sys.argv) == 3, sys.argv\n"
                    "endpoint = sys.argv[2]\n"
                    "with open(os.environ['CLI_FIXTURE_LOG'], 'a') as log:\n"
                    "    log.write(json.dumps(endpoint) + '\\n')\n"
                    "responses = json.loads(Path(os.environ['CLI_FIXTURE_RESPONSES']).read_text())\n"
                    "print(json.dumps(responses[endpoint]))\n",
                    encoding="utf-8",
                )
                gh_path.chmod(0o755)

                def snapshot():
                    # Include index, reflogs, stash, recovery refs, object files,
                    # dirty worktree, and both bare remotes, not just branch tips.
                    return {
                        str(path.relative_to(fixture)): path.read_bytes()
                        for tree in (root, *remotes.values())
                        for path in tree.rglob("*") if path.is_file()
                    }

                before = snapshot()
                result = subprocess.run([
                    sys.executable, str(SCRIPT), "--root", str(root), "--base", "main",
                    *[arg for provider, branch in pairs
                      for arg in ("--hosted-branch", f"{provider}={branch}")],
                ], capture_output=True, timeout=30, env=env)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, b"")
                report = json.loads(result.stdout)
                self.assertNotIn("error", report)
                self.assertFalse(report["fetch_performed"])
                hosted = report["hosted_lifecycle"]
                self.assertEqual([(item["provider"], item["ref"])
                                  for item in hosted["entries"]], list(pairs))
                expected_blocking_codes = []
                for pair_index, entry in enumerate(hosted["entries"]):
                    expected_codes = sorted([
                        *([codes[index] for index in repeated] if pair_index == 0 else []),
                        *([] if first_count == 0 or (pair_index == 0 and 0 in repeated)
                          else ["hosted-ref-has-deployments"]),
                    ])
                    expected_blocking_codes.append(expected_codes)
                    self.assertEqual(entry["classification"], "present")
                    self.assertEqual(entry["ref_status"], "present")
                    self.assertEqual(entry["tip"], tip)
                    self.assertEqual(entry["protection"]["status"], "unprotected")
                    self.assertEqual(entry["deletion_blocked"], bool(expected_codes))
                    self.assertEqual(entry["blocking_reasons"], expected_codes)
                    for index, key in enumerate(keys):
                        if pair_index == 0 and index in repeated:
                            label = "deployments" if index == 0 else "pull-request"
                            self.assertEqual(entry[key], {
                                "status": "unknown",
                                "reason": f"GitHub {label} history incomplete at page {repeat_page}: "
                                          f"repeated full page (matches page {matching_page}); "
                                          "no short or empty page confirmed completion",
                            })
                        else:
                            fields = (("id", "sha", "ref", "environment", "created_at", "updated_at")
                                      if index == 0 else
                                      ("number", "state", "title", "merged_at", "html_url", "head", "base"))
                            items = [
                                {field: record[field] for field in fields if field in record}
                                for record in expected_histories[pair_index, index]
                            ]
                            self.assertEqual(entry[key], {
                                "status": "available", "source": "github-api",
                                "count": len(items), "items": items,
                            })
                self.assertEqual(hosted["deletion_blocked"], bool(first_count))
                self.assertEqual(hosted["blocking_entries"],
                                 [f"{provider}:{branch}" for (provider, branch), blocking_codes
                                  in zip(pairs, expected_blocking_codes) if blocking_codes])
                plan_items = [{
                        "provider": provider, "ref": branch,
                        "blocking_reasons": blocking_codes,
                        "blocking_reason_explanations": [{
                            "reason_code": code,
                            "explanation": audit_repo.HOSTED_HOLD_EXPLANATIONS[code],
                        } for code in blocking_codes],
                    } for (provider, branch), blocking_codes
                       in zip(pairs, expected_blocking_codes)]
                self.assertEqual(hosted["cleanup_plan"], {
                    "merge": [], "delete": [],
                    # Completed deployment histories legitimately require keeping
                    # the branch; only unknown evidence belongs in review here.
                    "keep": sorted(
                        [item for index, item in enumerate(plan_items)
                         if item["blocking_reasons"] and (index != 0 or 0 not in repeated)],
                        key=lambda item: (item["provider"], item["ref"]),
                    ),
                    "review": plan_items[:1] if 0 in repeated else [],
                })
                self.assertEqual([json.loads(line) for line in
                                  calls_path.read_text().splitlines()], expected_calls)
                self.assertEqual(snapshot(), before)

    def test_cli_changed_history_values_complete_without_repository_writes(self) -> None:
        real_git = shutil.which("git")
        self.assertIsNotNone(real_git)
        for final_count in (0, 99):
            with self.subTest(final_count=final_count), tempfile.TemporaryDirectory() as directory:
                fixture = Path(directory)
                root = fixture / "repo"
                root.mkdir()
                remote = fixture / "origin.git"
                bin_dir = fixture / "bin"
                bin_dir.mkdir()
                calls_path = fixture / "calls.jsonl"
                responses_path = fixture / "responses.json"
                # Isolate credentials/configuration and prohibit network transports.
                env = {
                    "PATH": str(bin_dir), "HOME": str(fixture),
                    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
                    "GIT_ALLOW_PROTOCOL": "file", "GIT_TERMINAL_PROMPT": "0",
                    "GIT_CONFIG_COUNT": "2",
                    "GIT_CONFIG_KEY_0": "gc.auto", "GIT_CONFIG_VALUE_0": "0",
                    "GIT_CONFIG_KEY_1": "maintenance.auto", "GIT_CONFIG_VALUE_1": "false",
                    "PYTHONUTF8": "1",
                    "CLI_FIXTURE_LOG": str(calls_path),
                    "CLI_FIXTURE_RESPONSES": str(responses_path),
                }
                git_path = bin_dir / "git"
                git_path.write_text(
                    f"#!{sys.executable}\n"
                    "import os, sys\n"
                    f"real_git = {real_git!r}\n"
                    "args = sys.argv[1:]\n"
                    "if args[:3] == ['ls-remote', '--heads', 'origin']:\n"
                    f"    args[2] = {str(remote)!r}\n"
                    "os.execv(real_git, [real_git, *args])\n",
                    encoding="utf-8",
                )
                git_path.chmod(0o755)

                def git(path, *args):
                    return subprocess.run(
                        ["git", *args], cwd=path, env=env, capture_output=True,
                        text=True, check=True, timeout=10,
                    ).stdout.strip()

                git(root, "init", "-q", "-b", "main")
                git(root, "config", "user.name", "Audit Test")
                git(root, "config", "user.email", "test@example.com")
                (root / "README.md").write_text("fixture\n", encoding="utf-8")
                git(root, "add", "README.md")
                git(root, "commit", "-qm", "initial")
                tip = git(root, "rev-parse", "HEAD")
                git(root, "clone", "-q", "--bare", str(root), str(remote))
                git(remote, "update-ref", "refs/heads/work", tip)
                git(root, "remote", "add", "origin", "https://github.com/fixture/repo.git")
                git(root, "branch", "work")
                git(root, "update-ref", "refs/archive/recovery", tip)
                (root / "README.md").write_text("stashed\n", encoding="utf-8")
                git(root, "stash", "push", "-qm", "retain recovery evidence")
                (root / "README.md").write_text("staged\n", encoding="utf-8")
                git(root, "add", "README.md")
                (root / "README.md").write_text("unstaged\n", encoding="utf-8")
                (root / "untracked.txt").write_text("retain\n", encoding="utf-8")

                protection_endpoint = "repos/fixture/repo/branches/work"
                responses = {protection_endpoint: {"protected": False}}
                expected_calls = [protection_endpoint]
                expected_histories = {}
                for key, query in (
                    ("deployments", "repos/fixture/repo/deployments?ref=work"),
                    ("pull_requests", "repos/fixture/repo/pulls?state=all&head=fixture%3Awork"),
                ):
                    # All full pages reuse the same IDs/numbers in the same order.
                    # Only retained, non-identity values change within this history.
                    pages = []
                    for page in range(4):
                        records = [
                            {"id": n, "ref": "work", "sha": tip,
                             "environment": f"preview-{page}"}
                            if key == "deployments" else pull_request_fixture(
                                number=n, title=f"Revision {page} for PR {n}",
                                head={"ref": "work", "repo": {"full_name": "fixture/repo"}},
                            )
                            for n in range(100 if page < 3 else final_count)
                        ]
                        pages.append(records)
                        endpoint = f"{query}&per_page=100&page={page + 1}"
                        responses[endpoint] = records
                        expected_calls.append(endpoint)
                    identity = "id" if key == "deployments" else "number"
                    self.assertEqual(
                        [[record[identity] for record in page] for page in pages[:3]],
                        [list(range(100))] * 3,
                    )
                    self.assertNotEqual(pages[0], pages[1])
                    self.assertNotEqual(pages[0], pages[2])
                    self.assertNotEqual(pages[1], pages[2])
                    expected_histories[key] = sum(pages, [])
                responses_path.write_text(json.dumps(responses), encoding="utf-8")
                gh_path = bin_dir / "gh"
                gh_path.write_text(
                    f"#!{sys.executable}\n"
                    "import json, os, sys\n"
                    "from pathlib import Path\n"
                    "assert sys.argv[1] == 'api' and len(sys.argv) == 3, sys.argv\n"
                    "endpoint = sys.argv[2]\n"
                    "with open(os.environ['CLI_FIXTURE_LOG'], 'a') as log:\n"
                    "    log.write(json.dumps(endpoint) + '\\n')\n"
                    "responses = json.loads(Path(os.environ['CLI_FIXTURE_RESPONSES']).read_text())\n"
                    "print(json.dumps(responses[endpoint]))\n",
                    encoding="utf-8",
                )
                gh_path.chmod(0o755)

                def snapshot():
                    # Check dirty files, index, reflogs, stash, recovery refs,
                    # objects, configuration, and the bare remote byte-for-byte.
                    return {
                        str(path.relative_to(fixture)): path.read_bytes()
                        for tree in (root, remote)
                        for path in tree.rglob("*") if path.is_file()
                    }

                before = snapshot()
                result = subprocess.run([
                    sys.executable, str(SCRIPT), "--root", str(root), "--base", "main",
                    "--hosted-branch", "origin=work",
                ], capture_output=True, timeout=30, env=env)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, b"")
                report = json.loads(result.stdout)
                self.assertNotIn("error", report)
                self.assertFalse(report["fetch_performed"])
                hosted = report["hosted_lifecycle"]
                self.assertEqual(len(hosted["entries"]), 1)
                entry = hosted["entries"][0]
                self.assertEqual((entry["provider"], entry["ref"]), ("origin", "work"))
                self.assertEqual(entry["classification"], "present")
                self.assertEqual(entry["ref_status"], "present")
                self.assertEqual(entry["tip"], tip)
                self.assertEqual(entry["protection"]["status"], "unprotected")
                for key, items in expected_histories.items():
                    self.assertEqual(entry[key], {
                        "status": "available", "source": "github-api",
                        "count": 300 + final_count, "items": items,
                    })
                # Completed deployments legitimately retain the branch, but no
                # repeated-page/unknown-evidence hold may leak into the plan.
                code = "hosted-ref-has-deployments"
                self.assert_single_hosted_hold(hosted, code, "keep")
                self.assertEqual(hosted["blocking_entries"], ["origin:work"])
                self.assertEqual(hosted["cleanup_plan"], {
                    "keep": [{
                        "provider": "origin", "ref": "work",
                        "blocking_reasons": [code],
                        "blocking_reason_explanations": [{
                            "reason_code": code,
                            "explanation": audit_repo.HOSTED_HOLD_EXPLANATIONS[code],
                        }],
                    }],
                    "merge": [], "delete": [], "review": [],
                })
                self.assertEqual(
                    [json.loads(line) for line in calls_path.read_text().splitlines()],
                    expected_calls,
                )
                self.assertEqual(snapshot(), before)

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
            side_effect=lambda _root, _url, branch, **kwargs: {**clear, **fixtures[branch]},
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

    def test_local_git_decode_failure_is_not_converted_to_a_hosted_hold(self) -> None:
        failure = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
        with tempfile.TemporaryDirectory() as directory, patch.object(
            sys, "argv", [str(SCRIPT), "--root", directory, "--hosted-branch", "origin=work"],
        ), patch.object(audit_repo.subprocess, "run", side_effect=failure) as run, patch.object(
            audit_repo, "audit_hosted_branches",
        ) as hosted, patch("sys.stdout", new_callable=io.StringIO) as output:
            with self.assertRaises(UnicodeDecodeError):
                audit_repo.main()
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(run.call_args.args[0], ["git", "rev-parse", "--is-inside-work-tree"])
        hosted.assert_not_called()

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
