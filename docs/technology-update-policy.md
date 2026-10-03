# AskJamie technology update policy

Prepared September 18, 2026. Scope: this repository only.

## Intended outcome

New stable releases produce a visible, tested update path. Successful
validation permits a maintainer to merge the update through the existing
pull-request release path. A version increase alone is not proof of
compatibility. No automated merge or direct push to main is configured.

The [initial review](../assets/docs/technology-review-2026-09-18/README.md)
records the stack, in-place versions, latest releases, source links, and gaps.
The [complete snapshot](../assets/docs/technology-review-2026-09-18/technology-versions.md)
includes every npm lockfile path, not just direct dependencies.

## Implemented mechanisms

| Coverage | Mechanism | Frequency | Result |
| --- | --- | --- | --- |
| npm packages, including Playwright and Lighthouse | `.github/dependabot.yml`, npm ecosystem | Monday, 9:00 a.m. America/Chicago | Update PRs with manifest and lockfile changes |
| Python QA packages | Same configuration, pip ecosystem | Monday, 9:15 a.m. America/Chicago | Update PRs for exact requirements consumed by CI |
| GitHub Actions, including SHA references | Same configuration, github-actions ecosystem | Monday, 9:30 a.m. America/Chicago | Workflow update PRs |
| Node, Python, Mermaid, packages, actions | `technology-audit.yml` and `technology-audit.py` | Monday, 15:43 UTC, or manual dispatch | Markdown summary and JSON artifact; failure when an actionable update exists or lookup fails |
| Vendored Mermaid | Existing `mermaid-version-watch.yml` | Daily | Existing review issue workflow, followed by intentional re-vendoring |
| Manifest/lockfile agreement and Python CI consumption | `technology-audit.py --check` in Site Validation | Each main push and PR | Offline gate independent of registry availability |
| Browser engine revisions | Playwright version in `package-lock.json` and its matching Chromium installed in CI | Every Playwright update | The isolated lazy-image fixture defines the supported browser boundary; other Playwright versions and browser engines are not implied |
| Fonts, analytics, Pages, Replit, standards | Existing hosted smoke checks plus the manual review below | Hosted checks daily; platform review monthly | Behavior and compatibility evidence, rather than fictional version pins |

The lazy-image compatibility check runs as its own Site Validation step against
the locked Playwright package and the Chromium build installed by that package.
It covers a normal request, a request that never starts, and one that starts
after the request deadline. Its fixture report is browser mode (`playwright`)
and is isolated from the static responsive-lint results. A Playwright upgrade
is supported only after this check passes on the new locked package/browser
pair; this does not claim compatibility with Firefox, WebKit, or separately
installed Chromium versions.

The current intended support target for this check is Chromium only. Firefox
and WebKit checks are deferred until a separate reviewed compatibility decision
explicitly names any additional target engines.

Dependabot groups minor and patch updates by ecosystem. Major updates remain
individual PRs. Five open version PRs per ecosystem allow major proposals to
coexist with a maintenance group. There are no major-version ignore rules.
Dependabot's [configuration reference](https://docs.github.com/en/code-security/reference/supply-chain-security/dependabot-options-reference)
documents these ecosystems, schedules, groups, and action tag resolution.

Dependabot version updates do not establish that repository security alerts
or security updates are enabled. Those settings were not changed. Existing
branch protection and merge settings also remain unchanged.

## Activation and acceptance

These files are prepared locally. To activate the schedules, merge this
change into the default branch through a normal PR. Then:

1. Verify Site Validation and i18n Page Sync pass on that exact PR head.
2. Confirm GitHub recognizes all three Dependabot jobs in the dependency
   update view. Run each job once and inspect the resulting proposals.
3. Manually dispatch Technology Version Audit. Existing outdated versions
   are expected to produce exit 1. A registry, parsing, or authentication
   failure produces exit 2 and must not be interpreted as current.
4. Review the summary and retained artifact. Resolve the initial upgrade
   queue below through separate tested changes.
5. After an accepted merge, verify Pages deployment and hosted smoke on the
   deployed commit. Verify Replit separately after syncing it.

Until that activation is observed, the schedules and remote runs are
unverified. Hosted schedules can be delayed or disabled; successful local
execution does not establish that GitHub will run them. Monthly, check the
last successful audit date and Dependabot job status.

## Release selection rules

- npm: use the publisher's `latest` channel and reject prerelease labels.
- PyPI: use the published stable release; reject prereleases and yanked
  latest releases. Python year-based versions are supported.
- Actions: use publisher stable releases; resolve in-place floating tags or
  SHAs through official tags. Report an unresolved SHA as unknown.
- Node: display newest Current, latest LTS, and latest patch in the configured
  major. The adoption target is latest LTS. A newer Current release does not
  by itself make an up-to-date LTS configuration fail policy.
- Python: show the newest stable minor and latest patch in the configured
  branch. The current `3.11` workflow selector floats its patch. Actual CI
  patch versions require a run log, not inference from that selector.
- Transitive npm packages: enumerate each locked version and latest release,
  but upgrade through the owning dependency's compatible tree. Do not force
  every nested package to its newest major. Transitive-only drift is
  informational, while lookup failures still make evidence incomplete.
- npm itself follows the selected Node distribution unless deliberately
  pinned after checking engine compatibility. Never install npm 12 blindly
  onto the current Node 22.19.0 pin.

