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