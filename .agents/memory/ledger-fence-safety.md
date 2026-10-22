---
name: Ledger fence safety
description: Why cleanup ledgers reject unclosed fenced examples rather than treating EOF as a valid closing fence.
---

Cleanup ledgers must reject unclosed fenced examples, even though general Markdown renderers allow a fence to end at EOF.

**Why:** An accidental opening fence could otherwise hide real retention holds later in the file while earlier archive decisions remain valid, incorrectly allowing cleanup.

**How to apply:** Preserve this fail-closed policy when changing Markdown handling. Completed examples are documentation, not cleanup instructions; a missing closer requires an explicit repair before cleanup can be approved.
