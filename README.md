# AskJamie™

**AskJamie™** is a warm, technically literate AI helpdesk persona — your thinking partner for decisions, workflows, and systems questions across the OverKill Hill P³™ ecosystem. Built for people who need clarity, not jargon.

## What It Is

AskJamie™ lives at the intersection of human communication and AI reasoning. Think of it as the vintage tech guy who actually listens — calm under pressure, diagram-ready on demand, and always routing toward the clearest path forward.

The site documents the AskJamie™ Lens System: a public-facing portfolio of GPT architectures, BrandGuard™ case studies, and professional prototypes built under the OverKill Hill P³™ umbrella.

## What This Site Demonstrates

This repository is itself a portfolio artifact — a proof of what intentional, discipline-driven static-site development looks like in 2026.

- **Static-site discipline**: pure HTML/CSS/JS with no application compilation or client framework. Validation and allowlisted artifact preparation run before GitHub Pages deployment.
- **LLM discoverability** — `llms.txt` follows the emerging convention for AI-crawler entry points; every canonical URL is listed and machine-readable.
- **GPT portfolio packaging** — each Lens System page is a self-contained case study for a custom GPT, structured for both human and AI readers.
- **BrandGuard™ concept development** — 13 public-information proofs-of-concept showing how brand AI safety guardrails are designed and documented.
- **Agent-assisted build culture** — the site was built and maintained with the Replit AI agent; every quality gate, script, and audit convention was co-designed for that workflow.

## Site Structure

```
/                     # Homepage — what AskJamie is, who it's for
about/                # About — the strategic intelligence layer
universe/             # OKHP³ Universe — ecosystem map (Mermaid diagram)
contact/              # Contact — six labeled inquiry-path cards
legal/                # Legal — terms, privacy, BrandGuard™ disclaimer
how-askjamie-works/    # How the Lens System works and where its limits are
found-ry/             # Feature page for the private local capability workbench
search/               # Site Search — full-text client-side search
lens-system/          # Lens System hub — four purpose-built GPTs
  resume-representative/     # GPT-AJ01
  professional-portfolio/    # GPT-AJ02
  enterprise-sleuth/         # GPT-AJ03
  okhp3-brandguard/          # GPT-AJ04 hub + 13 BrandGuard case studies
    bfs-framing-intelligent-futures/
    lego/ starbucks/ brooks-running/ ping/ costco/
    hershey/ lvmh/ dollar-general/ coca-cola/
    discount-tire/ scheels/ mathews-archery/
assets/
  css/theme.css        # Single stylesheet — GLOBAL → OKH → GLEE → ASKJAMIE tiers
  js/app.js            # Single JS file — analytics, search modal, nav, GA4 events
  js/mermaid-init.js   # Mermaid v11 ESM init (universe page only)
  data/search-index.json  # Pre-built search index (26 content pages)
  img/                 # Brand assets, avatars, case study images
  docs/                # Generated docs (audit reports, QA results, specs)
  templates/           # 9 developer page templates (excluded from QA)
scripts/
  audit-site.py        # Static-site auditor: quality gates; rerun for current findings
  responsive-qa.mjs    # Playwright + static-lint QA (208 rows across 26 sitemap routes)
  build-search-index.py   # Regenerates assets/data/search-index.json
  archive/             # Reference-only and retired maintenance scripts
```

## Mermaid Runtime

The `universe/` diagram runs on Mermaid, vendored locally at
`assets/vendor/mermaid/` (not loaded from a CDN) so rendering can't break on
someone else's release schedule or outage. `assets/vendor/mermaid/VERSION`
pins the exact release; a daily `mermaid-version-watch` GitHub Action
compares it against the latest npm release and opens/updates a tracking
issue when the vendored copy falls behind -- re-vendoring is always a
deliberate, reviewed step, never automatic. `scripts/validate-site.py`
checks the VERSION pin matches the vendored bundle and that every page
rendering a live diagram carries a CSP class that actually allows Mermaid's
runtime-generated inline styles (see `scripts/csp.py`).

The reviewed vendor pin is 12.2.0. Initialization explicitly retains the
previous dagre layout, default/classic appearance and 200px flowchart label
wrapping, with per-diagram front matter taking precedence. Mermaid 12 targets
ES2024 and Safari 17.4+. Native page-map disclosures and links remain available
when diagram rendering is unavailable. See the
[compatibility review](assets/docs/mermaid-12-compatibility-2026-10-14.md) for
provenance, browser checks and unverified coverage.

