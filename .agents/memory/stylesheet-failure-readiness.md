---
name: Failed stylesheet readiness
description: Distinguish a failed local stylesheet from a loaded stylesheet in Chromium QA.
---

When a stylesheet request fails, do not treat the presence of `link.sheet` as proof that the CSS loaded. Confirm usable rules only when same-origin access succeeds, catch access errors, and retain the failed-request evidence separately from geometry readiness.

**Why:** Chromium may expose a `CSSStyleSheet` object after a failed request, and the parser/preload scanner can finish that request before deferred JavaScript attaches an error listener. Reading `cssRules` can also throw a `SecurityError`.

**How to apply:** Attach an error listener before activation when possible, then safely inspect same-origin rules after script initialization to recover failures whose event already fired. Record request failure separately, and report theme/geometry readiness as unavailable without usable rules.
