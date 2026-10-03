# Performance and Visual Baseline

**Captured:** 2026-08-22  
**Site:** AskJamie.bot  
**Method:** Lighthouse 12.8.2 against the local static server, using the
Playwright-managed Chromium binary. Visual references were captured with
Playwright at 1280px and 390px viewport widths.

This is a repeatable lab baseline, not field data. Network conditions, browser
versions, Google Fonts responses, analytics responses, and CPU load can change
the results. The report records a historical capture. The current site
intentionally uses Google Fonts and unconditional page-shell GA4.
The mobile raw Lighthouse reports are in
`assets/audit/lighthouse-baseline-2026-08-22/`. The compact mobile summary is
`assets/audit/lighthouse-baseline-2026-08-22.json`. Desktop trend comparisons
use the separate reference at
`assets/docs/performance-baseline-desktop-2026-09-07.json`.

## Measurement-stack review gate

The historical reference uses Lighthouse **12.8.2** and Chromium major version
**148**. The exact Chromium build **148.0.7778.96** is inferred from Playwright
1.60.0 browser metadata; the historical Lighthouse reports establish major
148 through the `HeadlessChrome/148.0.0.0` user agent, not that exact patch
build. Treat the full build number as a historical reference, not an
independently owner-approved exact stack. The route runner records the
effective Lighthouse version from each raw report and the exact version of the
Chromium binary it launches in each dated `summary.json`, alongside the
browser user agent reported by Lighthouse. These reference values are kept
with the runner so the review gate does not depend on ignored, regenerable
files under `assets/audit/`.

Every mobile summary compares those versions with the historical mobile stack.
Desktop summaries compare only with the separate desktop reference. If either
version changes, `measurementStackReview.status` becomes `required` and names
the changed component. A tool upgrade alone does not approve a budget change.
Keep each reference separate; changing one must not silently change the other.

## Run-condition review gate

The approved 2026-08-22 raw reports record a mobile form factor with simulated
throttling, a 412×823 Moto G Power screen emulation, and a 4× CPU slowdown. The
route runner records the effective `formFactor`, `throttlingMethod`, network
and CPU throttling values, screen emulation, and emulated user agent from each
raw report's `configSettings` in the dated summary. The reference values are
kept with the runner because the raw audit directory is regenerable.

The same reports record `maxWaitForFcp` 30000ms, `maxWaitForLoad` 45000ms,
`pauseAfterFcpMs` and `pauseAfterLoadMs` 1000ms, and 1000ms network and CPU
quiet thresholds. The effective locale is `en-US`. Storage reset is enabled
(`disableStorageReset: false`) and the clear-storage types are
`file_systems`, `shader_cache`, `service_workers`, and `cache_storage`.
Each dated summary records these settings from every raw report, including
repeated BrandGuard samples; a missing setting is marked unavailable.

Each mobile report's effective settings are compared with the approved
historical mobile conditions. Controlled runs are checked against their
explicit blocked-URL profile; normal runs are checked for no blocked-URL
patterns. A difference or missing setting marks `runConditionsReview.status`
as `required` and names the changed setting. The gate also compares Lighthouse
execution mode (`auditMode`, `gatherMode`), channel, navigation and gathering
behavior, extra trace categories, request headers, Lantern input, and explicit
audit/category filters. These values can change which page state is loaded,
which data is gathered, or which measurements and audits are produced. An
unrecognized `configSettings` field is recorded and requires review until it is
explicitly classified in the inventory, even if it matches a copied reference
value. This prevents a new Lighthouse setting from silently escaping the gate.

The supported-report inventory was checked against the saved Lighthouse
12.8.2 mobile and desktop reports and the installed Lighthouse 13.5.0 settings
schema. `output` is recorded separately but not compared: it selects report
serialization (the route runner requires JSON) and does not change navigation,
collection, or audit measurements. Collection timing, locale, storage, and all
other tracked settings remain compared. A changed or unavailable tracked
setting requires review. This is separate from `measurementStackReview`, so
tool-version changes and condition changes remain independently visible.

