---
name: Browser accessible-name checks
description: Verify that interactive controls expose clear, distinct computed accessible names.
---

For browser-based checks of labeled links or actions, confirm that each computed accessible name is non-empty, matches the visible label, and uniquely identifies the control among its sibling actions. Checking only visible text or an `aria-label` attribute does not verify the name assistive technology receives.

**Why:** Accessible names can come from several sources, and repeated generic names can leave otherwise named actions indistinguishable.

**How to apply:** Use the browser's role-and-name query scoped to the relevant control group, compare it against the normalized visible label, and report the route, label, and destination when the match is missing or ambiguous.
