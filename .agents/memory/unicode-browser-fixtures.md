---
name: Unicode browser fixtures
description: Controlled browser HTML fixtures must declare their intended character encoding when they contain non-ASCII text.
---

Set `charset=utf-8` on controlled HTML responses when fixture titles or visible text include Unicode punctuation or other non-ASCII characters.

**Why:** Without an explicit charset, Chromium may decode fixture bytes using a legacy encoding, changing the text before assertions or application logic reads it.

**How to apply:** Add the charset to the fixture response's content type whenever expected browser text contains Unicode; otherwise use ASCII-only fixture text.