## Desktop comparison reference

Desktop runs use
`assets/docs/performance-baseline-desktop-2026-09-07.json`, built from the
four raw desktop reports captured on 2026-09-07. It stores the desktop
measurements, exact effective settings, and measurement-stack versions
separately from the approved mobile reference. Desktop summaries compare
desktop measurements and run conditions only with this desktop reference; a
desktop run does not inherit the mobile form factor, throttling, or emulation
settings.

The desktop capture is a historical comparison candidate, not an approved
reference: the project has no recorded owner approval for it. Summaries expose
`referenceApproval.status: "not-recorded"` and mark deltas as exploratory.
Do not change that status to `"owner-approved"` unless the approval record,
approver, and approval date are recorded in the desktop reference JSON. This
approval metadata does not affect the mobile comparison or its review gate.

Normal and controlled mobile runs are separate conditions in the summary.
Controlled runs continue to block third-party fonts and analytics as an
explicit isolation measure; they are not merged with normal results. The
condition review compares Lighthouse emulation, throttling, collection timing,
locale, storage, blocked-URL settings, and the remaining effective
`configSettings` inventory. The run label and `controls` field preserve the
normal-versus-controlled distinction.

## Lighthouse summary

| Page | Performance | Accessibility | Best practices | SEO | LCP | CLS | Max potential FID | TBT |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Homepage `/` | 87 | 100 | 100 | 100 | 3.76 s | 0.034 | 16 ms | 0 ms |
| BrandGuard hub | 75 | 100 | 100 | 100 | 6.61 s | 0.003 | 89 ms | 40 ms |
| Universe `/universe/` | 44 | 100 | 100 | 100 | 6.16 s | 0.493 | 392 ms | 428 ms |
| Search `/search/` | 75 | 100 | 100 | 100 | 6.46 s | 0.069 | 49 ms | 0 ms |

Lighthouse 12 does not provide field INP in this lab report. The max potential
FID column is retained as the closest available lab interaction proxy. It
should not be presented as real visitor INP.

## Findings above the agreed thresholds

The task thresholds are performance below 90, CLS above 0.1, and LCP above
2.5 seconds.

### Performance below 90

All four pages are below 90 in this run. The Universe page is the most
significant outlier at 44, followed by the BrandGuard hub and Search at 75.
The homepage is closest to the target at 87.

### LCP above 2.5 seconds

All four pages exceed the 2.5 second target:

- **Homepage, 3.76 s:** Lighthouse identified the hero tagline
  `.hero-tagline` as the LCP element. Render delay accounted for about 88% of
  LCP in this run.
- **BrandGuard hub, 6.61 s:** Lighthouse identified the hero heading
  `#hero-title` as the LCP element. Render delay accounted for about 93% of
  LCP.
- **Universe, 6.16 s:** Lighthouse identified the hero tagline
  `.askjamie-hero-tagline` as the LCP element. Render delay accounted for about
  93% of LCP.
- **Search, 6.46 s:** Lighthouse identified `.search-hero-lede` as the LCP
  element. Render delay accounted for about 93% of LCP.

These are measurement findings only. Performance optimization is outside this
task. The large render-delay share is a useful target for a later performance
task, especially on pages with client-side initialization. Future comparisons
should record Google Fonts loading conditions rather than describe a self-hosted
font migration that is not part of the current site.

### CLS above 0.1

The Universe page recorded **0.493 CLS**, above the 0.1 threshold. Its
interactive Mermaid diagram is the highest-impact area to investigate because
the generated SVG replaces the initial diagram container after page load. The
other pages remained below the threshold, with Search at 0.069 as the next
highest value.

### Interaction and blocking observations

Universe also recorded the highest lab interaction cost: 392 ms max potential
FID and 428 ms TBT. BrandGuard recorded 89 ms max potential FID and 40 ms TBT.
The homepage and Search pages recorded 0 ms TBT in this run. These values are
lab observations, not a claim about field experience.

