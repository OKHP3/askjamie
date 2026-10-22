---
name: BrandGuard font-outage checks
description: Keep blocked-web-font readability checks separate from normal font-swap geometry tests.
---

Use an isolated browser context for fallback-font outage checks. Block both remote font stylesheets and font files, wait for the complete local theme to become active, and then check text visibility, clipping, overflow, and overlap. Keep outage CSS rules scoped to measured viewport boundaries so they do not change neighboring font-swap layouts. Preserve existing font-swap comparisons and their independent 1px tolerance.

**Why:** A blocked-font pass found breadcrumb overflow that the ordinary font-swap baseline did not reveal. Extending the responsive rule across the adjacent 390px measurement changed fallback wrapping and shifted hero content by about 24px.

**How to apply:** Treat missing-font layout and font-loading geometry as separate states. When correcting a fallback failure, reserve the measured fallback text height only where the rule was evaluated, then rerun both the blocked-font check and the unchanged adjacent font-swap checks.

Only call a responsive font geometry comparison verified when the intended stylesheet and all required font faces actually load. If a hosted request fails, preserve its resource identity and mark the geometry as fallback-only rather than treating repeated fallback measurements as a stable font swap.

**Why:** Tablet reservation can succeed while a hosted stylesheet or font file fails, leaving two fallback measurements that look numerically stable but provide no evidence about the intended fonts.

**How to apply:** Record failed hosted resource type, path, and HTTP or network failure evidence; leave loaded-font geometry unavailable and suppress stability conclusions until expected faces load successfully.

**Why:** A reservation measured at one narrow viewport did not cover the adjacent 357–359px widths, where the same fallback heading wrapped to an extra line and shifted when the web font loaded.

**How to apply:** Keep reservations tied to tested geometry, but when adjacent tested widths share the same fallback wrap, cover that entire verified interval rather than only its first sampled width. Rerun the font-swap checks across the interval after changing the rule.
