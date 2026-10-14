# Mermaid 12 compatibility review

Reviewed October 14, 2026 on the Mac AskJamie clone. Issue #41.

## Provenance and release review

The npm 12.2.0 tarball passed its publisher SHA-512 integrity check before
extraction. The complete minified ESM entry and 104 chunk modules were copied
without code edits. The package LICENSE and embedded module notices are
retained. VERSION is 12.2.0. The [provenance receipt](mermaid-12-compatibility-2026-10-14.json)
records package source, integrity, declared upstream dependencies and every
vendored file hash. The publisher tarball supplies no resolved dependency
lockfile or SBOM. Exact bundled dependency versions remain unverified;
declared dependency ranges are not a resolved inventory.

Reviewed publisher notes for [12.0](https://github.com/mermaid-js/mermaid/releases/tag/mermaid%4012.0.0),
[12.1](https://github.com/mermaid-js/mermaid/releases/tag/mermaid%4012.1.0) and
[12.2](https://github.com/mermaid-js/mermaid/releases/tag/mermaid%4012.2.0).
Version 12 changes the default layout to ELK and appearance to redux-color/neo,
removes defaultRenderer, and targets ES2024, Safari 17.4+ and Node 22.12+.
AskJamie uses no removed exports or defaultRenderer options. Initialization
explicitly preserves dagre, default/classic appearance and the previous 200px
flowchart label wrapping. The latter is important: the new 120px default made
the desktop diagram taller in the first comparison. Explicit 200px wrapping
restored the captured presentation exactly. Per-diagram front matter still
wins. Strict/loose selection, exact source click-target filtering, generated
link enhancement and native navigation were not changed.

## Local evidence

Baseline source was 6ed61ed9fef964da09e0c26bc80cc61150a8c6b6. The reviewed
candidate also includes checked audit PR #93, which does not change site
rendering. Before and after browser runs cover all six generated diagrams,
27 indexed pages, 390px and 1280px, both color schemes, label counts, SVG links,
keyboard access, horizontal scrolling, in-place theme switching and
no-JavaScript navigation. All pass. The first group screenshots at both widths
and both themes are pixel-identical after the wrapping fix. These four images
do not establish visual identity for every diagram.

CSP generation and structural validation pass. The old vendor, package tarball,
release notes, raw browser logs and screenshots are preserved privately under
`.scratch/backlog-closeout-20261014/`. Remaining release checks and exact-head
CI are recorded in the pull request. Local browser checks do not establish
hosted delivery, Firefox/WebKit support or actual screen-reader speech.

## Preserved acceptance boundary

PR #90 and Replit task #230 remain separate and held for human screen-reader
acceptance. This upgrade does not supply or waive that result. Native page-map
links remain available when the diagram cannot render. Windows runner settings
and Replit Free Mode/$0 constraints are unchanged.
