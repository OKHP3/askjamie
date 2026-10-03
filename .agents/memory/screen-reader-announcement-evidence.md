---
name: Screen-reader announcement evidence
description: Distinguishes browser accessibility-tree evidence from actual screen-reader speech verification.
---

Browser DOM attributes and the browser accessibility tree can confirm that a live region is exposed and marked polite. They do not confirm that a supported screen reader actually announces an update.

**Why:** Screen-reader speech depends on the assistive technology and browser pairing, so a browser-only test cannot establish the spoken result.

**How to apply:** For tasks that require confirmation of spoken announcements, record the screen reader, browser, and observed result from an actual assistive-technology run. Report browser-tree evidence separately and do not claim the speech check passed when that run is unavailable.