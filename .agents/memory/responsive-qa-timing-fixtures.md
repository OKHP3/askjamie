---
name: Responsive QA timing fixtures
description: Keep browser timing tests deterministic around readiness-warning thresholds.
---

For browser fixtures that verify a readiness warning threshold, place delayed-resource cases clearly inside and outside the cutoff rather than within a few hundred milliseconds of it, especially when the runner repeats the route across many viewports.

**Why:** A 4.5-second delay intermittently triggered a 5-second `DOMContentLoaded` warning during a 20-viewport test, making the nominally “inside” case depend on runner scheduling.

**How to apply:** Preserve checks on both sides of the configured threshold, but leave enough time margin that ordinary browser and host scheduling variance cannot change the expected classification.

Set the subprocess timeout for a multi-viewport browser fixture using its full-suite runtime under load, not only its isolated runtime. **Why:** A fixture that passed alone exceeded its 90-second subprocess limit in the complete Python suite. **How to apply:** Preserve bounded timeouts, but include enough headroom for browser startup and host contention across all viewport rows.

For delayed browser markers, measure delay from when the runner begins waiting, not just from page load, or use a delay comfortably beyond navigation/setup time. **Why:** A 200 ms page-load timer left only 16 ms inside a later reservation wait. **How to apply:** Prefer an explicit synchronization signal; if using timers, pair a clearly delayed marker with a larger bounded wait timeout.

When testing a delayed `DOMContentLoaded` warning, keep the document-commit navigation timeout generous enough for a loaded runner to receive the response, and put the blocked resource delay well beyond the readiness-warning cutoff. **Why:** A 500 ms commit timeout and 6.2 s resource delay intermittently produced navigation failures instead of the intended 5 s readiness warning across a 20-viewport test. **How to apply:** Give commit detection several seconds of headroom, then use a delay with a clear margin beyond the warning threshold.

For multi-viewport fixtures that test a near-deadline resource warning, avoid requiring every viewport to produce the same timing classification. **Why:** A lazy-image delay only 200 ms beyond a 5 s threshold sometimes exceeded the additional grace window under full-suite load. **How to apply:** Keep every viewport's result valid, but require the intended late-warning path to be exercised at least once unless the fixture synchronizes the delay to the start of observation.

When a stylesheet failure drives a visible fallback, do not assert the fallback immediately after enabling the stylesheet. Wait for the resource-error outcome and the resulting UI state. **Why:** Changing a link's `media` to `all` only starts the request; its failure event and page update arrive later. **How to apply:** In browser QA, wait for the failure notice to become visible before checking its semantics and surrounding controls.
