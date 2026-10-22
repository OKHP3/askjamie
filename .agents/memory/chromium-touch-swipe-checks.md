---
name: Chromium touch swipe checks
description: Reliable Chromium CDP touch gestures for testing horizontal scrolling.
---

When dispatching touch input with Chromium's CDP, ensure the scroll container is inside the viewport and pause briefly between touch moves. A burst of touch events sent immediately can be accepted without triggering native overflow scrolling.

**Why:** In headless Playwright runs, an instant touch sequence left `scrollLeft` unchanged; bringing the map into view and spacing movement events by about 30 ms triggered the expected scroll.

**How to apply:** For touch-scroll browser checks, enable touch support on the context, scroll the target container into view, dispatch a start/move/end sequence with short intervals, then assert the container's scroll position changed.
