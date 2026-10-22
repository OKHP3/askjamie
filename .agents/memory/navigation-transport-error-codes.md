---
name: Navigation transport error codes
description: Chromium error text for local navigation failures caused by an interrupted connection.
---

Chromium may report a server-side socket close during navigation as `net::ERR_EMPTY_RESPONSE` rather than `net::ERR_CONNECTION_RESET`. Treat this and related Chromium network error codes as transport failures, distinct from Playwright navigation timeouts.

**Why:** The browser QA connection-reset fixture closed the socket without an HTTP response, and Chromium surfaced `ERR_EMPTY_RESPONSE`.

**How to apply:** When classifying navigation errors, cover the underlying family of connection/network error codes rather than relying on only the literal reset code; keep Playwright timeout exceptions in their own category.
