#!/usr/bin/env python3
"""Compare a finite served sample with the exact downloaded Pages artifact.

This is post-deployment evidence, not a remote revision detector or rollback.
Only the production CLI origin is accepted. Tests inject a controlled opener.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from queue import Empty, Queue
import ssl
from threading import Event, Thread
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

BASE = "https://askjamie.bot"
POSITIVES = {"/": "index.html", "/.well-known/security.txt": ".well-known/security.txt",
             "/.well-known/discord": ".well-known/discord", "/assets/js/app.js": "assets/js/app.js"}
NEGATIVES = ("/AGENTS.md", "/scripts/prepare-pages-artifact.py",
             "/assets/templates/template--homepage.html")
MAX_FILE = 2 * 1024 * 1024
MAX_BYTES = 20 * 1024 * 1024
MAX_ATTEMPTS = 21  # Seven reviewed paths, three attempts per path.
MAX_REQUESTS = 63  # Each logical attempt permits at most two redirects.
TOTAL_SECONDS = 180
RESPONSE_SECONDS = 20
IO_SECONDS = 10
EVIDENCE_LIMIT = ("Only the selected bytes and excluded paths are verified at the recorded times. "
                  "Identical samples can occur in different revisions. No remote revision, complete "
                  "site coverage, response-header enforcement, behavior or performance is proven.")


class ProbeError(Exception):
    def __init__(self, classification, message):
        super().__init__(message)
        self.classification = classification


def checked_path(path):
    """Reject ambiguous URL paths, including encoded separators/traversal."""
    if (not path.startswith("/") or path.startswith("//") or "\\" in path
            or any(c in path for c in "%?#") or any(ord(c) < 32 for c in path)
            or any(part in (".", "..") for part in path.split("/"))):
        raise ProbeError("configuration", "Invalid fixed path")
    return path


def checked_base(base):
    if base.rstrip("/") != BASE or base not in (BASE, BASE + "/"):
        raise ProbeError("configuration", "Production origin must be exactly https://askjamie.bot")
    return BASE


def artifact_file(root, relative):
    parts = PurePosixPath(relative).parts
    if not parts or relative.startswith("/") or "\\" in relative or any(p in (".", "..") for p in parts):
        raise ProbeError("configuration", "Invalid artifact path")
    candidate = root.joinpath(*parts)
    if any(p.is_symlink() for p in (candidate, *candidate.parents)):
        raise ProbeError("configuration", "Artifact path contains a symlink")
    if not candidate.resolve().is_relative_to(root.resolve()) or not candidate.is_file():
        raise ProbeError("configuration", "Required artifact file is missing or unsafe: " + relative)
    if candidate.stat().st_size > MAX_FILE:
        raise ProbeError("configuration", "Expected artifact file exceeds 2 MiB")
    return candidate


class RestrictedRedirect(HTTPRedirectHandler):
    def __init__(self, intended, observations, before_follow=None):
        self.intended, self.observations, self.before_follow = intended, observations, before_follow

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.observations.append({"status": code, "location": headers.get("Location"), "resolved_url": newurl})
        original, target = urlsplit(self.intended), urlsplit(newurl)
        if (target.scheme, target.netloc) != (original.scheme, original.netloc):
            raise ProbeError("redirect", "Off-origin or downgraded redirect refused")
        if newurl != self.intended or len(self.observations) > 2:
            raise ProbeError("redirect", "Wrong-route redirect or redirect limit refused")
        if self.before_follow:
            self.before_follow()
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def bounded_operation(operation, wall_timeout, cancelled):
    """Bound the whole HTTP operation, including headers and chunk framing.

    A daemon owns its response until it closes. On timeout the coordinator
    never waits on a possibly locked response.close(). Late results are ignored.
    """
    result = Queue(maxsize=1)

    def worker():
        try:
            value = operation()
            if not cancelled.is_set():
                result.put((value, None))
        except Exception as error:
            if not cancelled.is_set():
                result.put((None, error))

    Thread(target=worker, daemon=True).start()
    try:
        value, error = result.get(timeout=wall_timeout)
    except Empty:
        cancelled.set()
        raise ProbeError("deadline", "HTTP response wall deadline exhausted") from None
    if error:
        raise error
    return value


def read_body(response, expected_size, deadline, clock, budget, cancelled):
    if response.headers.get("Content-Encoding", "identity").strip().lower() not in ("", "identity"):
        raise ProbeError("encoding", "Unsupported Content-Encoding despite identity request")
    length = response.headers.get("Content-Length")
    if length is not None:
        if not length.isdecimal():
            raise ProbeError("length", "Malformed Content-Length")
        if int(length) > expected_size:
            raise ProbeError("size", "Declared body exceeds expected size")
    cap = expected_size + 1
    body = bytearray()
    # read1 bounds payload reads. The outer daemon deadline also bounds
    # buffered header/chunk framing, which can perform multiple socket reads.
    while len(body) < cap:
        remaining = deadline - clock()
        if remaining <= 0:
            raise ProbeError("deadline", "Response or total deadline exhausted")
        sock = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
        if sock is not None:
            sock.settimeout(min(IO_SECONDS, remaining))
        remaining_bytes = MAX_BYTES - budget["bytes"]
        if remaining_bytes <= 0:
            raise ProbeError("budget", "Total response byte budget exhausted")
        chunk = response.read1(min(65536, cap - len(body), remaining_bytes))
        if cancelled.is_set():
            raise ProbeError("deadline", "Late body ignored after cancellation")
        budget["bytes"] += len(chunk)
        if budget["bytes"] > MAX_BYTES:
            raise ProbeError("budget", "Total response byte budget exhausted")
        body.extend(chunk)
        if clock() >= deadline:
            raise ProbeError("deadline", "Response or total deadline exhausted")
        if not chunk:
            break
    if len(body) > expected_size:
        raise ProbeError("size", "Body exceeds expected size")
    if length is not None and len(body) != int(length):
        raise ProbeError("length", "Truncated declared body")
    return bytes(body)


def run_probe(artifact_root, source_revision, artifact_name, *, base=BASE,
              runner_revision=None, opener_factory=None, clock=time.monotonic, sleep=time.sleep):
    """Return a report even on configuration or network failure; never write public files."""
    report = {"expected_source_revision": source_revision, "runner_revision": runner_revision,
              "expected_artifact_name": artifact_name, "aggregate_manifest_digest": None,
              "started_at": datetime.now(timezone.utc).isoformat(), "outcome": "FAIL",
              "evidence_limit": EVIDENCE_LIMIT, "paths": []}
    started = clock()
    budget = {"attempts": 0, "requests": 0, "bytes": 0}
    try:
        base = checked_base(base)
        if not re.fullmatch(r"[0-9a-f]{40}", source_revision) or artifact_name != "pages-site-" + source_revision:
            raise ProbeError("configuration", "Artifact name must identify the exact 40-character source revision")
        root = Path(artifact_root).absolute()
        expected = {}
        for path, relative in POSITIVES.items():
            checked_path(path)
            file = artifact_file(root, relative)
            data = file.read_bytes()
            expected[path] = {"artifact_path": relative, "expected_bytes": len(data),
                              "expected_sha256": hashlib.sha256(data).hexdigest()}
        for path in NEGATIVES:
            checked_path(path)
            if root.joinpath(path.lstrip("/")).exists() or root.joinpath(path.lstrip("/")).is_symlink():
                raise ProbeError("configuration", "Excluded path is present in the artifact")
        for path in (*POSITIVES, *NEGATIVES):
            positive = path in expected
            row = {"path": path, "kind": "positive" if positive else "exclusion",
                   **expected.get(path, {}), "attempts": [], "passed": False}
            report["paths"].append(row)
            for number in range(1, 4):
                if (budget["attempts"] >= MAX_ATTEMPTS or budget["bytes"] >= MAX_BYTES
                        or clock() >= started + TOTAL_SECONDS):
                    raise ProbeError("budget", "Total attempt or deadline budget exhausted")
                budget["attempts"] += 1
                redirects = []
                observation = {"number": number, "at": datetime.now(timezone.utc).isoformat(),
                               "status": None, "final_url": None, "redirects": redirects}
                row["attempts"].append(observation)
                attempt_start = clock()
                retry = False
                cancelled = Event()
                deadline = min(started + TOTAL_SECONDS, attempt_start + RESPONSE_SECONDS)
                try:
                    url = base + path
                    def before_request():
                        if cancelled.is_set() or clock() >= deadline:
                            raise ProbeError("deadline", "Request deadline exhausted")
                        if budget["requests"] >= MAX_REQUESTS:
                            raise ProbeError("budget", "Total HTTP request budget exhausted")
                        budget["requests"] += 1

                    handler = RestrictedRedirect(url, redirects, before_request)
                    opener = opener_factory(handler) if opener_factory else build_opener(handler)
                    request = Request(url, headers={"Accept-Encoding": "identity", "User-Agent": "AskJamie-Pages-Probe/1"})
                    before_request()
                    def inspect_response():
                        details = {}
                        passed = False
                        retryable = False
                        try:
                            response = opener.open(request, timeout=min(IO_SECONDS, deadline - clock()))
                        except HTTPError as error:
                            response = error
                        with response:
                            if cancelled.is_set():
                                raise ProbeError("deadline", "Late headers ignored after cancellation")
                            status = response.code
                            details.update(status=status, final_url=response.geturl(),
                                           content_type=response.headers.get("Content-Type"),
                                           content_encoding=response.headers.get("Content-Encoding"),
                                           content_length=response.headers.get("Content-Length"))
                            try:
                                if response.geturl() != url:
                                    raise ProbeError("redirect", "Unexpected final URL")
                                if status in (401, 403):
                                    details["classification"] = "authentication_or_unknown"
                                elif status == 429 or 500 <= status <= 599:
                                    details["classification"], retryable = "transient_service", True
                                elif not positive:
                                    passed = status in (404, 410)
                                    details["classification"] = "absent" if passed else "exposure_or_unexpected_status"
                                elif status != 200:
                                    details["classification"] = "positive_unavailable"
                                    retryable = status in (404, 410)
                                else:
                                    body = read_body(response, row["expected_bytes"], deadline, clock, budget, cancelled)
                                    details.update(observed_bytes=len(body), observed_sha256=hashlib.sha256(body).hexdigest())
                                    passed = details["observed_sha256"] == row["expected_sha256"]
                                    details["classification"] = "byte_match" if passed else "byte_mismatch"
                                    retryable = not passed
                            except ProbeError as error:
                                details.update(classification=error.classification, error=str(error))
                        return details, passed, retryable

                    details, row["passed"], retry = bounded_operation(inspect_response, deadline - clock(), cancelled)
                    observation.update(details)
                except ProbeError as error:
                    observation.update(classification=error.classification, error=str(error))
                except (URLError, OSError) as error:
                    reason = getattr(error, "reason", error)
                    retry = not isinstance(reason, ssl.SSLError)
                    observation.update(classification="transient_network" if retry else "tls", error=str(error))
                finally:
                    observation["elapsed_seconds"] = round(clock() - attempt_start, 3)
                if observation["classification"] in ("deadline", "budget"):
                    raise ProbeError(observation["classification"], observation.get("error", "Bound exhausted"))
                if row["passed"] or not retry or number == 3:
                    break
                observation["retry_reason"] = observation["classification"]
                delay = (5, 10)[number - 1]
                if clock() + delay >= started + TOTAL_SECONDS:
                    raise ProbeError("deadline", "Retry would exceed total deadline")
                sleep(delay)
        if all(row["passed"] for row in report["paths"]):
            report["outcome"] = "PASS"
    except Exception as error:
        report["error"] = {"classification": getattr(error, "classification", "unexpected"), "message": str(error)}
    report.update(logical_attempts=budget["attempts"], http_requests_started=budget["requests"], response_bytes=budget["bytes"],
                  elapsed_seconds=round(clock() - started, 3))
    return report


def write_report(report, destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    summary = ("## Served Pages sample: " + report["outcome"] + "\n\n"
               "Expected artifact: `" + report["expected_artifact_name"] + "`.\n\n"
               + "\n".join("- `" + row["path"] + "`: " + ("PASS" if row["passed"] else "FAIL") for row in report["paths"])
               + "\n\n" + EVIDENCE_LIMIT + "\n\n"
               "A failed probe occurs after deployment and does not undo or reclassify the Pages action.\n")
    destination.with_suffix(".md").write_text(summary, encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-root", required=True)
    parser.add_argument("--base", default=BASE)
    parser.add_argument("--expected-source-revision", required=True)
    parser.add_argument("--runner-revision", required=True)
    parser.add_argument("--expected-artifact-name", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args(argv)
    destination = Path(args.report).absolute()
    root = Path(args.artifact_root).absolute().resolve()
    if destination.resolve().is_relative_to(root):
        parser.error("Report must be outside the public artifact")
    report = run_probe(args.artifact_root, args.expected_source_revision, args.expected_artifact_name,
                       base=args.base, runner_revision=args.runner_revision)
    write_report(report, destination)
    print("Served Pages sample: " + report["outcome"])
    return 0 if report["outcome"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