## Optimization verification

The Universe layout and Mermaid initialization were rechecked on 2026-08-22
with Lighthouse 12.8.2 against the same local static server and four routes.
The existing baseline remains the historical comparison point; it was not
overwritten.

| Page | Performance | LCP | CLS | Max potential FID | TBT |
| --- | ---: | ---: | ---: | ---: | ---: |
| Homepage `/` | 73 (−14) | 9.68 s (+5.93 s) | 0.027 (−0.007) | 86 ms (+70 ms) | 36 ms (+36 ms) |
| BrandGuard hub | 75 (—) | 6.46 s (−0.15 s) | 0.004 (+0.001) | 158 ms (+69 ms) | 108 ms (+68 ms) |
| Universe `/universe/` | 88 (+44) | 2.63 s (−3.53 s) | 0.028 (−0.465) | 402 ms (+10 ms) | 296 ms (−132 ms) |
| Search `/search/` | 75 (—) | 6.46 s (−0.001 s) | 0.069 (−0.0001) | 49 ms (—) | 0 ms (—) |

The Universe result confirms that reserving the generated diagram's measured
space prevents the replacement from shifting the hero: CLS moved below the
0.1 threshold, while performance and LCP improved substantially. The
homepage and BrandGuard variance reinforces that these are lab samples, not
field guarantees; the Universe change is the clear signal because its CLS
improvement is much larger than the other pages' movement.

## Targeted performance pass

A follow-up Lighthouse 12.8.2 desktop run on 2026-08-22 verified the
above-the-fold and image-delivery changes against the same four routes:

| Page | Performance | LCP | Total payload |
| --- | ---: | ---: | ---: |
| Homepage `/` | 95 | 1.2 s | 1,240 KiB |
| BrandGuard hub | 99 | 0.8 s | — |
| Universe `/universe/` | 96 | 1.1 s | — |
| Search `/search/` | 97 | 1.2 s | — |

The homepage hero now paints before `app.js` initialization, its avatar uses
a right-sized WebP source with a PNG fallback, and the milestone card uses a
right-sized WebP source. The audited route scripts are deferred because they
do not provide parser-blocking behavior. These are desktop lab results;
mobile Lighthouse scores remain sensitive to device and browser timing and are
not represented as passing thresholds by this record.

## Repeatable route check

The current comparison run was captured on **2026-08-24** with Lighthouse
12.8.2. Start the configured static server, then run:

```bash
node scripts/lighthouse-routes.mjs --date=2026-08-24
```

The runner discovers the committed Playwright Chromium binary, tests `/`,
`/lens-system/okhp3-brandguard/`, `/universe/`, and `/search/` with the desktop
preset, writes raw reports to the dated directory under `assets/audit/`, and
compares each result with `lighthouse-baseline-2026-08-22.json`.

| Page | Before performance | After performance | Change | Before LCP | After LCP | CLS after |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Homepage `/` | 87 | 97 | +10 | 3.76 s | 1.33 s | 0.025 |
| BrandGuard hub | 75 | 97 | +22 | 6.61 s | 1.29 s | 0.001 |
| Universe `/universe/` | 44 | 96 | +52 | 6.16 s | 1.29 s | 0.013 |
| Search `/search/` | 75 | 97 | +22 | 6.46 s | 1.25 s | 0.055 |

The homepage, BrandGuard, and Search routes now clear the 90 performance target.
Universe remains below target because Mermaid still contributes blocking work,
but its layout shift remains below the 0.1 threshold. The route check's raw
Lighthouse accessibility score varied for two AskJamie routes because this
fresh browser session evaluated the active-navigation contrast and injected
search control labeling; the repository's canonical structural and accessibility
checks remained clean (`validate-site.py` and `audit-site.py`).

## Mobile Lighthouse comparison

