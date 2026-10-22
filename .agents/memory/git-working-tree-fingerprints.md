---
name: Git working-tree fingerprints
description: Fingerprint changed tracked and untracked files reliably across Git snapshots.
---

`git diff --raw` identifies changed worktree paths but can report an all-zero
worktree object ID, so it cannot distinguish two different contents for an
already-dirty file. Use binary diff output for staged and unstaged tracked
changes, and include untracked file contents when they are part of the snapshot.

**Why:** A repeated Lighthouse-run test changed a tracked file while the checkout
was dirty both before and after; raw diff output produced identical fingerprints.

**How to apply:** When comparing working-tree snapshots, hash staged and
unstaged binary diff output plus untracked path/content pairs. Exclude generated
run artifacts consistently at both snapshot boundaries.
