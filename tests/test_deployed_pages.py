"""Failure-oriented, offline checks for exact deployed-artifact sampling."""
import importlib.util
import io
import json
from pathlib import Path
import ssl
from threading import Event
import time
from urllib.error import URLError
from urllib.request import Request

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("deployed_pages", ROOT / "scripts/check-deployed-pages.py")
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)
SHA = "a" * 40


class Response(io.BytesIO):
    def __init__(self, body=b"", code=200, headers=None, url=None):
        super().__init__(body)
        self.code, self.headers, self.url = code, headers or {}, url

    def geturl(self):
        return self.url


class Fixture:
    def __init__(self, root):
        self.root, self.now, self.calls, self.overrides = root, 0, [], {}
        self.content = {}
        for number, (url, relative) in enumerate(probe.POSITIVES.items()):
            data = b"binary\x00\xff\r\n" + bytes([number])
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            self.content[url] = data

    def opener(self, redirect):
        fixture = self

        class Opener:
            def open(self, request, timeout):
                path = request.full_url.removeprefix(probe.BASE)
                fixture.calls.append((path, timeout, request.get_header("Accept-encoding")))
                override = fixture.overrides.get(path)
                item = override.pop(0) if override else None
                if isinstance(item, Exception):
                    raise item
                response = item or Response(fixture.content.get(path, b""), 200 if path in fixture.content else 404)
                response.url = response.url or request.full_url
                return response

        return Opener()

    def sleep(self, seconds):
        self.now += seconds

    def run(self, **kwargs):
        return probe.run_probe(self.root, SHA, "pages-site-" + SHA, runner_revision="b" * 40,
                               opener_factory=self.opener, clock=lambda: self.now, sleep=self.sleep, **kwargs)


@pytest.fixture
def fixture(tmp_path):
    return Fixture(tmp_path / "artifact")


def test_exact_artifact_bytes_and_evidence_labels(fixture):
    report = fixture.run()
    assert report["outcome"] == "PASS"
    assert report["logical_attempts"] == 7
    assert report["expected_source_revision"] != report["runner_revision"]
    assert report["aggregate_manifest_digest"] is None
    assert all(call[2] == "identity" for call in fixture.calls)
    assert report["paths"][1]["artifact_path"] == ".well-known/security.txt"
    assert "No remote revision" in report["evidence_limit"]


def test_missing_local_input_fails_before_requests_and_report_is_retained(fixture, tmp_path):
    (fixture.root / "index.html").unlink()
    report = fixture.run()
    assert report["error"]["classification"] == "configuration"
    assert not fixture.calls
    destination = tmp_path / "reports/report.json"
    probe.write_report(report, destination)
    assert json.loads(destination.read_text())["outcome"] == "FAIL"
    assert "does not undo" in destination.with_suffix(".md").read_text()


@pytest.mark.parametrize("status", [404, 410])
def test_explicit_exclusion_absence_passes(fixture, status):
    fixture.overrides[probe.NEGATIVES[0]] = [Response(code=status)]
    assert fixture.run()["outcome"] == "PASS"


@pytest.mark.parametrize("body", [b"homepage fallback", b"custom 404"])
def test_negative_200_is_terminal_even_if_next_response_is_absent(fixture, body):
    fixture.overrides[probe.NEGATIVES[0]] = [Response(body), Response(code=404)]
    row = fixture.run()["paths"][4]
    assert not row["passed"]
    assert len(row["attempts"]) == 1
    assert row["attempts"][0]["classification"] == "exposure_or_unexpected_status"
    assert "observed_sha256" not in row["attempts"][0]


@pytest.mark.parametrize("positive", [True, False])
@pytest.mark.parametrize("status", [401, 403])
def test_authentication_is_unknown_and_never_retried(fixture, positive, status):
    path = "/" if positive else probe.NEGATIVES[0]
    fixture.overrides[path] = [Response(code=status)]
    row = next(row for row in fixture.run()["paths"] if row["path"] == path)
    assert not row["passed"]
    assert len(row["attempts"]) == 1
    assert row["attempts"][0]["classification"] == "authentication_or_unknown"


@pytest.mark.parametrize("first", [Response(code=404), Response(code=410), Response(code=429),
                                   Response(code=503), URLError(TimeoutError("fixture timeout"))])
def test_transient_positive_failure_can_recover_with_history(fixture, first):
    fixture.overrides["/"] = [first]
    row = fixture.run()["paths"][0]
    assert row["passed"] and len(row["attempts"]) == 2
    assert row["attempts"][0]["retry_reason"]
    assert fixture.now == 5


