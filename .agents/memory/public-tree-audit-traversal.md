---
name: Public tree audit traversal
description: Static-site audits must prune workspace-managed directories before descending into them.
---

## Rule
Use a top-down filesystem walk that removes non-public workspace directories from the traversal list before recursion, and tolerate transient access errors.

**Why:** Replit can provision or remove auxiliary skill directories while a validation workflow is running; recursive globbing can raise before a later path filter gets a chance to exclude them.

**How to apply:** Share the same safe public-tree walker across HTML discovery and repository-wide checks so one remaining recursive glob cannot reintroduce audit crashes. Tests that filter `Path.parts` must also place temporary repository roots outside excluded directory names in their absolute ancestry; otherwise even a synthetic root-level page is filtered out.