## Analytics and typography

GA4 is intentionally loaded unconditionally from each public page shell with
measurement ID `G-MT9Y10YY0G`. `assets/js/app.js` provides the no-op-safe
`askJamieTrack` wrapper and records `search_open`, `gpt_click`, and
`inquiry_click` when the browser's analytics function is available. The Legal
page discloses this behavior. The repository contains no visitor export, so
it can prove the instrumentation exists but cannot prove visitor counts,
engagement, conversions, or funnel rates.

Baloo 2, Open Sans, and Kalam remain intentionally hosted by Google Fonts.
There is no local font bundle. Browser controls and privacy extensions can
limit analytics requests and cookies without blocking access to the public
pages.

The current shipped, conditional, deferred, and intentionally excluded work is
tracked in [`assets/docs/project-scorecard.md`](assets/docs/project-scorecard.md).

Translation prototyping uses exact-pair Agent Skills with Python planning,
validation, and drift detection. The `i18n Page Sync` workflow is part of that
approach. AskJamie has no configured translation pilot or published locale
routes at the September 5 inspection. See the
[translation cleanup record](assets/docs/translation-cleanup-2026-09-05/README.md)
for canonical tooling, removed duplicates, validation, and activation steps.

## What It Builds

- **Lens System** — a modular portfolio of AI case studies and GPT prototypes, each solving a real-world problem
- **BrandGuard™ Series** — custom GPT proofs-of-concept demonstrating how brands can own their AI voice before drift defines it (LEGO, Starbucks, Builders FirstSource, Brooks Running, Ping, and more)
- **Enterprise Sleuth™** — a working demo and recipe pack for building in-house AI investigator tools
- **Résumé Representative** — an AI-assisted professional portfolio tool

## Why It Matters

AI is becoming the default front door for how people find and evaluate brands. AskJamie™ exists to demonstrate — clearly, publicly, and ethically — what it looks like when a brand shows up with intention inside that space. The BrandGuard™ series uses only public information and positions itself as demonstration, not impersonation.

## Quality Gates

Run the release checks after any HTML or content change:

```bash
python3 scripts/audit-site.py --quiet        # 0 issues target across 28 source pages
node scripts/responsive-qa.mjs --static      # 200/200 static row target
python3 scripts/build-search-index.py        # rebuild after any copy change
python3 scripts/build-search-index.py --check # verify committed index is current
python3 scripts/prepare-pages-artifact.py --output .scratch/pages-review
```

The auditor checks: title/description length, canonical links, OG fields, image alt/width/height/loading, external link `noopener noreferrer`, CSP + referrer meta, theme-color, duplicate ids, broken in-page anchors, og:image file existence, sitemap ↔ disk reconciliation, search-index ↔ disk reconciliation. The site validator also guards the first meaningful use of BrandGuard™, OKHP³, OverKill Hill P³™, and Lens System with nearby plain-language definitions.

The September 5 source inventory has 36 HTML files: nine developer templates
and 28 QA-relevant source pages. The sitemap and search index contain 26
content routes. The responsive script evaluates 26 routes at eight viewport
configurations. Generated `dist-pages/` copies are not additional source pages.
Use the artifact-preparation manifest for the current public file count.

## Local Development

```bash
python3 -m http.server 5000 --bind 0.0.0.0
# Then open http://localhost:5000
```

The preview serves static source files without compilation. Release validation
uses development tools, and GitHub Actions prepares an allowlisted artifact
before publication. See `assets/docs/project-scorecard.md` for commands and
current evidence limitations.

## Explore

- **Website:** [https://askjamie.bot](https://askjamie.bot)
- **Email:** [contact@askjamie.bot](mailto:contact@askjamie.bot)
- **Parent brand:** [OverKill Hill P³™](https://overkillhill.com)
- **Sibling brand:** [Glee-fully Personalizable Tools™](https://glee-fully.tools)
- **Ko-fi:** [https://ko-fi.com/overkillhillp3](https://ko-fi.com/overkillhillp3)

---

> *Bring the messy context. Get calm, diagram-ready answers in return.*