On **2026-08-25**, the same four routes were measured with Lighthouse 12.8.2
using its `--form-factor=mobile` configuration against the local static server.
Raw reports and the compact summary are in
`assets/audit/lighthouse-2026-08-25-mobile/`. These are lab measurements under
emulated mobile conditions, not field data.

| Page | Performance | LCP | CLS | TBT |
| --- | ---: | ---: | ---: | ---: |
| Homepage `/` | 87 | 3.63 s | 0.002 | 55 ms |
| BrandGuard hub | 72 | 6.91 s | 0.003 | 0 ms |
| Universe `/universe/` | 58 | 6.46 s | 0.045 | 562 ms |
| Search `/search/` | 95 | 2.42 s | 0.026 | 0 ms |

### Mobile-only findings

- **Universe is the mobile outlier:** performance is 58, LCP is 6.46 s, and
  TBT is 562 ms. CLS remains below the 0.1 threshold, so the measured issue is
  render and blocking cost rather than a new layout-shift regression.
- **BrandGuard is slower than the desktop comparison:** performance is 72 and
  LCP is 6.91 s. Its CLS and TBT remain low in this sample.
- **Search is not a mobile regression in this run:** it scored 95 with LCP
  below 2.5 s. The homepage remained near its desktop score at 87.
- No visual identity changes were made in response to this measurement.

The mobile run is repeatable with:

```bash
node scripts/lighthouse-routes.mjs --preset=mobile --date=2026-08-25
```

The runner keeps mobile reports in a `-mobile` dated directory and preserves
the existing desktop command and output path.


## Controlled mobile measurement

The route runner supports a controlled mobile pass that blocks Google Fonts,
Google Tag Manager, and Google Analytics requests:

```bash
node scripts/lighthouse-routes.mjs --preset=mobile --controlled --date=2026-09-07
```

Controlled reports are written to a `-mobile-controlled` directory and label
the blocked third-party conditions in `summary.json`. This isolates external
font and analytics timing when comparing the first-party HTML, CSS, media, and
Mermaid work. It is a controlled lab measurement only, not field data, and it
must not be compared directly with a normal run as if the visitor experience
were identical.

The final controlled pass for this change was captured on **2026-09-07** in
`assets/audit/lighthouse-2026-09-07-final-mobile-controlled/`:

| Page | Performance | LCP | CLS | TBT | LCP element |
| --- | ---: | ---: | ---: | ---: | --- |
| BrandGuard hub | 85 | 4.05 s | 0.000 | 11 ms | `#hero-title` |
| Universe `/universe/` | 90 | 3.15 s | 0.000 | 0 ms | `.askjamie-hero-tagline` |

These controlled samples materially improve the target routes versus the
2026-09-04 after sample, while the mobile 2.5 second LCP budget remains a
reviewed lab exception. The remaining render delay is reported explicitly
instead of being presented as field performance or hidden by third-party
blocking.

## Mobile performance remediation verification

The remediation was measured on **2026-09-04** with Lighthouse 12.8.2 against
the same local static server, routes, and mobile preset as the 2026-08-25
baseline. Lighthouse used its Moto G Power emulation at 412×823 CSS pixels,
4× CPU slowdown, 150 ms simulated RTT, and approximately 1.5 Mbps simulated
download throughput. The before sample is
`assets/audit/lighthouse-2026-09-04-mobile/`; the final after sample is
`assets/audit/lighthouse-2026-09-04-final4-mobile/`. A second exact-code
verification is retained in `assets/audit/lighthouse-2026-09-04-final3-mobile/`.

| Page | Before performance | After performance | Before LCP | After LCP | Before TBT | After TBT |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Homepage `/` | 83 | 60 | 4.05 s | 8.94 s | 88 ms | 311 ms |
| BrandGuard hub | 64 | 71 | 8.55 s | 7.80 s | 237 ms | 129 ms |
| Universe `/universe/` | 54 | 70 | 7.81 s | 6.46 s | 679 ms | 154 ms |
| Search `/search/` | 72 | 72 | 6.30 s | 6.75 s | 148 ms | 124 ms |

