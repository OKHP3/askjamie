# Universe diagram failure announcement check

**Status:** Deferred — human screen-reader acceptance check remains required  
**Updated:** 2026-10-13

## Verified automated behavior

- Latest rerun: 2026-10-13 19:22:43–19:22:56 UTC.
- Browser pair actually used in this checkout: Playwright 1.60.0 with Chromium
  152.0.7977.64.
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

The fetched `origin/main` is `d4c0dcb` and declares Playwright 1.64.0. This
checkout still declares/locks Playwright 1.63.0 and has Playwright 1.60.0 and
Chromium 152.0.7977.64 installed. The Playwright 1.64.0 / Chromium 156.0.8078.4
pair has **not** been run here; no dependencies were installed or changed.

## Required human check — not performed

No actual screen-reader speech test has been performed or supplied. The reader,
browser, and spoken result are **not tested**; polite speech announcement is
**unverified**. This is not a pass or a waiver of the acceptance requirement.

## Short human procedure

1. On a Windows machine with NVDA and Google Chrome, start the existing `Start
   application` workflow and open `/universe/`. Record the NVDA, Chrome, and
   Windows versions.
2. In DevTools → Network request blocking, block `*mermaid.esm.min.mjs*`, then
   reload the page to force the Mermaid module load failure.
3. Listen for the exact unavailable-diagram message below. Confirm focus is not
   redirected to the status, then use Tab to reach the three hero map links.
4. Record what was spoken, whether it was announced politely, and the observed
   focus and link behavior.

Expected message: “The diagram could not be displayed. Use the links below to
browse these pages.”