def test_stale_same_size_bytes_can_recover_or_exhaust(fixture):
    stale = b"x" * len(fixture.content["/"])
    fixture.overrides["/"] = [Response(stale)]
    row = fixture.run()["paths"][0]
    assert row["passed"] and row["attempts"][0]["classification"] == "byte_mismatch"
    assert row["attempts"][0]["observed_sha256"] != row["expected_sha256"]
    fixture.overrides["/"] = [Response(stale) for _ in range(3)]
    report = fixture.run()
    assert report["outcome"] == "FAIL"
    assert len(report["paths"][0]["attempts"]) == 3


@pytest.mark.parametrize("headers,body,classification", [
    ({"Content-Encoding": "gzip"}, b"abc", "encoding"),
    ({"Content-Length": "99999"}, b"", "size"),
    ({"Content-Length": "nonsense"}, b"", "length"),
    ({"Content-Length": "8"}, b"short", "length"),
    ({}, b"x" * 1000, "size"),
])
def test_response_encoding_size_and_length_fail_terminally(fixture, headers, body, classification):
    fixture.overrides["/"] = [Response(body, headers=headers)]
    row = fixture.run()["paths"][0]
    assert len(row["attempts"]) == 1
    assert row["attempts"][0]["classification"] == classification


def test_tls_verification_failure_is_terminal(fixture):
    fixture.overrides["/"] = [URLError(ssl.SSLCertVerificationError("untrusted fixture"))]
    row = fixture.run()["paths"][0]
    assert len(row["attempts"]) == 1
    assert row["attempts"][0]["classification"] == "tls"


@pytest.mark.parametrize("target", ["https://other.invalid/", "http://askjamie.bot/",
                                    "https://askjamie.bot/contact/", "https://askjamie.bot/?x=1"])
def test_redirect_refused_before_followup_request(target):
    locations = []
    handler = probe.RestrictedRedirect(probe.BASE + "/", locations)
    with pytest.raises(probe.ProbeError, match="redirect"):
        handler.redirect_request(Request(probe.BASE + "/"), None, 302, "Found", {"Location": target}, target)
    assert locations[0]["resolved_url"] == target


def test_exact_canonical_redirect_has_two_hop_limit():
    locations = []
    url = probe.BASE + "/"
    handler = probe.RestrictedRedirect(url, locations)
    for _ in range(2):
        assert handler.redirect_request(Request(url), None, 302, "Found", {"Location": url}, url).full_url == url
    with pytest.raises(probe.ProbeError, match="limit"):
        handler.redirect_request(Request(url), None, 302, "Found", {"Location": url}, url)


@pytest.mark.parametrize("path", ["//evil.invalid/x", "/../AGENTS.md", "/%2e%2e/x", "/x\\y", "/x?query=1"])
def test_invalid_fixed_path_fails_before_requests(fixture, monkeypatch, path):
    monkeypatch.setattr(probe, "POSITIVES", {path: "index.html"})
    assert fixture.run()["error"]["classification"] == "configuration"
    assert not fixture.calls


def test_artifact_traversal_rejected(fixture, monkeypatch):
    monkeypatch.setattr(probe, "POSITIVES", {"/": "../private.txt"})
    assert fixture.run()["error"]["classification"] == "configuration"
    assert not fixture.calls


@pytest.mark.parametrize("ancestor", [False, True])
def test_symlink_file_or_directory_rejected_before_network(fixture, tmp_path, ancestor):
    target = tmp_path / "outside"
    target.mkdir()
    if ancestor:
        file = fixture.root / ".well-known"
        for child in file.iterdir():
            child.unlink()
        file.rmdir()
    else:
        file = fixture.root / "index.html"
        file.unlink()
        target = target / "private.txt"
        target.write_bytes(b"private")
    try:
        file.symlink_to(target, target_is_directory=ancestor)
    except OSError:
        pytest.skip("Symlink creation is not enabled on this Windows host")
    assert fixture.run()["error"]["classification"] == "configuration"
    assert not fixture.calls


@pytest.mark.parametrize("base", ["http://askjamie.bot", "https://askjamie.bot:443", "https://user@askjamie.bot",
                                  "https://askjamie.bot/?q=1", "http://127.0.0.1:8000"])
def test_cli_origin_contract_rejects_remote_and_implicit_fixture_origins(fixture, base):
    assert fixture.run(base=base)["error"]["classification"] == "configuration"
    assert not fixture.calls


