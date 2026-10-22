---
name: Font-face decode evidence
description: Browser signals for distinguishing successful font HTTP responses from usable decoded faces.
---

Do not treat an HTTP 2xx font response or `document.fonts.status === "loaded"` as proof that every face in a requested family decoded. A `document.fonts.load()` call for visible text can resolve with a usable sibling face while another registered face in that family is in `error`.

**Why:** A corrupt hosted font returned HTTP 200, the visible-text load resolved with a loaded face, and the overall FontFaceSet reported `loaded`, while a registered face in the same family reported `error`.

**How to apply:** Preserve the response status and browser face statuses separately. Include rejected loads and registered error faces in readiness evidence, and withhold geometry comparisons unless the required face evidence is usable.
