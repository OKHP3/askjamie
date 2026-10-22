---
name: Hosted history memory safety
description: Why hosted-history resource limits may discard evidence but retirement retention must not.
---

Hosted-history resource limits may discard an incomplete or oversized history
only when its evidence becomes explicitly unknown and continues to block deletion.
Do not generalize that policy to retirement-ledger retention.

**Why:** The user explicitly allowed hosted evidence to be discarded under a
practical aggregate memory budget while preserving unknown-evidence deletion
holds, and required this work to stay separate from retirement-ledger memory
checks. Retirement evidence has a different completeness requirement.

**How to apply:** Keep hosted and retirement memory policies independent.
Continue later hosted checks after a resource hold, and never turn missing
history into an empty, complete history.

Measure transient parsing and streaming-hash allocations independently from
retained-history accounting; build wire fixtures before starting the phase
trace, and do not count an already parsed tree as a new hashing allocation.

**Why:** Small nested JSON containers can expand far beyond the wire-byte limit
before retention accounting can reject them. String-heavy fixtures alone do
not establish a practical bound for other response shapes.

**How to apply:** Use shape-specific bounds rather than interpreting the
retained-history allowance as a whole-process memory cap. Resource exceptions
must follow the same unknown-evidence policy as a retention-budget rejection.
