---
name: Responsive QA dialog settling
description: Reliable responsive checks when a page opens a native arrival dialog and restores focus on dismissal.
---

When a page opens an automatic native dialog, wait until startup has settled, dismiss it, and wait for the close event and focus restoration before measuring scroll or keyboard behavior. Run keyboard interaction checks on a fresh page so earlier tests cannot leave application state behind.

**Why:** Native dialog dismissal restores focus asynchronously and can scroll the page; shared-page tests can also inherit menu state from earlier interactions, producing false QA failures.

**How to apply:** In browser QA, wait for the dialog to open or for page initialization to complete, dismiss it, wait until it is closed, allow two animation frames, then reset scroll or begin keyboard assertions on an isolated page.
