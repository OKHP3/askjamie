---
name: Post-merge timeout diagnosis
description: Distinguish hook-level timeout fallout from an underlying Playwright browser problem.
---

A Playwright “Target page, context or browser has been closed” error at the configured post-merge timeout can be fallout from the hook terminating its script, not an independent browser failure.

**Why:** The browser trace can point to the page action interrupted by the hook rather than the actual cause. A successful full run with a larger timeout confirms timeout fallout when other checks pass.

**How to apply:** Compare elapsed time with the configured limit and responsive-QA progress. Estimate the full run, add buffer for workflow contention, update the setting through the post-merge configuration callback, and verify with a complete setup run.