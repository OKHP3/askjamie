---
name: Page-map theme baselines
description: Prevent shared theme state from leaking across per-group page-map checks.
---

Restore the pinned dark theme after each page-map group's light/dark transition sequence, and verify the map's horizontal scroll position remains unchanged during that restoration. **Why:** All generated groups share one theme control; leaving a group in light mode makes subsequent groups start from the wrong baseline and can hide regressions in pan retention. **How to apply:** When a browser test loops across groups on one page, normalize the shared theme state between iterations and capture both state and scroll position before and after normalization.
