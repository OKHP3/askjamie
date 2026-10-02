# askjamie.bot: Flawless GitHub Pages Plan

October 1, 2026 · Jamie Hill

## Executive summary

AskJamie is already over-engineered on process and under-delivered on the visitor's first five seconds. The plan flips that: fix what a visitor and a reviewer actually see, then make the repo itself the teaching artifact.

- **The pipeline is genuinely good.** Allowlisted artifact, validate-then-deploy, CSP hashes, fingerprinting, 200+ responsive checks. Most Pages sites never get here.
- **The homepage is slow on mobile.** Lighthouse mobile scored 69 on performance, LCP 5.9 s. The LCP element is the GPT transition dialog, not the hero.
- **The deploy silently drops files.** `/.well-known/security.txt` exists in source and returns 404 live. Something between the allowlist and Pages strips dotfolders.
- **Dead weight ships.** `_headers` is published but GitHub Pages ignores it. A 7 MB 4096px PNG is publicly served. A 756 KB avatar PNG renders at thumbnail size.
- **The repo is noisy for a showcase.** 638 of 1,305 tracked files are `.agents/` tooling. The Windows clone shows 675 modified files that are pure CRLF churn.
- **GitHub Pages has a hard ceiling.** No custom headers, no HSTS on custom domains, fixed 10-minute cache. Flawless means owning that ceiling explicitly, not pretending `_headers` works.
- **Recommendation:** stay pure GitHub Pages, cut the noise, hit measurable green on every route, and publish a "How this site is built" page that turns the constraints into the lesson.

## Current state baseline

Measured October 1, 2026 against the live site and the local Windows clone at `0b06d7d`. Lighthouse figures are one mobile-emulated sample from a cloud runner, so treat them as directional until the 5-run median in Phase 1.

| Area | Finding | Evidence | Severity |
| --- | --- | --- | --- |
| Performance | Home: perf 69, LCP 5.9 s, TBT 340 ms, 1,336 KiB | Lighthouse 12 mobile, `/` | High |
| Performance | LCP element is `#transition-dialog-summary` (the GPT transition modal) | Lighthouse LCP element audit | High |
| Performance | `askjamie-avatar-tall-left-square-1024.png` is 739 KiB, ~99% wasted at rendered size | uses-responsive-images | High |
| Performance | GTM blocks the main thread ~594 ms | third-party-summary | Medium |
| Performance | `/universe/` CLS 0.273 (two shifts, Mermaid render) | Lighthouse 12 mobile | Medium |
| Accessibility | `.eyebrow` text `#3c8ea1` on `#fdfbf7` = 3.64:1, fails AA | color-contrast audit | Medium |
| Deploy integrity | `/.well-known/security.txt` and `/.well-known/discord` return 404; both exist in source and in the allowlist | curl, `prepare-pages-artifact.py` | High |
| Deploy hygiene | `_headers` (5 KB) is publicly served and has no effect on Pages | curl 200 | Low |
| Deploy hygiene | `askjamie-background-muted-wide-4096.png` (7 MB) is publicly served | curl 200 | Low |
| Platform | No HSTS, no `frame-ancestors`, `cache-control: max-age=600` on everything | response headers | Platform ceiling |
| Repo | 675 files show modified on Windows; zero diff ignoring CR, so line-ending churn | `git diff --ignore-cr-at-eol` | Medium |
| Repo | `.gitattributes` has no global `* text=auto eol=lf` rule | file inspection | Medium |
| Repo | 638 of 1,305 tracked files are `.agents/`; 174 are `assets/docs/` dated audits | `git ls-files` | Medium |
| Already strong | Best practices 100, SEO 100, www and http redirect to apex, canonical, JSON-LD, meta CSP with hashes, allowlisted artifact | Lighthouse, curl | Keep |

The pattern: the release machinery is mature, and the gaps live in what ships and what a visitor hits first.

## Definition of flawless

Flawless is a gate CI enforces, not an adjective. Every row below becomes a check that fails the build.

| Dimension | Bar | How it's proven |
| --- | --- | --- |
| Performance | Lighthouse mobile perf 95+ on every sitemap route; LCP under 2.5 s, CLS under 0.1, TBT under 200 ms | Lighthouse CI, 5-run median, budgets in repo |
| Accessibility | Lighthouse a11y 100, zero axe-core violations at WCAG 2.2 AA, one recorded human NVDA and VoiceOver pass | axe in Playwright suite, dated human session note |
| SEO and discovery | SEO 100, sitemap equals search index equals allowlist, valid JSON-LD on every route | existing validators plus Rich Results spot check |
| Deploy integrity | Every allowlisted file returns 200 live; nothing outside the allowlist returns 200 | post-deploy probe diffing manifest vs live |
| Payload discipline | No served image over 200 KB; every served file referenced by at least one page | orphan and size check in `audit-site.py` |
| Security posture | Meta CSP with no `unsafe-inline`; `security.txt` live and unexpired; Actions pinned by SHA; artifact attestation | CSP test, post-deploy probe, `actions/attest-build-provenance` |
| Repo legibility | Fresh clone on Windows and Mac shows zero diffs; public tree is site plus its tooling, nothing else | CI matrix clone check, `git ls-files` budget |
| Honesty | Platform ceiling documented on a public page; no config file implies a control that isn't enforced | content review, `_headers` removed or excluded |

## Options

Four credible paths. They differ mainly on whether you keep the "pure GitHub Pages" claim.

