---
name: Archive merge fixture boundaries
description: How to test multiple archive-only merges when shared feature commits are already integrated on the active line.
---

Build multi-merge archive-review fixtures so each merge has an independently
observable outcome: a clean merge contributes no resolution evidence, while a
conflicted merge contributes only the resolution content still different at
the archive tip. Shared non-merge commits may already be in the active line;
`git cherry` does not report them as archive-only patches, and merge commits
need their own remerge-diff review.

When a conflict resolution moves a file, remove the unmerged source path and
add the resolved destination path before staging. `git mv` cannot operate on
the source while its conflict stages remain in the index. Assert both explicit
name-status paths, but follow Git's output ordering rather than assuming the
delete precedes the add.

**Why:** Git rejects `git mv` while the source still has unmerged index stages,
and name-status record order depends on the paths.

**How to apply:** In conflicted rename fixtures, resolve by removing the source
path and adding the destination, then assert both records without assuming order.

**Why:** A single-merge fixture cannot show whether a clean earlier merge
obscures a later merge with unique resolution content.

**How to apply:** Give the archive and active line equivalent feature history,
create separate merge commits, and assert each merge's remerge diff and final
archive-side difference separately. Count only the merge whose resolution
path still differs in the archive tip.
