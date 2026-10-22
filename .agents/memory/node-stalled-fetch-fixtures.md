---
name: Node stalled-fetch fixtures
description: Keep simulated stalled-fetch tests alive until AbortSignal timeout handling executes.
---

When a Node test simulates a stalled fetch with a promise that never settles, keep a referenced event-loop handle active until the abort signal fires. `AbortSignal.timeout()` alone may not keep Node alive; without active I/O or another referenced handle, Node can exit successfully while the awaited request is still pending.

**Why:** A stalled-fetch regression initially exited with status 0 before its second timeout could fire, leaving the Lighthouse run incomplete and its summary unwritten.

**How to apply:** In fake fetch implementations that wait for `options.signal`, add a referenced timer or equivalent active handle and clear it in the abort handler. Assert that every expected fetch both starts and aborts.