| Option | What it is | Gains | Costs | Exemplar value |
| --- | --- | --- | --- | --- |
| A. Pure Pages, polished | Fix perf, a11y, deploy integrity within the current stack | Keeps the claim intact; smallest change set | Header ceiling stays (no HSTS, no frame-ancestors, 10-min cache) | High, if the ceiling is documented |
| B. Pages behind Cloudflare | Proxy the apex through Cloudflare for headers, HSTS, long cache | Real security headers, `_headers`-style control, edge caching | Second vendor, DNS change, no longer "just Pages" | Medium: shows a pattern, dilutes the claim |
| C. Add a static generator | Move to Eleventy or Astro for partials and an image pipeline | Kills page-shell duplication, native responsive images | Migration risk, new runtime, contradicts AGENTS.md non-goals | Low for this goal |
| D. Edit first, then A | Cut repo and deploy noise, normalize line endings, then execute A | Cleanest public tree; every later fix lands on a quiet baseline | One upfront hygiene sprint before visible wins | Highest |

## Recommendation

Go with D. Edit first, then polish inside pure GitHub Pages.

The point of an exemplar is the claim. "Look what plain GitHub Pages can do" stops being true the moment Cloudflare sits in front of it, and a generator migration solves a duplication problem you can already handle with your existing Python tooling.

The ceiling becomes content, not a defect. A public `/how-this-site-is-built/` page that says exactly which controls are enforced (meta CSP, allowlisted artifact, attestation) and which the platform can't do (HSTS, frame-ancestors, cache TTL) is more credible than any header score.

Keep Option B in the drawer. If a real security requirement ever demands HSTS or frame-ancestors, that's the escape hatch, documented in an ADR now so it's a decision, not a scramble.

## Roadmap

Six phases, each closed by a gate that becomes a permanent CI check. Each phase is one or a few small PRs, matching the repo's own "small, reviewable steps" rule.

| Phase | Scope | Key deliverables | Gate to exit |
| --- | --- | --- | --- |
| 0. Quiet baseline | Repo hygiene | Add `* text=auto eol=lf` to `.gitattributes` and renormalize; decide `.agents/` fate (move to a tooling repo or a submodule, or keep with a `.agents/README` justification); prune dated `assets/docs/` audits into one `docs/history/` index | Fresh Windows and Mac clone show 0 diffs; tracked-file budget agreed and checked |
| 1. Measure honestly | Instrumentation | Lighthouse CI on all sitemap routes, 5-run median, `budgets.json`; axe-core added to the Playwright suite; post-deploy probe that diffs the artifact manifest against live 200s | Baseline numbers committed; probe runs on every deploy |
| 2. Ship what's promised | Deploy integrity | Fix the dotfolder drop (likely `upload-pages-artifact` excluding hidden files; confirm by downloading the deployed artifact); stop shipping `_headers` and orphan images; add `Expires` to `security.txt` | Probe green: every allowlisted file 200, nothing else 200 |
| 3. First five seconds | Perf and a11y | Defer the transition dialog until after LCP (or make it an inline banner); AVIF/WebP plus `srcset` for the avatar and hero art via a CI image script; load GA4 after interaction or idle; reserve Mermaid height on `/universe/`; fix `.eyebrow` contrast with a darker teal from the brand set | Perf 95+, a11y 100 on every route at median; axe 0 |
| 4. Prove it | Security and provenance | Pin all Actions by SHA; add `actions/attest-build-provenance` on the Pages artifact; drop any remaining `unsafe-inline`; ADR for the Pages header ceiling and the Cloudflare escape hatch | Attestation visible on releases; ADR merged |
| 5. Tell the story | Exemplar content | Public `/how-this-site-is-built/` page: pipeline diagram, enforced vs not-enforced controls, live scorecard badges; README "Use this as a template" section | Page live, linked from footer and README |

Phases 0 and 1 come first on purpose. Fixing perf before you can measure it reliably is how the old dated audits piled up.

## Risks and mitigations

| Risk | Likelihood | Mitigation |
| --- | --- | --- |
| Renormalizing line endings collides with in-flight work in other clones or Replit | Medium | Do it as a standalone PR on a quiet day; push from one machine; re-clone elsewhere instead of merging |
| Moving `.agents/` breaks agent workflows that expect it in-tree | Medium | Submodule or sparse path keeps the location; update AGENTS.md in the same PR |
| Theme or `app.js` changes ripple to sister sites | High | Follow `sister-site-sync.md`; land AskJamie-only overrides first, sync as a separate decision |
| Deferring GA4 loses some pageview data | Low | Load on idle, not on interaction; compare a week of counts before and after |
| Lighthouse variance makes gates flaky | Medium | Median of 5, budgets with 5-point tolerance, fail only on the median |
| Transition dialog change weakens the GPT retirement message | Low | Keep the persistent inline notice; dialog stays reachable on demand |
| Scope creep back into dated audit documents | High | One scorecard, regenerated by CI; history goes to an index, not new files |

## Next actions

- [ ] Decide the `.agents/` question: separate tooling repo, submodule, or stays with a written reason
- [ ] Phase 0 PR: `.gitattributes` global LF rule plus `git add --renormalize .`
- [ ] Download the last deployed Pages artifact and confirm whether `.well-known/` is in it
- [ ] Phase 1 PR: Lighthouse CI with budgets and axe in Playwright, both reporting only
- [ ] Phase 3 spike: transition dialog after LCP plus avatar to AVIF/WebP, measure the delta

Assumptions: "flawless" means measurable quality plus repo legibility, not a redesign; brand contract and external Google Fonts stay as AGENTS.md defines them.

Suggested follow-ups:

1. Have me run Phase 0 locally and hand you the renormalize PR.
2. Run the 5-run Lighthouse median across all 27 routes to replace the single-sample baseline.
3. Draft the `/how-this-site-is-built/` page outline and the Pages header-ceiling ADR.
4. Inventory which `.agents/skills` this repo actually uses, so the move decision is evidence-based.
