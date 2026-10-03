---
name: Mobile critical-rendering evidence
description: Durable guidance for separating critical CSS improvements from Lighthouse variance on mobile hero routes.
---

Mobile critical-rendering work should keep the first viewport paintable with a small local stylesheet and system-font fallback, while deferring large shared theme/font work without hiding or rewriting content. Treat controlled third-party-isolated Lighthouse samples and normal samples as separate lab evidence; neither is field evidence.

**Why:** Large late cascades can dominate hero render delay, while repeated Lighthouse runs on the same static route can vary substantially under CPU/network emulation. Reporting one favorable run as a budget pass is misleading.

**How to apply:** Keep the critical stylesheet in the cache-busting asset registry, preserve intrinsic image dimensions and accessible source content, compare controlled and normal runs in separate dated reports, and only claim a budget pass when repeat evidence supports it.

When deferred fonts move a hero, measure the content above it and the breadcrumb before changing the hero itself; an upstream line-wrap change can account for downstream vertical movement. Compare line counts under fallback and loaded faces, and prefer small tracking adjustments that preserve wraps over fixed `min-height` spacers that leave permanent blank space.

**Why:** A visually displaced block can be a downstream symptom, while fixed reservations may pass geometry checks but still leave visible empty space.

**How to apply:** Compare the same viewport before and after font release, inspect preceding text-block heights and breadcrumb wrapping, and keep the geometry gate enabled while resolving the underlying wrap difference.

Font-swap checks must use each route's actual page root and active stylesheet mode. A hub and its case studies can have different root classes, so a hub-only pass does not prove case-study rules took effect. **Why:** font tuning that is correct for one route can silently miss the same-looking content on another route. **How to apply:** Verify computed styles and before/after boxes on each representative route, then run the full browser gate rather than relying only on a simulated font-family swap.