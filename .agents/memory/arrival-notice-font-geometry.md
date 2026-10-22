---
name: Arrival-notice font geometry
description: Distinguish notice-panel layout shifts from normal intrinsic action sizing during web-font swaps.
---

For BrandGuard arrival notices, use the panel box to detect downstream layout movement and check child text for visibility, wrapping, clipping, and overlap. Do not fail the panel geometry gate solely because an inline action's intrinsic width changes with the branded font.

**Why:** A font swap can narrow a notice action while its label remains readable and the reserved panel, breadcrumb, and hero stay in place.

**How to apply:** Keep font-only evidence scoped to the panel geometry plus child readability and notice/breadcrumb/hero overlap checks; keep deferred-theme activation evidence separate.
