---
name: BrandGuard phone action geometry
description: Font-swap layout behavior for reserved BrandGuard hero action groups at 320px.
---

At 320px, BrandGuard font reservation gives direct children of the hero copy a fallback-derived minimum height. A wrapped `.hero-actions` flex group can then stretch its rows to fill that reserved height; when a neighboring note reflows after the web font loads, the button rows move even if their own target sizes are fixed. Keep the narrow action links at a fixed, adequate block size and use `align-content: flex-start` so unused reserved space does not redistribute between rows.

**Why:** button-only geometry checks exposed vertical row shifts caused by flex alignment, not by button dimensions or deferred-theme activation.

**How to apply:** for BrandGuard font-only comparisons, settle the deferred theme first, hold branded font requests for the fallback capture, and inspect action container plus individual link geometry before and after font load. Keep this scoped to the narrow width where the reservation applies.

Keep touch-target audits scoped to the controls named by the task: hub case-study cards and detail-page CTAs are separate groups, while site chrome and inline editorial links are not equivalent button targets. **Why:** A page-wide anchor audit reported unrelated header, banner, footer, and editorial links as failures, and cross-group overlap checks treated independent hub cards and later CTAs as collisions. **How to apply:** choose selectors from the intended control role and page region, then evaluate target size and overlap only within that group.