def test_deadline_byte_and_attempt_budgets_stop_and_retain_failure(fixture, monkeypatch):
    monkeypatch.setattr(probe, "MAX_BYTES", 1)
    report = fixture.run()
    assert report["paths"][0]["attempts"][0]["classification"] == "budget"
    assert report["logical_attempts"] == 1 and report["response_bytes"] == 1
    assert len(fixture.calls) == 1
    monkeypatch.setattr(probe, "MAX_BYTES", 20 * 1024 * 1024)
    monkeypatch.setattr(probe, "MAX_ATTEMPTS", 1)
    report = fixture.run()
    assert report["outcome"] == "FAIL" and report["logical_attempts"] == 1
    assert report["error"]["classification"] == "budget"
    monkeypatch.setattr(probe, "MAX_ATTEMPTS", 21)
    monkeypatch.setattr(probe, "TOTAL_SECONDS", 3)
    fixture.overrides["/"] = [Response(code=503, headers={"Retry-After": "999999"})]
    report = fixture.run()
    assert report["error"]["classification"] == "deadline"
    assert report["logical_attempts"] == 1


def test_dripping_body_cannot_extend_response_deadline(fixture):
    class Drip(Response):
        def read1(self, size):
            fixture.now += 11
            return super().read1(1)

    fixture.overrides["/"] = [Drip(fixture.content["/"])]
    report = fixture.run()
    assert report["paths"][0]["attempts"][0]["classification"] == "deadline"
    assert report["paths"][0]["attempts"][0]["elapsed_seconds"] == 22


def test_network_failure_still_writes_json_and_markdown(fixture, tmp_path):
    fixture.overrides["/"] = [URLError(ConnectionError("offline")) for _ in range(3)]
    report = fixture.run()
    destination = tmp_path / "private-report/report.json"
    probe.write_report(report, destination)
    assert json.loads(destination.read_text())["paths"][0]["attempts"][2]["classification"] == "transient_network"
    assert destination.with_suffix(".md").exists()


@pytest.mark.parametrize("blocked_phase", ["headers", "body"])
def test_real_wall_deadline_stops_blocked_headers_or_chunk_framing(fixture, monkeypatch, blocked_phase):
    release, closed = Event(), Event()

    class BlockedResponse(Response):
        def read1(self, size):
            release.wait(2)
            return super().read1(size)

        def close(self):
            super().close()
            closed.set()

    class Opener:
        def open(self, request, timeout):
            if blocked_phase == "headers":
                release.wait(2)
            return BlockedResponse(fixture.content["/"], url=request.full_url)

    monkeypatch.setattr(probe, "RESPONSE_SECONDS", 0.05)
    started = time.monotonic()
    report = probe.run_probe(fixture.root, SHA, "pages-site-" + SHA,
                             opener_factory=lambda handler: Opener())
    elapsed = time.monotonic() - started
    release.set()
    assert elapsed < 0.5
    assert report["outcome"] == "FAIL" and report["logical_attempts"] == 1
    assert report["error"]["classification"] == "deadline"
    assert report["response_bytes"] == 0
    assert closed.wait(0.5)  # The daemon, not the reporting thread, closes late I/O.


def test_physical_request_budget_includes_redirects(fixture, monkeypatch):
    monkeypatch.setattr(probe, "MAX_REQUESTS", 1)

    class RedirectOpener:
        def __init__(self, handler):
            self.handler = handler

        def open(self, request, timeout):
            self.handler.redirect_request(request, None, 302, "Found", {"Location": request.full_url}, request.full_url)
            raise AssertionError("Follow-up must not start beyond budget")

    report = probe.run_probe(fixture.root, SHA, "pages-site-" + SHA, opener_factory=RedirectOpener)
    assert report["error"]["classification"] == "budget"
    assert report["http_requests_started"] == 1
    assert len(report["paths"][0]["attempts"][0]["redirects"]) == 1


def test_expected_file_size_and_artifact_name_fail_before_network(fixture, monkeypatch):
    monkeypatch.setattr(probe, "MAX_FILE", 1)
    assert fixture.run()["error"]["classification"] == "configuration"
    assert not fixture.calls
    report = probe.run_probe(fixture.root, SHA, "wrong-name", opener_factory=fixture.opener)
    assert report["error"]["classification"] == "configuration"
    assert not fixture.calls


def test_report_destination_cannot_be_published(fixture):
    with pytest.raises(SystemExit):
        probe.main(["--artifact-root", str(fixture.root), "--expected-source-revision", SHA,
                    "--runner-revision", SHA, "--expected-artifact-name", "pages-site-" + SHA,
                    "--report", str(fixture.root / "report.json")])

