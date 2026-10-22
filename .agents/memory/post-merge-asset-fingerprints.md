---
name: Post-merge asset fingerprints
description: Keep generated shared-asset URLs and CSP hashes synchronized after frontend asset changes.
---

After a merge changes a fingerprinted shared asset, post-merge setup should regenerate the tracked asset references and the CSP hashes before auditing, then run the freshness check as verification. **Why:** A check-only hook failed after valid shared JavaScript updates because generated page URLs still referenced the previous asset hash. **How to apply:** Keep generation and verification separate: the post-merge hook may refresh generated tracked files, while CI can remain read-only and reject stale artifacts.
