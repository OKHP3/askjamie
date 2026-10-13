# Universe diagram failure announcement check

**Status:** Deferred — human screen-reader acceptance check remains required  
**Recorded:** 2026-10-12

## Verified automated behavior

- Browser: Chromium 152.0.7977.64, driven by the project's Playwright smoke test.
- Before failure, the empty status is present in Chromium's accessibility tree as
  a `status` node with `live=polite`, `atomic=true`, and relevant changes set to
  `additions text`.
- After a forced Mermaid render failure, the status contains:
  “The diagram could not be displayed. Use the links below to browse these
  pages.”
- Automated keyboard checks confirm focus remains on the focused navigation
  link and all three map links remain reachable in Tab order.
- `node tests/test_js_smoke.spec.mjs` passes.
- `python3 scripts/audit-site.py --quiet`, `python3 scripts/cache-bust.py
  --check`, and `python3 scripts/generate-csp.py --check` pass.

These checks establish browser semantics and keyboard behavior only. They do
not establish that a screen reader spoke the message.

## Required human check — not performed

No actual screen-reader speech test has been performed or supplied. The reader,
browser, and spoken result are **not tested**; polite speech announcement is
**unverified**. This is not a pass or a waiver of the acceptance requirement.

To close this check, reproduce a Mermaid load or render failure on the Universe
page with a supported screen-reader/browser pairing, then record:

1. Screen reader name and version, and browser name and version.
2. Whether the unavailable-diagram message was announced politely.
3. Whether focus stayed where it was and the ordinary navigation links remained
   reachable.
4. The observed result, including any failure or uncertainty.