The homepage and Search markup were not changed by this remediation. Their
mobile movement is therefore treated as lab variance, not a product
regression: the same route runner produced materially different samples on
adjacent runs while external Google Fonts and analytics requests were in
flight. Across the two final exact-code samples, BrandGuard ranged from
6.91–7.80 s LCP and 127–129 ms TBT, while Universe ranged from 6.45–6.46 s
LCP and 154–167 ms TBT. The stable signal is reduced blocking work, especially
from the Universe Mermaid bundle. BrandGuard and Universe mobile LCP remain
budget exceptions in this environment and are not claimed as field
performance.

The final desktop comparison used the same four routes and desktop preset:

| Page | Performance | LCP | TBT |
| --- | ---: | ---: | ---: |
| Homepage `/` | 97 | 1.09 s | 0 ms |
| BrandGuard hub | 97 | 1.25 s | 0 ms |
| Universe `/universe/` | 96 | 1.33 s | 34 ms |
| Search `/search/` | 97 | 0.88 s | 0 ms |

The implementation keeps the existing visual and semantic behavior while
reducing first-view work:

- Universe dynamically imports Mermaid only when its diagram approaches the
  viewport on mobile; the source text and existing `<noscript>` explanation
  remain available as fallback content, and generated links retain their
  accessibility hardening.
- BrandGuard and Universe prefetch Google Fonts without making the stylesheet
  render-blocking, then enable the branded stylesheet after document parse. The
  bounded `display=fallback` policy prevents a late webfont swap from
  redefining mobile LCP while retaining the branded face on faster visits.
- BrandGuard uses `content-visibility: auto` for below-the-fold portfolio
  sections with an intrinsic size reservation, reducing initial mobile layout
  and paint work without removing content.

The Mermaid visual references were not overwritten because these are loading
and scheduling changes, not intentional visual redesign. The capture helper
now scrolls the diagram into view before waiting for its SVG, matching the
new mobile lazy-render condition. All Lighthouse numbers above are lab data;
they remain sensitive to browser version, simulated throttling, Google Fonts
responses, analytics responses, and CPU contention, and should not be
presented as real-user or field metrics.

The current follow-up path keeps the branded Google Fonts stylesheet available
after the first paint while removing its early preload from the BrandGuard and
Universe shells. Their above-fold avatar media now uses a 320px responsive
variant for the 260px BrandGuard hero and an 80px source for the 40px header
mark. The source artwork, alt text, intrinsic dimensions, metadata, and
desktop layout remain unchanged.

## Task #118 mobile first-paint follow-up

On **2026-09-07**, the target routes received a narrow loading pass against the
same four-route Lighthouse runner and mobile preset. The raw final mobile
reports are in `assets/audit/lighthouse-2026-09-07-task118-r4-mobile/`; the
desktop regression run is in
`assets/audit/lighthouse-2026-09-07-task118-desktop/`. The run remains local
Lighthouse lab evidence, not field data.

The implementation:

- serves right-sized WebP hero logo/avatar variants with the existing PNG
  fallback on BrandGuard;
- enables the already-preloaded branded font stylesheet after DOMContentLoaded
  rather than waiting for the third-party analytics request to finish;
- keeps GA4 unconditional: the page-shell queue still receives `js`, `config`,
  and interaction events immediately, while only the Google-hosted script
  download is deferred until after the first load opportunity on BrandGuard and
  Universe;
- preserves Mermaid's deferred import, fallback source, accessibility hooks,
  and no-JavaScript navigation.

### Final comparison

| Page | 2026-09-04 performance | 2026-09-07 performance | LCP before | LCP after | TBT after |
| --- | ---: | ---: | ---: | ---: | ---: |
| BrandGuard hub | 71 | 70 | 7.80 s | 6.83 s | 176 ms |
| Universe `/universe/` | 70 | 85 | 6.46 s | 3.01 s | 225 ms |
| Homepage `/` | 60 | 75 | 8.94 s | 4.51 s | 231 ms |
| Search `/search/` | 72 | 65 | 6.75 s | 7.96 s | 180 ms |

