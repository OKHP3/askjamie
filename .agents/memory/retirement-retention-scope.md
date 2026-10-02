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