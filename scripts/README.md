# AskJamie maintenance scripts

This directory contains the scripts that are safe to use for the current
AskJamie repository. The active scripts are the only scripts kept at this
level. Historical and manual tools are preserved in `scripts/archive/` so they
cannot be mistaken for current pipeline commands.

## Classification

| Script | Classification | Use |
| --- | --- | --- |
| `audit-site.py` | active | Canonical site audit |
| `technology-audit.py` | active | Check manifest consistency offline or report stable publisher releases online |
| `build-search-index.py` | active | Rebuild search and then refresh the universe map |
| `sync-universe-map.py` | active | Generate/check the universe page and public provenance from the installed skill |
| `cache-bust.py` | active | Generate/check LF-normalized shared asset hashes, deferred app loading, and the brand import map |
| `check-links.py` | active | Check internal and external links |
| `prepare-pages-artifact.py` | active | Build the allowlisted static release artifact |
| `validate-site.py` | active | Structural site validation, including external-font-origin regression checks |
| `check-critical-theme-drift.py` | active | Verify the first-viewport critical CSS contract against the shared AskJamie theme |
| `sync-foundation-files.py` | active | 3-way sync of theme.css/app.js/mermaid-init.js across the three sibling repos |
| `responsive-qa.mjs` | active | Responsive QA entry point |
| `experience-qa.cjs` | active | Focused Playwright checks for AskJamie search, resilient content visibility, and footer contrast |
| `post-merge.sh` | active | Post-merge rebuild and validation hook |
| `capture-visual-baseline.mjs` | active | Capture the dated visual reference set |
| `check-public-gpt-links.py` | active | Opt-in reachability probe for public AJ01–AJ03 destinations |
| `lighthouse-routes.mjs` | active | Run four-route desktop/mobile passes with optional repeated BrandGuard mobile samples |

The following scripts are **reference-only**. They may still be useful for a
deliberately scoped maintenance or migration task, but they are not part of
the current validation or release pipeline: `apply-modern-baseline.py`,
`audit-assets.py`, `audit-meta-versions.py`,
`check-accent-contrast.py`, `cross-site-sync.py`, `enhance-pages.py`,
`extract-templates.py`, `fix-image-performance.py`,
`fix-placeholder-gpt-links.py`, `generate-illustrations.py`,
`generate-templates.py`, `inject-gpt-icon-picture.py`,
`inject-keep-exploring.py`, `modernize-pages.py`,
`move-orphans-to-library.py`, `normalize-head.py`, `picture-upgrade.py`,
`png-to-webp.py`, `remove-deprecated-meta.py`, `rename-img-kebab.py`,
`reorg-theme-css.py`, `responsive-audit.py`, `sync-portfolio-stats.py`,
`update-card-srcsets.py`, and `update-placeholder-dimensions.py`.

The following scripts are **retired**. They are preserved for history only and
must not be run against AskJamie: `activate-icons.py`,
`add-toolbox-to-footer.py`, `convert-gpt-icons-webp.py`,
`convert-hero-webp.py`, `generate-feed.py`, `inject-breadcrumb.py`,
`inject-color-scheme-init.py`, `inject-hero-picture.py`, `inject-jsonld.py`,
`inject-nav-logo-webp.py`, `inject-showcase-footer.py`,
`inject-showcase-subnav.py`, `push-to-github.py`, `release-mtb.py`,
`run-viewport-qa.py`, `viewport-qa.py`, and `wire-illustrations.py`.

All reference-only and retired Python scripts live in `scripts/archive/`.
Read their headers and review their target paths before adapting any of them.
The release-check regression test uses the active table above as its command
allowlist and scans CI plus `post-merge.sh`; historical documentation is not
part of that executable-command check.

## Focused experience checks

With a local server running and Playwright plus Chromium already available,
run `node scripts/experience-qa.cjs`. The default origin is local; `BASE_URL`
selects a different test origin and `QA_OUTPUT` selects a result file. Inspect
all results. These focused checks complement the existing responsive and
JavaScript smoke suites; they do not replace human assistive-technology tests.

## Shared asset freshness

After changing a shared CSS/JS asset, run `python3 scripts/cache-bust.py`,
then `python3 scripts/generate-csp.py` to refresh the inline import-map hash.
CI runs `python3 scripts/cache-bust.py --check` without writing files.
The generator owns `AUTOGEN:SHARED-ASSETS` references and the
`AUTOGEN:BRAND-IMPORT-MAP` block in tracked production pages and templates.
It preserves shared asset bytes and routes; SHA-256 prefixes normalize CRLF/LF.
The import map versions the brand module loaded by the shared app, keeping
analytics initialization in the existing dynamic-import order.
The archived cache tool remains historical provenance only.

## Public GPT reachability

Run `python3 scripts/check-public-gpt-links.py` when verifying the three live
ChatGPT destinations. `reachable` means the final response is 2xx or 3xx.
`broken_or_unpublished` covers 404/410, while `authentication_or_private`
covers 401/403. `transient_service` covers rate limits and 5xx responses;
`transient_network` covers timeouts and connection failures. Investigate and
rerun transient results before treating them as destination status changes. The
scheduled workflow uses `--retries 2`, which retries only those two transient
classes and never retries a 401/403 or 404/410 destination result.
This probe never edits site content and is intentionally not part of the
static `check-links.py` run.

## Google Fonts runtime policy

Public pages use the declared Google Fonts stylesheet for Baloo 2, Open Sans,
and Kalam. The page CSP explicitly allows `fonts.googleapis.com` for the
stylesheet and `fonts.gstatic.com` for the resulting font files. The static
site validator checks the page structure and generated CSP alignment; it does
not reject those declared font origins.

## Provenance

This active/reference-only/retired classification, and the `scripts/archive/`
convention, were ported to `overkill-hill/scripts/README.md` and
`glee-fullytools/scripts/README.md` on 2026-08-30 -- both sibling repos now
use this same scheme for their own `scripts/` directories. See
`overkill-hill/docs/sxs-infrastructure-audit-2026-08-29.md` for the
cross-repo classification evidence.