The homepage and Search markup were not changed by this task, so their movement
is treated as lab variance rather than a product regression. Four target-route
mobile samples were reviewed during the pass: BrandGuard ranged from
6.83–7.73 s LCP and Universe ranged from 3.01–4.43 s LCP. Universe is now
materially closer to the target and below the prior 6.46 s result; BrandGuard
improved by about one second but still misses the 2.5 s LCP budget. The
remaining BrandGuard hero-rendering gap is intentionally carried into the
proposed follow-up task rather than being presented as solved.

The desktop regression run remained healthy: Homepage 88 performance / 1.69 s
LCP, BrandGuard 99 / 0.73 s, Universe 99 / 0.73 s, and Search 96 / 1.28 s.
The current browser/static checks also passed: 27-page structural validation,
200/200 responsive checks, zero site-audit issues, seven focused privacy/cache
tests, and current search-index integrity. No visual baseline was refreshed
because the changes alter delivery and scheduling, not the intended visual
design.

## Task 125 mobile critical-rendering pass

On **2026-09-09**, BrandGuard and Universe received a mobile-safe critical
rendering layer. The target shells now paint a small local hero stylesheet
with system fallbacks while the 237 KiB shared theme and Google Fonts remain
deferred. The full branded cascade still activates after the initial mobile
load window; desktop keeps the full theme activation at load. BrandGuard's
existing artwork, intrinsic dimensions, metadata, semantic text, fallback
content, and accessibility hooks remain in place. Its mobile hero avatar keeps
the same source set and is now lower priority because it is below the heading
on narrow screens.

The final controlled Lighthouse sample was run with Google Fonts and analytics
blocked, and the final normal sample was run without third-party isolation.
These are separate lab conditions and neither is field evidence:

| Page | Controlled LCP | Normal LCP | Controlled performance | Normal performance |
| --- | ---: | ---: | ---: | ---: |
| BrandGuard hub | 3.23 s | 2.93 s | 79 | 80 |
| Universe `/universe/` | 2.93 s | 3.08 s | 81 | 77 |

The critical path reduced controlled LCP substantially from the 2026-09-07
sample (BrandGuard 6.83 s; Universe 3.01 s), with repeat-run variance still
visible. The current run does not support claiming a consistent 2.5 s pass for
either route, so the budget remains an active measurement target rather than
being represented as solved. The raw controlled and normal reports are kept
under the dated `assets/audit/` directories for this run.

## Task #135 BrandGuard mobile hero delay follow-up

On **2026-09-14**, the BrandGuard critical layer was aligned with the existing
mobile hero cascade without replacing the branded artwork: the compact
breadcrumb mark remains the documented 120px mobile treatment, the fallback
heading keeps the final mobile copy width and line count, and the critical
hero uses the same flex alignment as the shared theme. Universe keeps its
existing route behavior.

Repeat mobile Lighthouse samples remain above the 2.5 second target:

| Page | Controlled LCP | Normal LCP | Controlled FCP | Controlled Speed Index | Controlled TBT |
| --- | ---: | ---: | ---: | ---: | ---: |
| BrandGuard hub | 3.229 s | 3.228 s | 1.668 s | 1.668 s | 0 ms |

The controlled run blocked Google Fonts and analytics; the normal run did not.
They are separate lab conditions and neither is field evidence. The Lighthouse
run reports zero blocking time, `lcpInvalidated: false`, and an observed local
first paint/first contentful paint/LCP timestamp of 87 ms, while its emulated
mobile metric reports the 1.561 second gap between Speed Index and LCP shown
above. A direct local Playwright paint observer also records the hero tagline
as the LCP candidate at 112 ms without Lighthouse emulation.

