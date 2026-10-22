---
name: Local branch ref disambiguation
description: Avoid Git short-name collisions between local branches and tags in repository audits.
---

When an audit already knows it is resolving a local branch, use its full
`refs/heads/<name>` ref for commit and log lookups. For branch enumeration under
`refs/heads/`, prefer `%(refname:strip=2)` over `%(refname:short)`: the latter
can return a disambiguated value such as `heads/archive` when a tag shares the
branch name.

**Why:** A same-named tag can be selected by abbreviated resolution and can
also change Git's abbreviated branch label, causing an audit to compare the
wrong tip or omit the branch.

**How to apply:** Use explicit local-head refs in branch-specific audits and
tests; leave user-selected refs alone unless their intended namespace is clear.
