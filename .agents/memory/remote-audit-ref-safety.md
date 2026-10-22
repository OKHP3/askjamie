---
name: Remote audit ref safety
description: Preserve local Git refs while collecting current remote branch-head evidence.
---

Read-only repository audits must not use `git fetch` to check whether remote
branches have advanced. In a local-remote fixture, even `git fetch --all
--dry-run` moved `refs/remotes/origin/main`.

**Why:** Updating remote-tracking refs violates the audit's read-only contract
and makes an audit alter the evidence it is meant to inspect.

When the selected base maps to an advertised remote branch, compare its local
OID with the advertised OID. A mismatch is stale evidence and must prevent a
clean audit result. When the comparison cannot be made, report it as
unavailable rather than current; preserve valid local-branch and SHA-based
comparisons without claiming they are current remote evidence.

**Why:** Cleanup decisions must not treat an out-of-date tracking ref as the
current remote head, while local-only and commit-SHA audits remain useful.

**How to apply:** Query current branch heads with `git ls-remote --heads`,
compare only the selected matching ref, and leave every local ref unchanged.
