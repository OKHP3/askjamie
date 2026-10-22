---
name: Universe link navigation waits
description: Avoid evaluating a newly opened page before its destination navigation commits.
---

When testing a link opened in a new tab, wait for the expected destination URL and `domcontentloaded` before evaluating the page. A new-page event can arrive while the tab is still at `about:blank`; its initial load state is not evidence that the destination document is ready.

**Why:** Responsive QA intermittently evaluated the new tab during its transition from `about:blank`, producing a destroyed execution context despite the destination loading successfully.

**How to apply:** Keep the response check for the expected navigation request, then wait for the opened page to reach the exact expected origin and path with `domcontentloaded` before reading its title, heading, or body.
