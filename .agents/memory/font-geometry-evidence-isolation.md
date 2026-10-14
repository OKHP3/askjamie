---
name: Font geometry evidence isolation
description: Keep responsive web-font geometry evidence separate from deferred-theme activation.
---

Responsive geometry checks must isolate one activation source at a time. Establish the active base theme before measuring the fallback-font baseline, then release web-font files separately. If fonts are intentionally deferred to protect layout, reserve the route's measured fallback geometry before activating the font stylesheet; do not substitute a wider tolerance.

**Why:** Combining theme activation and font loading produced shifts that could not be attributed to either change, while hardcoded dimensions did not match all route content.

**How to apply:** Keep separate browser gates and evidence for deferred theme CSS and web-font loading. For any new viewport band, ensure the baseline reflects the active shell styles while font files remain blocked, and verify the normal tolerance.
