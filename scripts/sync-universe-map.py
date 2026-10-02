#!/usr/bin/env python3
"""Generate the AskJamie universe page from the installed portable skill."""
import argparse
import hashlib
import html
import importlib.util
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
START = "<!-- AUTOGEN:UNIVERSE-MAP -->"
END = "<!-- /AUTOGEN:UNIVERSE-MAP -->"
REFERRAL = ('<p class="mermaid-referral-note">Diagram rendered with Mermaid.js. '
            '<a class="mermaid-referral-link" href="https://mermaidchart.cello.so/UhVlNtC2MlS" '
            'target="_blank" rel="noopener noreferrer">Try Mermaid.AI</a></p>')


def generate(root=ROOT):
    package = root / ".agents/skills/okhp3-universe-map"
    spec = importlib.util.spec_from_file_location("universe_generator", package / "scripts/build-universe-map.py")
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    outputs = generator.build(root / "universe-map.config.json")
    report = json.loads(outputs["universe-map.json"])
    nodes = {node["id"]: node for node in report["nodes"]}
    blocks = [START, '<section class="content-section container universe-map-generated" aria-labelledby="indexed-map-title">',
              '<h2 id="indexed-map-title">Explore AskJamie by page</h2>',
              '<p>These maps follow the public search index. Open a group to explore its diagram and page links. '
              'Published page means a page exists, not that the project or tool is complete. Diagrams scroll sideways on small screens.</p>']
    for number, diagram in enumerate(report["diagrams"], 1):
        parent = nodes[diagram["parent"]]
        title = html.escape(parent["title"])
        source = outputs[diagram["file"]]
        # Wider page groups lay out better top-to-bottom: the maps remain
        # horizontally scrollable, while the rendered SVG uses much less height.
        if len(diagram["nodes"]) > 2:
            source = re.sub(r"^flowchart\s+LR\b", "flowchart TB", source, count=1, flags=re.M)
        # Navigation is added from validated ordinary links after strict rendering.
        source = re.sub(r"^\s*click\s+.*$", "", source, flags=re.M)
        source = re.sub(r"^\s*classDef\s+.*$", "", source, flags=re.M)
        blocks.extend([f'<details class="card universe-map-group" data-map-node-count="{len(diagram["nodes"])}"><summary>{title} (group {number})</summary>',
                       '<figure class="askjamie-mermaid-shell">',
                       '<div class="mermaid-scroll-wrap" aria-hidden="true" tabindex="-1">',
                       f'<div class="mermaid" inert data-diagram-label="{title}">{html.escape(source)}</div>',
                       '</div>',
                       '<p class="mermaid-render-status" data-mermaid-failure-status role="status" aria-live="polite" hidden></p>',
                       REFERRAL, f'<figcaption>Pages beneath {title}. The links below provide the same navigation.</figcaption>', '</figure>', '<ul class="link-list">'])
        for key in diagram["nodes"]:
            node = nodes[key]
            label = html.escape(node["title"])
            if node["url"]:
                url = node["url"]
                if url.startswith("https://askjamie.bot/"):
                    url = url[len("https://askjamie.bot"):]
                ident = "n" + hashlib.sha256(key.encode()).hexdigest()[:16]
                label = f'<a data-universe-node="{ident}" href="{html.escape(url, quote=True)}">{label}</a>'
            blocks.append(f'<li>{label}. {html.escape(node["status"])}. {html.escape(node["description"])}</li>')
        blocks.extend(['</ul>', '</details>'])
    blocks.extend(['</section>', END])
    page_path = root / "universe/index.html"
    page = page_path.read_text(encoding="utf-8")
    if page.count(START) != 1 or page.count(END) != 1:
        raise ValueError("Expected exactly one generated universe block")
    updated = re.sub(re.escape(START) + r".*?" + re.escape(END), lambda _: "\n".join(blocks), page, flags=re.S)
    # Report remains machine-readable and public; source previews stay separate.
    return {page_path: updated, root / "assets/data/universe-map.json": outputs["universe-map.json"]}


def sync(root=ROOT, check=False):
    outputs = generate(root)
    stale = [path for path, content in outputs.items()
             if not path.exists() or path.read_text(encoding="utf-8") != content]
    if check and stale:
        raise ValueError("Stale universe output: " + ", ".join(str(p.relative_to(root)) for p in stale))
    if not check:
        for path in stale:
            path.write_text(outputs[path], encoding="utf-8", newline="\n")
    return len(stale)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        changed = sync(check=args.check)
        print(f"Universe map {'checked' if args.check else 'generated'}; {changed} changed files.")
        return 0
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