All online requests are bounded. GitHub metadata uses the workflow's
read-only token; it is not sent to registries. The auditor never installs
code, rewrites a version, opens an issue, or changes GitHub settings.

## Initial upgrade queue

1. **Restore local reproducibility.** Use the configured Node version in an
   isolated environment, then `npm ci`. The workstation's Lighthouse 12.8.2
   differs from the locked 13.4.1. Use `python3 -m pip install -r
   requirements-qa.txt` in a virtual environment. Replace the workstation's
   Python 3.14.0rc1 with a stable interpreter through its normal installer.
   These host changes are not performed by this repository workflow.
2. **GitHub Actions.** Review the proposed major upgrades, especially the
   upload/download artifact pair and Pages artifact actions. Verify their
   documented artifact formats and runner requirements together before
   merge. Do not assume identical inputs mean compatible artifact formats.
3. **Playwright 1.60.0 to 1.63.0 and Lighthouse 13.4.1 to 13.5.0.** Regenerate
   `package-lock.json` through npm, install the matching Playwright browsers,
   and run the site, responsive, search, Universe, and experience checks.
   Review Lighthouse score movement against the same page/browser conditions.
4. **Node.** A same-major interim update is 22.23.2; the intended LTS migration
   is 24.21.0. Update `.node-version`, `package.json` engines, and lockfile
   root engines together. Confirm the Replit module supports the selected
   major before changing `.replit`. Run the complete suite on the candidate.
5. **Python.** Test 3.14.7 alongside current 3.11 before changing workflow
   selectors, `pyproject.toml` support policy, and the Replit module. Do not
   select Python 3.15 release candidates. The current branch's latest stable
   patch is 3.11.16. Replit availability remains a separate host check.
6. **Mermaid 11.17.2 to 12.0.0.** This changes default layouts, appearance,
   and browser requirements. Review the [upstream migration notes](https://github.com/mermaid-js/mermaid/releases/tag/mermaid%4012.0.0).
   Fetch the release archive, verify its registry integrity, preserve license
   notices, replace the complete ESM entry/chunk set, and update `VERSION`.
   Do not merely edit `VERSION` or install Mermaid into the unrelated QA npm
   tree. Run CSP generation, site validation, Universe tests, and visual
   comparisons at desktop and mobile sizes. Review accessible text, links,
   click-target restrictions, and failure fallback before merge.

Each future version audit refreshes these candidates. The dates and versions
above are a starting point, not permanent hard-coded requirements.

## Upgrade validation and recovery

`npm run check:stack` now routes to the offline technology contract check.
It checks Node and Python configuration agreement with Replit as well as
the package/lockfile pair and Python CI input. This checks configuration,
not the actual live Replit installation. A temporary dual-Python test matrix
for migration should run separately before changing the main selectors.

The existing Site Validation workflow remains the release gate: source
structure, fingerprints, links, Python tests, search freshness, full Chromium
responsive QA, JavaScript smoke, FoundRy reflow, Universe interactions,
experience checks, and canonical audit. i18n helper tests remain separate.

For a failed update, keep the PR open with the failing evidence and resolve
compatibility on its branch. If upstream breaks compatibility, record the
reason, retained version, responsible maintainer, and next review date in the
PR. Do not hide it with an indefinite ignore. A failed online lookup is
unknown, never permission to downgrade.

If a merged update breaks the live site, revert its merge through a PR and
redeploy the previously validated artifact path. Keep the version audit
visible until a compatible upgrade is available. Local, CI, Pages, and Replit
verification are separate claims.

## Manual platform and standards review

Monthly, inspect GitHub runner-image updates, Replit's supported modules and
Nix channels, Google Fonts and analytics behavior, and relevant browser
support. Use the owning OS/package manager for Git, shells, and workstation
Python/Node. Replit's `stable-25_05` channel is not interchangeable with a
NixOS release number; confirm provider support before changing it.

HTML, CSS, JavaScript, SVG, JSON, JSON-LD, YAML, TOML, and image formats are
standards interpreted by browsers or tools. Review compatibility and syntax
when needed; do not add a build framework to give them artificial package
versions. The site has no TypeScript, Vite, Tailwind, React, or Next.js build.

Vendored Mermaid internals have no local dependency lock or SBOM. Record
that provenance gap, maintain them as part of Mermaid, and retain an upstream
dependency SBOM/integrity receipt with the next re-vendor. Installed Agent
Skills are contributor support with separate source provenance; they are
outside automatic application dependency changes.

## Running the audit

```text
python3 scripts/technology-audit.py --check
python3 scripts/technology-audit.py
python3 scripts/technology-audit.py --fail-on-outdated
```

On Windows use `py -3 -X utf8` in place of `python3`. Full Python tests should
also set `PYTHONUTF8=1` so child processes use UTF-8. Online reports default
to ignored `.scratch/technology-audit/`; a dated evidence review can specify
`--output assets/docs/technology-review-YYYY-MM-DD`. GitHub's API may rate
limit unauthenticated local requests. Use an already authorized token through
`GITHUB_TOKEN` if needed; never commit it.

Exit 0 means lookup/contract success. With `--fail-on-outdated`, exit 1 means
an actionable upgrade remains; exit 2 means incomplete evidence or a broken
contract. All version proposals still require the validation path above.
