import importlib.util
import json
from pathlib import Path
import shutil

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generated_content_does_not_feed_search(tmp_path, monkeypatch):
    index = load('build-search-index')
    monkeypatch.setattr(index, 'REPO_ROOT', str(tmp_path))
    (tmp_path / 'index.html').write_text('<h1>Authored title</h1><p>Before</p>'
        '<!-- AUTOGEN:UNIVERSE-MAP --><h2>Generated noise</h2><p>Injected needle</p>'
        '<!-- /AUTOGEN:UNIVERSE-MAP --><p>After</p>', encoding='utf-8')
    page = index.process_file('index.html')
    assert 'Injected needle' not in page['body']
    assert 'Generated noise' not in page['headings']
    assert 'Before' in page['body'] and 'After' in page['body']


def fixture_site(tmp_path):
    shutil.copytree(ROOT / '.agents/skills/okhp3-universe-map', tmp_path / '.agents/skills/okhp3-universe-map')
    (tmp_path / 'universe').mkdir()
    (tmp_path / 'universe/index.html').write_text('<h1>Keep this introduction</h1>\n'
        '<!-- AUTOGEN:UNIVERSE-MAP --><!-- /AUTOGEN:UNIVERSE-MAP -->', encoding='utf-8')
    (tmp_path / 'assets/data').mkdir(parents=True)
    (tmp_path / 'universe-map.config.json').write_text(json.dumps({'schema': 1,
        'sites': [{'origin': 'https://askjamie.bot', 'title': 'AskJamie', 'index': 'index.json'}]}))
    return [{'url': '/', 'title': 'AskJamie'}, {'url': '/new/', 'title': 'New <page>'}]


def test_add_remove_idempotence_and_readonly_check(tmp_path):
    adapter = load('sync-universe-map')
    rows = fixture_site(tmp_path)
    index = tmp_path / 'index.json'
    index.write_text(json.dumps({'entries': rows}))
    adapter.sync(tmp_path)
    assert adapter.sync(tmp_path) == 0
    assert adapter.sync(tmp_path, check=True) == 0
    page = tmp_path / 'universe/index.html'
    before = page.read_bytes()
    assert b'New &lt;page&gt;' in before and b'href="/new/"' in before
    assert b'click n' not in before and b'Keep this introduction' in before
    assert b'class="mermaid-scroll-wrap" aria-hidden="true" tabindex="-1"' in before
    assert b'class="mermaid" inert' in before
    assert b'data-map-node-count="2"' in before and b'flowchart LR' in before
    rows.pop()
    index.write_text(json.dumps({'entries': rows}))
    with pytest.raises(ValueError, match='Stale universe output'):
        adapter.sync(tmp_path, check=True)
    assert page.read_bytes() == before
    adapter.sync(tmp_path)
    assert '/new/' not in page.read_text()


def test_wider_groups_use_top_to_bottom_and_record_node_count(tmp_path):
    adapter = load('sync-universe-map')
    rows = fixture_site(tmp_path)
    rows.extend([
        {'url': '/second/', 'title': 'Second'},
        {'url': '/third/', 'title': 'Third'},
    ])
    index = tmp_path / 'index.json'
    index.write_text(json.dumps({'entries': rows}))
    adapter.sync(tmp_path)

    page = (tmp_path / 'universe/index.html').read_bytes()
    assert b'data-map-node-count="4"' in page
    assert b'flowchart TB' in page


def test_invalid_input_preserves_published_output(tmp_path):
    adapter = load('sync-universe-map')
    rows = fixture_site(tmp_path)
    index = tmp_path / 'index.json'
    index.write_text(json.dumps({'entries': rows}))
    adapter.sync(tmp_path)
    page = tmp_path / 'universe/index.html'
    before = page.read_bytes()
    rows.append({'url': 'javascript:alert(1)', 'title': 'Unsafe'})
    index.write_text(json.dumps({'entries': rows}))
    with pytest.raises(ValueError):
        adapter.sync(tmp_path)
    assert page.read_bytes() == before
