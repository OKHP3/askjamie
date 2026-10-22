---
name: Keyboard tab test starting points
description: Make browser keyboard-reachability checks deterministic when prior tests changed focus.
---

When a browser test verifies that a control can be reached with Tab, start from a known, visible focusable element immediately before it in the real tab order. Do not count Tab presses from whatever element happens to remain focused after navigation, dialog dismissal, or an earlier keyboard test.

**Why:** prior page interactions can leave focus far from the target, making Tab counts vary with unrelated content and weakening the reachability assertion.

**How to apply:** for shared-header controls, identify the closest visible, enabled, non-inert focusable sibling that precedes the target, focus it, press Tab, and assert the target actually received focus.
