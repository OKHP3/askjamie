---
name: Retirement retention review scope
description: Why retention audits follow an owner-selected mainline rather than every side-branch state.
---

Audit retention along the owner's selected mainline; treat merged tree states
as the point where side-branch changes enter that retained history.

**Why:** A side branch can legitimately originate before the ledger exists.
Comparing all of its intermediate trees against a newer mainline baseline
would falsely label absent decisions as removals. A trusted baseline defines
the extent of evidence; changing it to hide a failure defeats that purpose.

**How to apply:** Keep the scope explicit in reports and instructions. If a
future change audits side-branch history, define its own comparable baseline
instead of treating unrelated ancestry as sequential ledger states.

## Performance changes must preserve the reporting contract

Retention-history optimizations must preserve the complete JSON result,
including a separate hold for every affected mainline commit. A repeated
invalid state is not redundant reporting, and restoration does not cancel
earlier holds.

**Why:** The owner requested faster long-history reviews without changing
their evidence or read-only behavior. Comparing only interval endpoints, or
skipping all repeated states, would hide temporary removals and rewrites or
change the recorded extent of a violation.

**How to apply:** Reuse immutable content reads, not commit-specific approval
decisions. Compare cached and uncached results for removal/restoration,
rewrite/restoration, invalid states, additions, and exact approved migrations.

## Measure churn, not only stable ledgers

Memory measurements for retirement review must include many distinct rewritten
decision blobs and cold restorations; entry-limited reuse is not a bound on
the retained ledger or the complete evidence report.

**Why:** Stable histories share almost all parsed data and conceal lifetime
growth from rewritten blobs. Evicting immutable reusable data is safe, but
evicting retention anchors or per-commit findings would change the audit.

**How to apply:** Compare peak memory and reread/revalidation counts together,
and label Python-heap measurements separately from process RSS. Preserve
full-output equivalence and read-only checks when changing cache limits.

## Include ledgers wider than reuse limits

Evaluate stable working sets larger than every reuse-cache limit, not just
histories with many distinct blobs over time.

**Why:** A sequential scan of a ledger wider than an LRU can evict its own
entries before the next scan. Low-churn histories can therefore amplify reads,
validation work, and parsed-object memory despite apparently effective limits.

**How to apply:** Measure amplification against unique blobs/records while
unrelated root trees change beyond snapshot-cache capacity. Keep a replaceable
current-ledger working set separate from bounded historical reuse, and preserve
per-tree path/mode checks and every commit-specific finding.