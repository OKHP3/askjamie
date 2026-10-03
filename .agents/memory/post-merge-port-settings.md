---
name: Post-merge port settings
description: Handle `.replit` port flags that appear after post-merge workflow reconciliation.
---

A successful post-merge setup may leave `.replit` modified. In one observed run, a clean pre-run tree gained `exposeLocalhost = true` under its configured port mapping after setup and workflow reconciliation. The hook script itself did not reference `.replit`.

**Why:** The diff appeared after the run, but its source was not independently established. Reverting it only to restore a clean tree could remove a platform-managed preview setting.

**How to apply:** Compare `git status` before and after `runPostMergeSetup()` and inspect any exact `.replit` diff. Preserve or investigate unexpected port flags; do not revert them automatically.