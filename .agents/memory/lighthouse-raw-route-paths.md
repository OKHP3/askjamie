---
name: Lighthouse raw route paths
description: Preserve redirects and unusual URL path spellings during Lighthouse navigation review.
---

Compare the original URL path text when reviewing requested and final Lighthouse URLs. The WHATWG `URL.pathname` parser normalizes path syntax such as encoded dot segments, which can make distinct requested and final paths appear identical.

**Why:** Navigation review should accept only the explicitly approved trailing-slash difference; normalization must not hide a route change from owner review.

**How to apply:** Keep URL parsing for origin, query, and fragment handling, but use the original URL text for path comparison and retain browser-free cases for encoded path variants.
