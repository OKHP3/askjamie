---
name: Font geometry evidence isolation
description: Keep responsive web-font geometry evidence separate from deferred-theme activation.
---

Responsive geometry checks must isolate one activation source at a time. Establish the active base theme before measuring the fallback-font baseline, then release web-font files separately. If fonts are intentionally deferred to protect layout, reserve the route's measured fallback geometry before activating the font stylesheet. Keep font evidence on its own tolerance constant, even when its current value matches the theme check; do not substitute a wider tolerance.

**Why:** Combining theme activation and font loading produced shifts that could not be attributed to either change, while hardcoded dimensions did not match all route content.

**How to apply:** Keep separate browser gates, evidence fields, and tolerance constants for deferred theme CSS and web-font loading. For any new viewport band, ensure the baseline reflects the active shell styles while font files remain blocked, and verify the font check's own tolerance.

At 320px on BrandGuard routes, text line-count changes may be diagnostic rather than failures only when the font-specific block geometry stays within 1px and both fallback and branded states remain readable, unclipped, and non-overlapping.

**Why:** Measured 320px pages changed some text blocks by one line after fonts loaded while their reserved boxes stayed stationary and neither state clipped or overlapped content.

**How to apply:** Keep line counts in font-loading evidence, enforce the separate 1px block-geometry tolerance, and run content-integrity checks on both sides of the font swap. Do not infer stability from the loaded-font state alone.

For mobile navigation font-swap checks, compare stable block-level link boxes and separately count rendered text lines; raw inline glyph bounds can change solely because the fallback and branded fonts have different metrics. **Why:** The labels were wrapping consistently, but the inline link rectangles changed width and height enough to fail a 1px gate without indicating a layout jump. **How to apply:** Keep the first-paint and full-theme mobile link-box rules aligned, then verify both box geometry and line counts at each tested viewport.

For BrandGuard routes that reserve fallback typography before loading web fonts, keep the font stylesheet inactive throughout the full tablet-and-larger range until each route's geometry is reserved. **Why:** At the 900px media boundary, fonts activated before the tablet-only reservation ran, shifting hero content by 25–82px across every case study. **How to apply:** Keep page-level font media conditions, reservation requirements, and QA baselines synchronized at breakpoint edges; verify a real font-file load after reservation and require the separate 1px geometry tolerance.

At 341–370px, allow long BrandGuard breadcrumb labels to wrap, but reserve enough breadcrumb height for the fallback-font line count so the hero does not move when branded fonts load. **Why:** Wrapping corrected real viewport overflow but fallback fonts used one extra line, shifting the hero by about 24px. **How to apply:** Keep the narrow wrapping and fallback-height reservation in both shared and critical CSS, and verify both font states at the breakpoint edges.

When app initialization reveals a geometry-affecting control, a generic reservation marker is not sufficient readiness evidence: if the route contains that control, require it to be visible and reserved before capturing the fallback-font baseline. **Why:** The BrandGuard hub notice does not include the dialog control used on case-study pages, so requiring it universally falsely fails the hub; where present, it is revealed by app.js and can shift later content. **How to apply:** Test for control presence per route, require visibility and reservation only when present, and always require the common notice blocks; delay app.js and hold font files independently when testing late initialization.
