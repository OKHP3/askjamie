import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("cache_bust", ROOT / "scripts/cache-bust.py")
cache = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cache)


def test_fingerprints_are_portable_and_change_with_content(tmp_path):
    asset = tmp_path / "app.js"
    asset.write_bytes(b"one\ntwo\n")
    expected = cache.file_hash(asset)
    asset.write_bytes(b"one\r\ntwo\r\n")
    assert cache.file_hash(asset) == expected
    asset.write_bytes(b"one\ntwo changed\n")
    assert cache.file_hash(asset) != expected


def test_generator_handles_unversioned_stale_preload_and_brand_import():
    hashes = {asset: "1234abcd" for asset in cache.ASSETS}
    source = '''<link rel="preload" href="/assets/css/theme.css" as="style">
<link rel="stylesheet" href="assets/css/theme.css?v=old">
<script src="/assets/js/app.js?mode=test#fragment"></script>
<script type="module" src="/assets/js/mermaid-init.js"></script>
<script src="https://example.com/assets/js/app.js"></script>'''
    result = cache.rewrite_one(source, hashes)
    assert result.count("theme.css?v=1234abcd") == 2
    assert 'defer src="/assets/js/app.js?mode=test&v=1234abcd#fragment"' in result
    assert '"/assets/js/askjamie-analytics.js":"/assets/js/askjamie-analytics.js?v=1234abcd"' in result
    assert result.index('type="importmap"') < result.index('<script defer')
    assert '<script type="module" src="/assets/js/mermaid-init.js?v=1234abcd"' in result
    assert 'src="https://example.com/assets/js/app.js"' in result
    assert cache.rewrite_one(result, hashes) == result
    hashes['assets/js/askjamie-analytics.js'] = 'abcd1234'
    changed = cache.rewrite_one(result, hashes)
    assert 'askjamie-analytics.js?v=abcd1234' in changed
    assert changed.count('type="importmap"') == 1


def test_check_is_read_only_and_detects_asset_drift(tmp_path, monkeypatch):
    for asset in cache.ASSETS:
        path = tmp_path / asset
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"content\n")
    page = tmp_path / "index.html"
    page.write_text('<script src="/assets/js/app.js"></script>', encoding="utf-8")
    monkeypatch.setattr(cache, "ROOT", tmp_path)
    monkeypatch.setattr(cache, "iter_html_files", lambda root: iter([page]))
    original = page.read_bytes()
    assert cache.main(["--check"]) == 1
    assert page.read_bytes() == original
    assert cache.main([]) == 0
    generated = page.read_bytes()
    assert cache.main(["--check"]) == 0
    (tmp_path / "assets/js/app.js").write_bytes(b"changed\n")
    assert cache.main(["--check"]) == 1
    assert page.read_bytes() == generated


def test_missing_asset_reports_release_check_before_hashing_or_writing(
    tmp_path, monkeypatch, capsys
):
    missing_asset = cache.ASSETS[0]
    for asset in cache.ASSETS[1:]:
        path = tmp_path / asset
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"content\n")
    page = tmp_path / "index.html"
    page.write_text('<script src="/assets/js/app.js"></script>', encoding="utf-8")
    original = page.read_bytes()
    monkeypatch.setattr(cache, "ROOT", tmp_path)
    monkeypatch.setattr(cache, "iter_html_files", lambda root: iter([page]))

    def hashing_must_not_start(path):
        raise AssertionError("assets must be validated before hashing")

    monkeypatch.setattr(cache, "file_hash", hashing_must_not_start)
    assert cache.main(["--check"]) == 1

    captured = capsys.readouterr()
    assert (
        f"ERROR: Missing shared asset required by the release fingerprint check: "
        f"{missing_asset}" in captured.err
    )
    assert (
        "Affected release check: python3 scripts/cache-bust.py --check"
        in captured.err
    )
    assert page.read_bytes() == original


def test_canonical_inventory_excludes_generated_pages_includes_templates():
    pages = [p.relative_to(ROOT).as_posix() for p in cache.iter_html_files(ROOT)]
    assert 'index.html' in pages
    assert 'found-ry/index.html' in pages
    assert 'assets/templates/template--homepage.html' in pages
    assert not any(p.startswith(('dist-pages/', '.scratch/', 'assets/audit/', '.agents/')) for p in pages)