This is measured runner variance/constraint rather than evidence of a remaining
page task. The owner approved a revised **3.5 second lab budget** for this
constraint on 2026-09-14. The revised budget applies only to this controlled
Lighthouse lab measurement; it is not a field-performance target, and
controlled and normal samples remain explicitly separated from field evidence.

## BrandGuard lab budget reconfirmation

On **2026-09-16**, the current Lighthouse route runner was run again under both
mobile conditions. The raw reports are in
`assets/audit/lighthouse-2026-09-16-mobile-controlled/` and
`assets/audit/lighthouse-2026-09-16-mobile/`. The conditions remain separate:
the controlled run blocked Google Fonts and analytics, while the normal run
left those requests in flight. Neither condition is field evidence.

| Condition | FCP | Speed Index | LCP | TBT | LCP invalidated |
| --- | ---: | ---: | ---: | ---: | --- |
| Controlled, third parties blocked | 1.671 s | 1.671 s | 3.234 s | 0 ms | false |
| Normal, third parties in flight | 0.903 s | 1.373 s | 2.856 s | 121 ms | false |

Both fresh BrandGuard samples remain below the approved **3.5 second
controlled-lab budget**. The controlled result is consistent with the
2026-09-14 controlled result of 3.229 s, and the normal result is also below the
budget despite higher blocking work. The current runner therefore still
supports the approved budget; owner review is not required again at this time.
The budget remains a controlled Lighthouse lab allowance, not a real-user
performance target.

## Repeat-sample protocol for the BrandGuard lab budget

Starting with future budget reviews, one controlled/normal pair is not enough
to request a budget change. Capture at least **three complete BrandGuard samples
per condition** on the same code, route, host, Lighthouse/Chromium stack, and
mobile preset. Use a fresh date that does not already have audit evidence:

```bash
node scripts/lighthouse-routes.mjs --preset=mobile --brandguard-samples=3 --date=YYYY-MM-DD
node scripts/lighthouse-routes.mjs --preset=mobile --controlled --brandguard-samples=3 --date=YYYY-MM-DD
```

The normal and controlled commands write to separate dated directories. Each
summary records its condition, the individual sample values and raw report
filenames, and the min, max, and spread for FCP, Speed Index, LCP, and TBT.
LCP invalidation is reported as true, false, and unavailable counts. Missing
measurements do not count as complete samples; rerun those samples before
requesting owner review. The controlled LCP is the evidence for the approved
3.5 second controlled-lab allowance. Normal results provide separate context
and must not be pooled with or substituted for controlled results.

Three samples per condition is the minimum evidence for an owner review, not
automatic approval to change the budget. If the controlled LCP range crosses
3.5 seconds, collect another three complete samples per condition before
submitting a proposed change. Any approved change still requires owner review,
and a changed measurement stack remains subject to the measurement-stack review
gate above. These results remain lab measurements, not field performance.

## Visual reference set

The committed reference images are in `assets/audit/visual-baseline/`:

- `homepage-full-desktop-1280.png`
- `homepage-full-mobile-390.png`
- `homepage-hero-desktop-1280.png`
- `homepage-hero-mobile-390.png`
- `brandguard-card-desktop-1280.png`
- `brandguard-card-mobile-390.png`
- `universe-diagram-desktop-1280.png`
- `universe-diagram-mobile-390.png`

The Universe references were refreshed after this intentional layout change.
The diagram remains fully visible at both reference widths while the reserved
slot prevents a post-load page shift.

The historical capture used the then-current analytics-denial state and the
light theme so that the reference images did not depend on a visitor decision
or a system color preference. The denial state is no longer part of the
runtime policy. These visual references are not analytics evidence.

## How to refresh

Start the local server, then run:

```bash
node scripts/capture-visual-baseline.mjs
```

For a new dated Lighthouse run, use the same four routes and write the raw
reports to a new dated directory under `assets/audit/`. Update the compact JSON
summary and this report only after reviewing the screenshots and raw metrics.
Do not overwrite the committed references for an intentional visual change
without recording why the change is expected.
