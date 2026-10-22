---
name: Remote audit test isolation
description: Avoid collisions when integration tests create local bare Git remotes alongside temporary repositories.
---

When a fixture uses a `TemporaryDirectory` as its repository root, its parent
directory is shared by unrelated test runs. A bare remote created at a fixed
sibling path can collide with another run.

**Why:** A Git remote fixture that is not uniquely isolated can fail or
accidentally inspect another run's repository, making network-audit tests
flaky.

**How to apply:** Derive each sibling remote path from the unique temporary
repository directory name, or create a dedicated temporary directory for the
remote.
