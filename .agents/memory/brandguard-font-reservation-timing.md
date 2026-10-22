---
name: BrandGuard font reservation timing
description: Keep fallback-font geometry reservations complete when app JavaScript reveals transition-notice controls.
---

Measure the BrandGuard transition notice only after app initialization has revealed its JavaScript-controlled opener. Apply reservations to every visible notice child, the breadcrumb, and hero copy before deferred web fonts are released. During startup, invalidate pending measurements by generation and record each reservation's viewport width. If the viewport returns to a covered width before fonts activate, measure that final width again; after fonts activate, let later resizes clear reservations and use natural layout.

**Why:** Measuring while the opener is hidden omits it from the fallback baseline. A delayed callback can also stamp the original width or leave reservations cleared after a return to that width. Re-reserving after fonts activate conflicts with the established natural-layout resize behavior.

**How to apply:** For startup-timing changes, hold app initialization and web-font requests, resize away and back before initialization completes, then verify the notice, breadcrumb, and hero reservations before releasing fonts. Keep post-font resize checks separate. When changing notice controls, confirm every visible child is included at 320px.
