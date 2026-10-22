---
name: Deferred theme measurement sequencing
description: Keep independent geometry evidence tied to the same deferred stylesheet transition.
---

Capture all requested component baselines before releasing the shared deferred-theme request gate. Release the stylesheet once, then capture each component's after-state and report its evidence independently. A component-specific measurement must not release the shared gate on its own.

**Why:** Releasing the gate in the first component's check leaves later checks without a genuine pre-activation state. Also, an expanded-menu attribute alone does not mean the menu's CSS transition has settled; wait for active animations to finish before recording geometry.

**How to apply:** When adding another measurement around the same deferred stylesheet transition, stage it as baseline capture → one theme activation → independent after-state capture. Include route, viewport, before/after geometry, and explicit unavailable evidence.
