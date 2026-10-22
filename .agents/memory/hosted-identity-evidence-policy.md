---
name: Hosted identity evidence policy
description: Why absent branch names and absent history association metadata have different acceptance rules.
---

For a branch-specific GitHub endpoint, an absent response name may rely on the
requested endpoint for association. Any supplied name must corroborate that
association exactly; a malformed or conflicting name must not be ignored.
Do not generalize this allowance to filtered deployment or PR histories.

**Why:** The protection hardening intentionally preserves minimal branch-response
compatibility while rejecting explicit contradictory identity evidence. A history
query filter, unlike a branch-specific resource path, is not sufficient proof of
each returned record's association.

**How to apply:** Keep endpoint provenance and record-level association separate
when extending hosted checks. Preserve independent unknown-evidence holds rather
than letting successful evidence from another lookup validate a bad response.
