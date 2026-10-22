---
name: Forced-colors alpha contrast
description: Correctly evaluate Chromium-emulated forced-colors outlines when computed system colors include alpha.
---

When measuring focus indicators in browser-emulated forced-colors mode, do not treat an `rgba()` system color as unmeasurable solely because it has transparency. Composite the computed foreground over the actual opaque Canvas color first, then calculate contrast from the rendered color. Keep the original computed color in diagnostic evidence.

**Why:** Chromium can report a system `Highlight` focus outline with partial alpha even though the indicator is visibly rendered. Rejecting all translucent foregrounds as unknown creates a false failure; ignoring alpha can misstate the rendered contrast.

**How to apply:** Use alpha compositing for focus-ring contrast in forced-colors browser checks. Keep stricter opacity handling for SVG paint checks where transparency itself is part of the tested behavior.
