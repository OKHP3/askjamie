# Performance measurement and public build explanation

October 14, 2026. Issue #62, AJ-P4 and AJ-P6.

## Measurement scope

Five independent fresh-profile Lighthouse runs per route measured the homepage,
BrandGuard hub, Universe and Search. The first three homepage LCP values varied
4276-6529ms, so the series was extended to five. Each route/sample launches its
own temporary Chromium profile; storage reset is enabled. These are first
visits. Returning visits and real visitor field data were not measured.

Measured source: `2f7d415458ca8cc08f329dcdafb08f5401eb3116`. Canonical merged
main `f5d3aed2cc793b329dc7ec5e1798a729710bfe96` has the identical Git tree,
`aec1f910ee6f328399557bf6fee962cc3c120e30`. Measurement finished before About
copy and generated navigation changed. Those later edits do not establish a
new whole-site performance result.

Lighthouse 13.5.0 used Playwright Chromium 156.0.8078.4. The local CLI process
used Node 26.10.0; required CI uses the repository Node 24.21.0 pin. The source
was served over loopback with no-store responses, a quiet threaded server and
an adequate request queue. This is separate from GitHub Pages delivery.
Mobile settings were fixed: 412x823 Moto G Power emulation, device scale 1.75,
simulated 150ms RTT, 1638.4Kbps throughput and 4x CPU slowdown. Google Fonts and
analytics were not isolated. Full effective settings, sample timestamps,
metric availability, LCP selectors, shifts and raw-report hashes are in the
[derived evidence](performance-review-2026-10-14.json).

## Five-sample results

| Route | Performance median (range) | LCP median ms (range) | CLS median (range) |
| --- | --- | --- | --- |
| Homepage | 81 (68-83) | 4276 (3976-6529) | 0.037810 (0.035597-0.038060) |
| BrandGuard hub | 93 (93-93) | 3151 (3076-3152) | 0.002428 (0.002168-0.059150) |
| Universe | 89 (89-90) | 3152 (3152-3152) | 0.066771 (0.066226-0.066771) |
| Search | 86 (86-86) | 3601 (3601-3601) | 0.038162 (0.038162-0.039018) |

All 20 raw reports mark LCP as not invalidated. The homepage LCP element is the
first-session transition dialog summary, not its hero image. BrandGuard's LCP
is the transition notice paragraph; Universe's is the hero tagline; Search's
is a footer paragraph. Do not describe these values as universal hero timing.
Homepage variance and BrandGuard font-related shift variance remain observed
limitations, not demonstrated regressions against an approved current stack.

Universe CLS reproduces near 0.066 across all five samples. The raw shift audit
identifies the decorative brand-stripes region as the largest shifted element,
with smaller heading/tagline shifts and explicit web-font causes. This records
current movement before any new height rule. It does not establish that a
blanket height change would solve the underlying sequence; none was added.

## Historical reference and tooling correction

The ignored historical mobile summary was absent from the active clone. Its
original bytes were recovered from the previously verified retained archive,
not reconstructed. The derived record preserves its SHA-256 and archive
member identity. Its source is a historical Lighthouse 12.8.2 / Chromium 148
capture. The exact old Chromium patch is inferred, not independently confirmed.
Current stack and condition differences continue to require owner review before
trend interpretation or BrandGuard budget changes. No budget or approval was
changed. Historical deltas emitted by the runner are not treated as improvement
claims here.

The original route summaries called the LCP selector unavailable because they
read only Lighthouse's legacy element audit. The installed Lighthouse 13
reports that node in `lcp-breakdown-insight`. The runner now supports both
formats, preserves the legacy source when present, rejects unrelated/malformed
items, and still reports missing evidence explicitly. The raw measurement files
and original summaries were not rewritten. The derived record reads those
unchanged raw reports with the corrected extractor.

Twenty raw JSON reports, original summaries and the extraction helper are
retained privately under the dated `assets/audit/lighthouse-*` folders and
`.scratch/backlog-closeout-20261014/`. Their hash manifest permits verification.
They are excluded from the Pages release. Human-authored report and derived
metadata are review evidence, not a new approved reference set.

## Public explanation and remaining acceptance

The existing About-page section now explains readable static source, reviewed
release files, finite hosted sampling, lab measurement limits and human-agent
review. It uses the existing shell and navigation. It does not present a case
study as an available live AI service. Source generation refreshes search and
Universe provenance through the existing scripts. Runtime references in README
and Replit notes now agree with Mermaid 12.2.0 and configured Node 24; the
Replit post-merge timeout description matches the existing 480-second setting.
No runner, runtime setting, approval gate or spending limit was changed.

The post-deployment sample for main f5d3aed passes separately from local
measurement. Full exact-head CI and later hosted About delivery are recorded
in the follow-up PR. PR #90 / Replit #230 retain human screen-reader acceptance.
Firefox/WebKit and returning-visit performance remain unverified.
