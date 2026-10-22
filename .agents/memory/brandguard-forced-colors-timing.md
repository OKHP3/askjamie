---
name: BrandGuard forced-colors timing
description: Avoid transient opacity failures when measuring BrandGuard hero text in forced-colors browser checks.
---

In desktop browser checks, scroll the BrandGuard hero into view and wait for its finite scroll-reveal animation to finish before judging text visibility. Theme readiness and `document.fonts.ready` do not guarantee the reveal animation has settled.

**Why:** Forced-colors emulation exposed a transient zero-opacity ancestor during the hero's 0.5-second entrance animation. Measuring too early falsely reported all hero text as hidden on some case studies.

**How to apply:** Keep forced-colors checks isolated from normal color-scheme measurements, settle the hero reveal before capturing geometry, and preserve the existing checks' independent timing assumptions.
