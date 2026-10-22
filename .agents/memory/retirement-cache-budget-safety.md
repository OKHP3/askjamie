---
name: Retirement cache budget safety
description: The safety boundary for future byte-weighted retirement-history caches.
---

Treat byte budgets as limits on optional historical reuse, not as record-size
validation rules or limits on the evidence retained by an audit.

**Why:** The user requires unusually large valid historical records to remain
accepted without dropping retained anchors, holds, or migration evidence.
Large-record probing supports byte-weighted reuse, but does not establish a
safe universal record-size limit or total-process memory cap.

**How to apply:** When changing cache policy, preserve complete results and
active-ledger reuse even above the reuse budget. Include snapshot references
when considering retained object memory; a blob-cache-only budget can leave
large parsed objects alive elsewhere.

Measure the accounting metadata as well as the reusable payload.

**Why:** Wide ledgers with many small shared objects can spend more memory on
identity/reference bookkeeping than on evidence. Retaining a flattened list of
all reachable identities per snapshot amplifies that cost even when payloads
are shared correctly.

**How to apply:** Prefer walking immutable payload edges over retaining another
edge graph, and avoid duplicate transitive identity lists. Recomputing cheap
object sizes and immediate edges is an intentional CPU-for-memory tradeoff;
it depends on cached payloads remaining immutable. Rerun both wide-ledger and
large-record probes when changing accounting; label owned bookkeeping storage
and payload charges separately from traced heap and RSS.

Keep failed-scan prefix reuse separate from successful-ledger promotion.

**Why:** A newly introduced valid prefix can exceed historical reuse limits
before a late error. Discarding the entire failed scan makes unrelated root
changes reread and revalidate that prefix without adding useful evidence.

**How to apply:** Retain only a replaceable failed-scan working set, never a
growing union or a successful snapshot. Recheck tree-dependent constraints,
never mark failing records validated, and preserve a hold for every commit.

Keep wide-ledger uncached controls genuinely uncached, but size their replay
separately from optimized scans.

**Why:** Uncached record validation launches Git ref-format checks as well as
reading blobs. Batch blob transport alone does not make long, wide replays
cheap. Once oversized snapshots bypass historical reuse entirely, exceeding
the snapshot entry count adds runtime without strengthening that byte-budget
regression.

**How to apply:** Preserve real record validation in the reference. Use several
distinct unrelated roots to detect cyclic rereads, retain production working-set
width, and scale the optional byte budget rather than requiring huge fixtures
or imposing an absolute RSS threshold.
