# Universe diagram failure announcement check

**Status:** Owner waived the human merge hold on 2026-10-14. Human
screen-reader testing remains incomplete and spoken output unverified.
**Updated:** 2026-10-14

## Historical Replit automated evidence

- Replit task #230 rerun: 2026-10-13 19:22:43 to 19:22:56 UTC.
- Browser pair used in that isolated task checkout: Playwright 1.60.0 with Chromium
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

The isolated Replit task checkout fetched `origin/main` at `d4c0dcb`, which
declares Playwright 1.64.0. That task checkout still declares and locks
Playwright 1.63.0 and has Playwright 1.60.0 and Chromium 152.0.7977.64 installed.
Its dated rerun does not establish behavior with Playwright 1.64.0 and Chromium
156.0.8078.4. The separate Replit project checkout has the newer installed pair.

The integration candidate is based on the reviewed current main. Its required
CI checks use the current lockfile and matching Chromium. Those results must
be read for the exact PR head before claiming candidate compatibility. Browser
semantics and keyboard checks still do not establish actual spoken output.

## Current Mac consolidation evidence

The candidate now includes checked main
`292bc34f1d8971d6841d40635c9e7de655cce777`, preserving the original task history
through ordinary merges. The only content conflict was the module fingerprint;
the cache-bust generator resolved it from the combined initializer. Mermaid
12.2.0 configuration, Mac shortcut/BFS fixes and the verified release-input
wording remain present.

Local checks on the combined code use Playwright 1.64.0 and Chromium
156.0.8078.4. JavaScript smoke (including forced module/render failure, focus
retention and the three map links), all six Universe diagrams at two widths
and both themes, and all 270 responsive rows pass. Structure, fingerprints,
CSP and generated freshness pass. Exact-head CI and the pinned preview revision
are recorded in PR #90. These checks still do not establish actual speech.

## Required human check. Not performed

No actual screen-reader speech test has been performed or supplied. The reader,
browser, and spoken result are **not tested**; polite speech announcement is
**unverified**. Automated evidence is not a human test pass.

Jamie explicitly authorized merging PR #90 on 2026-10-14 with this instruction:
“Explicitly waive the merge hold; keep human testing recorded as incomplete.”
This waives the merge prerequisite only. It does not establish spoken output
or complete Replit task #230. The human procedure below remains follow-up work.

## Short human procedure

1. Use the exact PR #90 candidate, its pinned Mac preview, or the merged
   release after verifying its source revision. Avoid an older task server.
   On Mac, use VoiceOver with the chosen browser and record both versions plus
   macOS. On Windows, use NVDA and Chrome and record their versions plus
   Windows. A preview does not imply a tested spoken result.
2. In DevTools → Network request blocking, block `*mermaid.esm.min.mjs*`, then
   reload the page to force the Mermaid module load failure.
3. Listen for the exact unavailable-diagram message below. Confirm focus is not
   redirected to the status, then use Tab to reach the three hero map links.
4. Record what was spoken, whether it was announced politely, and the observed
   focus and link behavior.

Expected message: “The diagram could not be displayed. Use the links below to
browse these pages.”
