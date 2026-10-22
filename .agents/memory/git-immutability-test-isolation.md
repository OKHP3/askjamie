---
name: Git immutability test isolation
description: Distinguish automatic Git maintenance races from changes made by read-only audits.
---

When a test compares every file in a temporary Git repository before and after
a read-only audit, isolate automatic Git garbage collection and maintenance
with process-scoped Git configuration before creating commits.

**Why:** A retirement-history regression run failed with temporary pack and
bitmap files changing between snapshots. The complete suite passed when
`gc.auto=0` and `maintenance.auto=false` were supplied through process-scoped
Git configuration. These file changes can reflect background maintenance,
not writes by the audit.

**How to apply:** If an immutability assertion fails on Git object-pack or
maintenance files, inspect the differences before changing the audit.
Use process-scoped Git configuration for fixture isolation: preserve inherited
configuration entries, append the maintenance overrides, and restore the
environment at test cleanup. This covers fixture commands, spawned CLI audits,
and in-process audits without persisting settings in repository or global
configuration. Do not weaken the immutability assertion; inject checkout,
Git metadata and saved recovery-snapshot writes as negative controls.

When constructing mode-only Git history fixtures with a deliberately dirty
checkout, preserve the staged object ID explicitly rather than using
`git update-index --chmod` with a worktree path.

**Why:** That form of `update-index` can stage checkout contents as well as
changing the mode. After `read-tree`, the checkout may still contain a different
decision version, silently adding another immutable record to the fixture.

**How to apply:** Read the index object ID first and use `--cacheinfo` with
the intended mode, unchanged object ID and exact path. Keep checkout contents
independent of the history being measured.
