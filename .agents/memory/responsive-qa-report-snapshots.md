---
name: Responsive QA report snapshots
description: Prevent recovery and retry processes from reading partially written progress reports.
---

Write every live QA report snapshot to a temporary file in the report directory, then atomically rename it over the public report path.

**Why:** A forced-stop recovery test exposed a race where a concurrent retry read the report after truncation but before the new JSON had been written.

**How to apply:** Centralize report publication in one atomic-write helper and use it for in-progress, completed, abandoned, and early-stop states; keep archives and baselines separate from the live report path.
