---
name: Rendered text visibility
description: Use browser visibility checks before counting DOM text ranges as visible copy.
---

When measuring visible page text, do not infer visibility from a nonempty `Range.getClientRects()` result or computed display styles alone. Chromium can return text rectangles for descendants inside a closed `<details>` even when those descendants are not painted. Use `Element.checkVisibility()` before clipping, overflow, and overlap analysis, and exclude intentionally screen-reader-only text and unfocused skip links.

**Why:** A fallback-font audit treated collapsed page-map content and accessibility-only labels as visible, creating hundreds of false overlap and clipping findings.

**How to apply:** Filter out non-rendered and visually hidden text before collecting its rectangles. Keep accessibility-only copy out of visual-readability assertions unless the test explicitly focuses or reveals that control.
