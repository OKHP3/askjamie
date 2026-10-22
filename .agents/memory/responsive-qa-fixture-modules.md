---
name: Responsive QA fixture modules
description: Keep isolated responsive-QA fixtures runnable after local helper modules are added to the runner.
---

Any test fixture that copies `responsive-qa.mjs` into a temporary `scripts/` directory must also copy every directly imported local module next to it. If the fixture initializes Git to test source provenance, stage those helper modules in its baseline commit as well.

**Why:** The runner's relative imports resolve from the copied script's directory. Missing a newly added helper prevents Node from loading the runner, so tests fail before reaching the behavior they intend to exercise.

**How to apply:** When adding or changing a relative import in the runner, search tests for fixture copies of `responsive-qa.mjs` and update each relevant fixture's copied files and any Git baseline file list.
