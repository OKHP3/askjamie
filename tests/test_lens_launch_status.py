import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_GPT_CHECK = ROOT / "scripts/check-public-gpt-links.py"


LENS_CONTRACTS = {
    "AJ01": {
        "page": ROOT / "lens-system/resume-representative/index.html",
        "contact_id": "inquiry-paths-aj01",
        "name": "Résumé Representative",
        "url": "https://chatgpt.com/g/g-691fb05c86c881919eec171e62fe1e00-resume-representative-by-askjamietm",
    },
    "AJ02": {
        "page": ROOT / "lens-system/professional-portfolio/index.html",
        "contact_id": "inquiry-paths-aj02",
        "name": "Professional Portfolio",
        "url": "https://chatgpt.com/g/g-691fa5845230819199d4ffb89b13e9ab-professional-portfolio-by-askjamietm",
    },
    "AJ03": {
        "page": ROOT / "lens-system/enterprise-sleuth/index.html",
        "contact_id": "inquiry-paths-aj03",
        "name": "Enterprise Sleuth",
        "url": "https://chatgpt.com/g/g-691f9a52f5088191b4f552770ffb5886-enterprise-sleuth-by-askjamietm",
    },
}


def test_public_lens_pages_have_verified_chatgpt_links():
    contact = (ROOT / "contact/index.html").read_text(encoding="utf-8")

    for lens_id, contract in LENS_CONTRACTS.items():
        page = contract["page"].read_text(encoding="utf-8")
        contact_path = f"/contact/#{contract['contact_id']}"

        assert contract["url"].startswith("https://chatgpt.com/g/")
        assert contract["url"] in page
        assert f'href="{contract["url"]}"' in page
        assert "is in development" not in page
        assert f'id="{contract["contact_id"]}"' in contact
        assert contract["name"] in contact
        assert contract["url"] in contact
        assert "early access" not in page
        assert "early access" not in contact[contact.index(f'id="{contract["contact_id"]}"'):contact.index(f'id="{contract["contact_id"]}"') + 1200]


def test_lens_hub_labels_all_four_lenses():
    hub = (ROOT / "lens-system/index.html").read_text(encoding="utf-8")

    for lens_id, contract in LENS_CONTRACTS.items():
        assert f"GPT‑{lens_id} is a legacy custom GPT. Migration is in progress." in hub
        assert contract["url"] in hub
    assert '<strong class="site-status-eyebrow">Legacy GPT</strong>' in hub
    assert "original BrandGuard GPT case studies" in hub


def test_public_gpt_probe_matches_owner_verified_destinations():
    probe_source = PUBLIC_GPT_CHECK.read_text(encoding="utf-8")
    for contract in LENS_CONTRACTS.values():
        assert f'"{contract["url"]}"' in probe_source


def test_public_gpt_probe_classifies_http_statuses():
    spec = importlib.util.spec_from_file_location("public_gpt_probe", PUBLIC_GPT_CHECK)
    probe = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = probe
    spec.loader.exec_module(probe)

    assert probe.classify_status(200) == "reachable"
    assert probe.classify_status(301) == "reachable"
    assert probe.classify_status(401) == "authentication_or_private"
    assert probe.classify_status(403) == "authentication_or_private"
    assert probe.classify_status(404) == "broken_or_unpublished"
    assert probe.classify_status(410) == "broken_or_unpublished"
    assert probe.classify_status(429) == "transient_service"
    assert probe.classify_status(503) == "transient_service"
    assert probe.classify_status(451) == "unexpected_response"


def test_public_gpt_probe_retries_only_transient_results(monkeypatch):
    spec = importlib.util.spec_from_file_location("public_gpt_probe_retry", PUBLIC_GPT_CHECK)
    probe = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = probe
    spec.loader.exec_module(probe)

    results = iter([
        probe.ProbeResult("AJ01", "https://example.test", "transient_network"),
        probe.ProbeResult("AJ01", "https://example.test", "reachable", 200),
    ])
    calls = []
    monkeypatch.setattr(probe, "probe", lambda *args: calls.append(args) or next(results))
    monkeypatch.setattr(probe.time, "sleep", lambda _: None)

    result = probe.probe_with_retries("AJ01", "https://example.test", 1, 2)

    assert result.classification == "reachable"
    assert len(calls) == 2


def test_public_gpt_probe_does_not_retry_destination_status(monkeypatch):
    spec = importlib.util.spec_from_file_location("public_gpt_probe_no_retry", PUBLIC_GPT_CHECK)
    probe = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = probe
    spec.loader.exec_module(probe)

    calls = []
    monkeypatch.setattr(
        probe,
        "probe",
        lambda *args: calls.append(args)
        or probe.ProbeResult("AJ01", "https://example.test", "broken_or_unpublished", 404),
    )

    result = probe.probe_with_retries("AJ01", "https://example.test", 1, 2)

    assert result.classification == "broken_or_unpublished"
    assert len(calls) == 1
