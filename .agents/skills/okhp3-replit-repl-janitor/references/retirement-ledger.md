# Retained recovery-ref decisions

Load this reference only when an owner asks to retire a recovery ref.

## Approval and storage

Ask the owner to approve the ledger path and format below as well as each
exact retirement. A committed file is retained evidence, not proof that a
human approved its contents; verify approval through the project's normal
owner-review channel. Do not invent the approver or date.

Suggested layout (owner may choose another repository-relative location):

```text
governance/recovery-retirements/
  policy.json
  decisions/
    feature-example-2026-10-02.json
```

This is internal governance, not website content. Confirm both deployment
paths exclude it; if the repository or preview is public, use only public-safe
handles and evidence. Never store credentials, session details, signed URLs,
private correspondence, or personal contact information. Record a short
redacted evidence summary and a safe repository-relative evidence reference
when useful. Unknown fields, credential-bearing URLs, URLs with query strings,
and common credential patterns are rejected; human redaction is still required.

Policy format 1 (illustrative values only; not actual approval):

```json
{
  "format": 1,
  "records_directory": "governance/recovery-retirements/decisions",
  "approved_by": "owner-handle",
  "approved_on": "2026-10-02"
}
```

Decision format 1 (replace the example commit with the exact reviewed full ID):

```json
{
  "format": 1,
  "decision": "retire",
  "ref": "refs/recovery/feature-example",
  "protected_commit": "0123456789abcdef0123456789abcdef01234567",
  "evidence": "Protected commit is reachable from verified origin/main; owner reviewed the retained work.",
  "decision_date": "2026-10-02",
  "approver": "owner-handle"
}
```

The policy and records must be committed unchanged in `HEAD`. Only JSON
records may be placed in the approved decisions directory (the policy itself
may be there and is skipped). Every record is validated, including earlier
retirements whose refs are now absent. Use one unique record per exact ref;
do not reuse retired ref names, rewrite old decisions, or delete records as
cleanup. A changed storage location or format requires renewed owner approval.

Decision dates use valid `YYYY-MM-DD` calendar dates. New decisions cannot
predate the location/format approval; unchanged retained decisions can preserve
their original dates with the explicit migration proof below.
Object IDs must be full lowercase SHA-1 or SHA-256
commit IDs. During a selected retirement, the protected commit must match the
pre-removal snapshot and still resolve to a commit. Evidence must exactly
match the explicit approval allowance. The approver label and evidence are
retained verbatim; no environment variables or secret values are consulted.

## Procedure

### History audit memory and read tradeoff

History reviews reuse immutable objects only within one audit: at most 32
ledger snapshots, plus historical LRU caches of 128 parsed JSON blobs and
1,024 successful record-validation IDs. These three optional historical caches
share a **4 MiB payload-byte budget** in addition to their entry limits.
The two most recent distinct successful
ledger object sets also retain their blob and validation identities,
independently of those LRU limits. A different successful object set replaces
the older set; unrelated root edits with the same object identities do not
displace it. These sets do not accumulate all historical objects. Failed scans
leave both successful working sets intact and replace a separate single
failed-scan working set. This set retains parsed blobs and only successful
per-record validations from that scan; a failing record is never marked valid.
Each new root still checks paths, modes, policy, and ledger-wide constraints,
and still gets its own invalid-ledger hold. This prevents stable or alternating
ledgers larger than either LRU from cyclically evicting their own entries on
every unrelated tree change, including a wide valid prefix before a late error.
An evicted historical blob may be read and validated again; this never removes
retained decision anchors, commit-specific holds, or approved migration evidence.
These are reuse limits, not a fixed process-memory cap: large individual
records, the active working sets, retained ledger, commit list, and complete
JSON report still require memory.

The byte weight is the sum of Python `sys.getsizeof` values for cache keys and
their reachable built-in containers and scalar objects. It measures parsed
Unicode storage width rather than serialized JSON bytes or character counts.
Identity-based reference accounting charges each shared object only once across
blob, validation-ID, and snapshot caches; removing one reference does not
release its charge while another historical cache still references it.
Container overhead is included; accounting tables, reference counts, LRU
bookkeeping, allocator arenas, and external/native allocations are not.
The budget therefore bounds accounted reusable payload, not total heap or RSS.

A hit refreshes global historical recency as well as the cache's local LRU.
Admission evicts the least-recently-used historical entries until the union
fits; local entry limits still apply. An entry whose own reachable payload
exceeds 4 MiB bypasses historical caching without flushing useful smaller
entries. This includes full-ledger snapshots, so bypassing only the blob cache
cannot keep oversized parsed objects alive through snapshot references.
Oversized objects remain valid and available to the current scan and exempt
working sets. A cache miss rechecks the immutable tree; it never suppresses a
hold or removes retention/migration evidence.

**Budget rationale:** 4 MiB leaves room for the 128-entry, 16 KiB-evidence churn
fixture (about 2 MiB of evidence plus container/snapshot overhead), while
preventing the roughly 140 MiB optional retention seen with large records.
An initial 8 MiB allowance reduced large-record peak heap to 16.34 MiB, but
wide-stable peak heap increased from the earlier 8.82 MiB measurement to
23.88 MiB, partly due to accounting metadata. The smaller allowance limits
this additional optional retention. It is deliberately a conservative reuse
allowance rather than a claimed universal optimum.
Keeping the separate active working sets avoids scan
self-eviction even when an active ledger exceeds this allowance. The existing
entry limits still guard against many tiny historical objects.

Accounting keeps one slotted node per reachable payload identity, holding the
object and its incoming reference count. The immutable payload already holds
its immediate edges: admission and final-owner removal walk those edges
instead of retaining another tuple of identity integers for each container.
Repeated edges and cache roots each count as references; a container's outgoing
edges count only once while it remains live. Sizes are recomputed with
`sys.getsizeof` rather than stored in every node. The temporary admission map
also holds payload objects directly, without per-object `(object, size)` tuples.
This changes bookkeeping storage, not the byte charge, LRU policy, working
sets, validation rules, or retained audit evidence. As before, cached payloads
must remain immutable and acyclic.

Focused accounting tests independently total the identity union shared by a
blob and a full snapshot, verify that deleting one owner keeps shared charges
alive until the last owner is removed, and check global LRU eviction across
caches, replacement, zero-entry limits, and oversized bypass without flushing
smaller entries. A one-byte-budget audit also compares complete JSON against
normal reuse while retaining oversized active objects, rewrite/removal holds,
and an approved migration. These tests exercise reuse policy, not a new
validation limit:

```bash
python3 -m pytest -q \
  .agents/skills/okhp3-replit-repl-janitor/tests/test_retirement_ledger.py \
  -k 'history_accounting or history_byte or history_oversized or tiny_history'
```

The regression fixture rewrites six decisions with 16 KiB evidence fields
across 192 revisions (1,152 distinct rewritten blobs), keeps another decision
unchanged, restores cold trees, and includes an invalid state, removal,
restoration, and an approved policy migration. On Python 3.11.14 in the Replit
Linux workspace, `tracemalloc` measured:

| Reuse strategy | Peak Python heap | Blob reads | Record validations | Audit time |
|---|---:|---:|---:|---:|
| Unbounded blob/validation caches | 19.92 MiB | 1,156 | 1,154 | 12.282 s |
| Former entry bounds plus current-ledger reuse | 5.05 MiB | 1,162 | 1,160 | 12.556 s |

This is approximately 75% less traced peak heap for six additional reads and
validations (about 0.5%). It is not an RSS measurement or a total-memory
guarantee; subprocess memory and Python allocator overhead are not included.
Timing is illustrative, not a performance gate. The test compares full JSON
results and fingerprints all repository files, including Git objects, index,
refs, and reflogs, before and after both audits. It requires a substantial
relative heap reduction and a bounded reread cost, rather than a host-specific
absolute memory or timing threshold.

Reproduce from the repository root:

```bash
python3 -m pytest -q -s \
  .agents/skills/okhp3-replit-repl-janitor/tests/test_retirement_ledger.py \
  -k high_churn
```

#### Large individual records: isolated-process probe

The separate `large_heterogeneous` regression uses three changing evidence
fields of 64 Ki, 384 Ki, and 1.5 Mi characters, with ASCII, Latin-1, and wider
Unicode text respectively, across 40 revisions. These are character counts,
not serialized JSON byte sizes. It also retains a stable baseline decision,
restores the first large-record versions after they leave recent snapshots,
and includes invalid-date, removal/restoration, and approved-migration states.
All four strategies must produce identical complete reports: four retained
anchors, three additions, 117 rewrite holds, one invalid-state hold, one
removal hold, and the exact approved migration. Repository fingerprints must
remain unchanged after each audit.

Each strategy runs in a fresh Python interpreter, so fixture creation and
earlier audits do not accumulate in its Python heap. The controls are:

- unbounded historical blob/validation caches, with the old 32 snapshots;
- the former entry-only caches and 32 snapshots (a pre-budget control);
- production shared byte-budget caches, including snapshot references;
- an experimental minimal-history reader: zero cold blob entries, one
  snapshot, and unchanged current-scan reuse, retention anchors, and report
  evidence. This is a lower-reuse control, not the production configuration.

The original pre-budget probe on Python 3.11.14 in the Replit Linux workspace
produced:

| Strategy | Peak traced heap | Current RSS after audit | Process RSS high-water mark | Blob reads | Audit time |
|---|---:|---:|---:|---:|---:|
| Unbounded historical objects | 139.53 MiB | 167.34 MiB | 167.54 MiB | 124 | 11.21 s |
| Former entry bounds | 139.53 MiB | 167.69 MiB | 167.66 MiB | 124 | 11.39 s |
| Minimal-history control | 12.84 MiB | 32.62 MiB | 59.62 MiB | 128 | 11.56 s |

The result motivated the **shared byte-weighted budget including full
snapshots**, rather than relying solely on entry counts.
In this deliberately large-record case the former entry bounds
provide effectively no heap reduction; reducing historical reuse lowers
peak heap by about 91% for four extra blob reads with identical evidence.
This demonstrates avoidable retention, not the appropriate production byte
limit, expected real-world record distribution, or a total-memory guarantee.
Production now accounts for shared parsed objects across blobs and snapshots,
and bypasses oversized entries instead of failing validation. The active
ledger, retained anchors, every hold, and migration evidence remain complete
even if they exceed the reuse budget.

The final 4 MiB-budget rerun on 2026-10-19 (Python 3.11.14, Linux) produced:

| Strategy | Peak traced heap | Blob reads | Audit time |
|---|---:|---:|---:|
| Unbounded historical objects | 139.53 MiB | 124 | 8.107 s |
| Former entry bounds | 139.54 MiB | 124 | 7.682 s |
| Shared 4 MiB payload budget | 16.35 MiB | 127 | 8.031 s |
| Minimal-history control | 16.33 MiB | 127 | 7.859 s |

The production budget reduced peak traced heap by about **88%** for three extra
blob reads, with identical complete evidence and unchanged repository
fingerprints in all four runs. Its current RSS after the audit was 34.75 MiB;
the RSS high-water mark was already 102.60 MiB at startup and did not change.
The automated regression requires production peak traced heap below 35% of
the former entry-only peak and at most 12 extra blob reads. It imposes no
absolute heap, RSS, or timing limit.

RSS is diagnostic only. The probe reports normalized `ru_maxrss` before and
after the audit where `resource` is available, and current RSS before/after
the audit and after garbage collection from `/proc/self/status` on Linux.
Unavailable measurements are `null`. A fresh interpreter's initial high-water
mark can already be elevated by process startup: the minimal-history run's
59.62 MiB high-water mark was unchanged, despite its much lower current RSS.
RSS includes native allocations, allocator arenas, interpreter overhead, and
the tracing machinery; it is not interchangeable with traced Python heap,
nor does it include separate Git subprocesses. In the earlier entry-only run,
traced heap after collection fell below 0.1 MiB while current RSS remained about 91 MiB,
illustrating why reclaimed Python objects need not immediately return memory
to the operating system. RSS samples and high-water marks use different OS
interfaces and need not agree exactly. No RSS or timing value gates CI;
only the relative traced-heap reduction and evidence integrity do.

Reproduce from the repository root:

```bash
python3 -m pytest -q -s \
  .agents/skills/okhp3-replit-repl-janitor/tests/test_retirement_ledger.py \
  -k large_heterogeneous
```

The complementary wide-stable benchmark retains 1,025 decisions (more than
both LRU limits) across 40 unrelated edits, for 41 distinct root trees (also
more than the 32-snapshot limit). In full-baseline mode it runs both the pre-fix
LRU-only reader and the current-ledger reader against the same real Git history.
It reports peak
Python heap, elapsed time, and read/validation amplification relative to the
number of unique blobs/records. The test requires exactly one read per unique
blob and one validation per record with current-ledger reuse, complete JSON
equivalence to the reference reader, and unchanged repository fingerprints,
including a deliberately dirty checkout. The existing transient-hold and
migration tests also compare the reader with fully uncached results.

Observed full-baseline results in the same Python 3.11.14 workspace:

| Reuse strategy | Peak Python heap | Blob reads | Record validations | Amplification | Audit time |
|---|---:|---:|---:|---:|---:|
| Pre-fix LRU-only | 43.26 MiB | 42,066 | 42,025 | 41.00× | 341.837 s |
| Current-ledger reuse | 8.82 MiB | 1,026 | 1,025 | 1.00× | 11.902 s |

That run reduced traced peak heap by approximately 80% and audit time by
approximately 29×. These are illustrative measurements, not RSS or timing
guarantees. Amplification is measured against 1,026 unique JSON blobs and 1,025
unique record validations. The full-baseline test also requires at least a 50%
relative traced-heap reduction.

Routine tests use unbounded immutable reuse (without current-ledger reuse) as
the full-JSON control, so they still audit all 41 real tree states and enforce
1× current-ledger read/validation counts without repeatedly spending nearly six
minutes reproducing old eviction. To rerun the full slow baseline:

```bash
RETIREMENT_LEDGER_WIDE_LRU_BASELINE=1 python3 -m unittest discover \
  -s .agents/skills/okhp3-replit-repl-janitor/tests \
  -p test_retirement_ledger.py -k wide_stable -v
```

The alternating-wide benchmark uses two distinct sets of 1,025 decision blobs
with matching recovery refs across 34 distinct root trees. Both exceed the
historical blob and validation limits, and the root history exceeds the
snapshot limit. Every B state rewrites the baseline evidence fields; restoring
A must not erase any of the 17,425 commit-specific rewrite holds. Both readers
must produce identical complete JSON and leave all repository bytes, modes,
directories, and symlinks unchanged, including the deliberately dirty policy.

The test reports read/validation amplification and peak traced Python heap for
the LRU-only reference and the two-working-set reader. It requires one read per
unique blob (2,051), one validation per unique record (2,050), and at least 20%
less peak heap than the LRU-only reference. Timing is diagnostic, not a gate;
this is not an RSS or universal process-memory bound. The small working-set
regression separately disables both historical LRUs to prove that repeated
and invalid scans preserve the two sets while colder sets are evicted.

Observed Linux workspace results, with other regression suites running
concurrently (timings are illustrative):

| Reuse strategy | Peak Python heap | Blob reads | Record validations | Read / validation amplification | Audit time |
|---|---:|---:|---:|---:|---:|
| LRU-only | 47.26 MiB | 34,884 | 34,850 | 17.01× / 17.00× | 289.239 s |
| Two distinct working sets | 13.76 MiB | 2,051 | 2,050 | 1.00× / 1.00× | 20.848 s |

That run reduced peak traced heap by approximately 71%, with complete JSON,
all 17,425 rewrite holds, and repository fingerprints unchanged.

As with the stable-wide benchmark, routine QA uses unbounded immutable object
reuse without working-set reuse as a fast complete-JSON control. Run the
several-minute real-Git LRU-only comparison explicitly:

```bash
RETIREMENT_LEDGER_ALTERNATING_LRU_BASELINE=1 python3 -m unittest discover \
  -s .agents/skills/okhp3-replit-repl-janitor/tests \
  -p test_retirement_ledger.py -k alternating_wide -v
```

The wide-invalid benchmark starts from a one-record valid baseline, introduces
1,025 new valid records plus one invalid-date record sorted last, and repeats
the invalid ledger across 40 distinct root trees. Both the new valid prefix
and the root-tree count exceed their respective reuse limits. Its default
reference disables failed-prefix reuse but keeps unbounded immutable caches;
the optional slow reference reproduces the pre-fix bounded-cache behavior.
Both references must produce identical complete JSON, including all 40
commit-specific invalid-ledger holds and no promotion of the new records to
retention anchors. The test fingerprints checkout files, Git metadata, saved
recovery evidence, directories, permission modes, and symlinks before and after
each audit, including a deliberately dirty policy.

The production reader must read each of 1,028 unique blobs once and perform
1,026 successful record validations total, plus one failing validation for each
invalid root. Historical LRUs remain bounded at 128/1,024 entries and snapshots
at 32; failed scans replace only the single failed-prefix slot, never either
successful set. A small zero-history-cache regression also exercises distinct
failed-prefix replacement and subsequent repair, with full uncached-JSON
equivalence.

Run the full real-Git pre-fix comparison explicitly (several minutes):

```bash
RETIREMENT_LEDGER_WIDE_INVALID_LRU_BASELINE=1 python3 -m unittest discover \
  -s .agents/skills/okhp3-replit-repl-janitor/tests \
  -p test_retirement_ledger.py -k wide_invalid -v
```

Peak traced Python heap and elapsed time are diagnostics, not RSS limits or
timing guarantees. Reuse bounds limit optional history, not accepted record
size or retained audit evidence.

Observed Python 3.11.14 Linux workspace results, with the broader regression
suite running concurrently:

| Reuse strategy | Peak Python heap | Blob reads | Successful validations | Failing validations | Audit time |
|---|---:|---:|---:|---:|---:|
| Pre-fix historical LRUs | 3.56 MiB | 41,042 | 41,001 | 40 | 360.340 s |
| Separate failed-prefix reuse | 3.76 MiB | 1,028 | 1,026 | 40 | 13.176 s |

The valid prefix was previously revalidated about 40 times (39.96× total
successful-validation amplification); it is now validated once. Blob-read
amplification fell from 39.92× to 1×, and this audit ran approximately 27×
faster. The separate failed working set cost approximately 0.20 MiB of traced
peak heap in this fixture. Both runs preserved identical full JSON, every
per-commit hold, and read-only fingerprints. These are fixture measurements,
not a universal memory or wall-clock guarantee.

#### Optional ACL immutability controls

The normal test snapshot compares bytes, Unix mode bits, directory/link types,
and hard-link relationships. Mode bits alone do **not** capture extended
access-control rules. The optional `acl_changes` controls enable numeric,
header-free `getfacl` evidence for every non-symlink snapshot entry, including
the repository root and Git metadata directories. They require Linux POSIX ACL
semantics and existing `getfacl` / `setfacl` executables; neither utility is a
mandatory project dependency.

```bash
python3 -m unittest discover \
  -s .agents/skills/okhp3-replit-repl-janitor/tests \
  -p test_retirement_ledger.py -k acl_changes -v
```

For both SHA-1 and SHA-256 repositories, the controls change a non-owner named
user's ACL from `---` to `r--` on a checkout decision file and `.git/index`.
The ACL mask is explicitly read-enabled and kept fixed, so effective access
for that named user changes while file bytes, ordinary mode bits, types, and
hard-link relationships remain identical. This uses ACL resolution rules,
not the runner's `os.access()` result, which root privileges could bypass.
Cached and uncached audits are exercised in both formats; SHA-256 also checks
the CLI guard. A mutation-completed sentinel prevents a setup assertion from
being mistaken for successful guard detection. Every injected change restores
the exact saved ACL in `finally`, verifies snapshot equality, and reruns the
unmodified audit. The outer cleanup also restores any preparatory mode/ACL
changes.

Unsupported platforms, missing utilities, or recognized filesystem/permission
limitations produce an explicit `unittest` skip. Unexpected command failures
and restoration failures remain test failures. A skipped control is **not**
evidence of ACL immutability; ordinary tests still cover only their non-ACL
snapshot fields. These controls do not prove macOS/NFSv4/Windows ACL behavior,
directory default-ACL mutation detection, arbitrary extended attributes,
SELinux labels, or an actual login as the synthetic named user. ACL evidence
is a test-only opt-in, not a new production audit guarantee.

#### Final byte-budget regression results

The same 2026-10-19 verification run passed all **96 tests and 267 subtests** in
`tests/test_retirement_ledger.py`, including accounting, eviction, oversized
bypass, retention holds, migrations, and repository immutability checks.
The complementary production measurements were:

| Fixture | Peak traced heap | Blob reads | Successful record validations | Audit time |
|---|---:|---:|---:|---:|
| High churn, unbounded control | 19.92 MiB | 1,156 | 1,154 | 10.601 s |
| High churn, shared budget | 5.66 MiB | 1,162 | 1,160 | 11.211 s |
| Wide stable, shared budget | 18.48 MiB | 1,026 | 1,025 | 15.996 s |
| Alternating wide, shared budget | 22.05 MiB | 2,051 | 2,050 | 25.496 s |
| Wide invalid, shared budget | 5.09 MiB | 1,028 | 1,026 | 10.895 s |

Churn retained its bounded reread cost and approximately 72% peak-heap saving.
Both successful wide fixtures retained exactly 1× read/validation
amplification; the invalid-prefix fixture retained every one of its 40
per-commit failures with only 40 failing validations. Full reports matched
their controls and repository fingerprints were unchanged. The wide-ledger
measurements include accounting overhead and are higher than the earlier
pre-budget working-set measurements above; the 4 MiB allowance reduced the
initial 8 MiB trial's wide-stable peak from 23.88 to 18.48 MiB and
alternating-wide peak from 31.91 to 22.05 MiB. This is not a claim that every
record distribution has lower total heap.

The optional several-minute historical LRU-only baselines were not rerun in
this verification; their explicit controls keep the old entry-only caches
and 32-snapshot behavior rather than silently adopting the new byte budget.
Routine wide tests use the documented unbounded immutable-object controls
and shared-budget snapshots, without active working-set reuse in the controls.
Timing remains diagnostic.

```bash
python3 -m pytest -q -s \
  .agents/skills/okhp3-replit-repl-janitor/tests/test_retirement_ledger.py
```

#### Compact bookkeeping verification

The compact-accounting probe separates three measurements:

- **Payload:** the high-water `used_bytes` charge for reachable cached keys
  and values, with shared identities charged once.
- **Bookkeeping:** sampled storage for the identity map, LRU accounting table,
  slotted nodes, entry/root tuples and identity integers. This excludes the
  payload, cache containers, shared small counter integers and allocator
  overhead. These are component high-water marks, not additive total peaks.
- **Peak traced heap / RSS:** the existing whole-audit measurements, including
  transient admission/eviction work, active working sets and required reports.
  RSS remains diagnostic, not a hard acceptance threshold.

The independent 1,025-record, 32-snapshot accounting regression retains
**1.98 MiB of payload and 0.48 MiB of bookkeeping**, at both 4 and 8 MiB
allowances. Reconstructing the previous four-element nodes and stored
identity-edge tuples for that *same graph* requires **5.86 MiB** of bookkeeping,
a reduction of about 92% with the compact layout. This reconstruction
is a representation-size comparison, not a traced-heap benchmark. The test
independently totals payload identities, exercises repeated edges and
identical key/value roots, and verifies that final-owner removal clears every
node and byte charge.

Replaying the real wide-stable fixture on Python 3.11.14/Linux with the
original **8 MiB trial allowance** gives:

| Accounting layout | Payload high-water | Bookkeeping high-water | Peak traced heap | Blob reads | Validations |
|---|---:|---:|---:|---:|---:|
| Previous stored edges | 6.04 MiB | 14.06 MiB | 23.88 MiB | 1,026 | 1,025 |
| Compact nodes / payload edges | 6.04 MiB | 6.59 MiB | 15.47 MiB | 1,026 | 1,025 |

Thus the real fixture's bookkeeping falls about 53%, and whole-audit peak
traced heap falls about 35%, with the same payload charge and 1×
read/validation amplification. Both runs compare complete reports against the
reference reader and preserve checkout/Git fingerprints. The 8 MiB allowance
is patched only in this probe; production remains at 4 MiB. This is not a
return to the earlier pre-budget 8.82 MiB measurement or a universal RAM bound.

The production **4 MiB allowance** was also rerun:

| Fixture | Previous peak heap | Compact peak heap | Compact payload high-water | Compact bookkeeping high-water | Blob reads / validations |
|---|---:|---:|---:|---:|---:|
| Wide stable, SHA-1 | 18.48 MiB | 12.96 MiB | 4.00 MiB | 5.20 MiB | 1,026 / 1,025 |
| Alternating wide, SHA-1 | 22.05 MiB | 16.87 MiB | 3.93 MiB | 6.03 MiB | 2,051 / 2,050 |
| Alternating wide, SHA-256 | Not replayed | 16.96 MiB | 4.00 MiB | 6.01 MiB | 2,051 / 2,050 |
| High churn | 5.67 MiB | 6.06 MiB | 3.33 MiB | 1.00 MiB | 1,162 / 1,160 |

The stored-edge comparison runs loaded the pre-change budget implementation
with the same fixtures. Wide stable saves about 30% peak heap; alternating
SHA-1 saves about 23%. Churn's peak is slightly higher despite smaller
bookkeeping, so this is specifically a metadata-efficiency improvement, not a
claim of lower total heap for every distribution. Churn retains exactly the
same six extra reads and validations over its unbounded control
(1,156 reads / 1,154 validations). Timing is diagnostic; independent checks
ran concurrently during these measurements.

The isolated heterogeneous-large-record fixture still compares complete
reports across unbounded, entry-bounded, byte-bounded and minimal-history
strategies. Compact byte-bounded reuse uses **16.29 MiB peak traced heap**,
**3.45 MiB payload**, and **0.022 MiB bookkeeping**, with 127 reads and 125
validations. The entry-bounded control uses **139.54 MiB peak heap**, with
124 reads and 122 validations. All rewrite/removal/invalid-ledger holds and
the approved migration remain present, and repository fingerprints are
unchanged.

Verification passed **137 retirement-ledger tests** (two optional ACL probes
skipped because `getfacl`/`setfacl` are unavailable), **83 repository-audit
integration tests**, and the focused accounting tests at both allowances.
The final focused accounting replay uses the singleton-aware 5.86 MiB
stored-edge reconstruction quoted above. No valid-record size limit,
production budget change, working-set change, or new oversized-stable
completeness fixture was introduced.

```bash
python3 -m unittest discover \
  -s .agents/skills/okhp3-replit-repl-janitor/tests \
  -p test_retirement_ledger.py -v
python3 -m unittest discover \
  -s .agents/skills/okhp3-replit-repl-janitor/tests \
  -p test_audit_repo.py -v
```

### Retirement steps

1. Resolve the exact recovery ref's commit and review why it is no longer
   needed. Verify the replacement path; dates alone are not evidence.
2. Obtain owner approval for the location/format and each record's exact
   ref, commit, evidence, date, and approver. Redact and commit the policy and
   records. Do this **before** the snapshot, so committing evidence cannot
   change protected refs during the comparison.
3. Preflight the selected committed record against the still-present ref:

   ```bash
   python3 .agents/skills/okhp3-replit-repl-janitor/scripts/audit-repo.py \
     --root . \
     --validate-retirement-ledger governance/recovery-retirements/policy.json \
     --approve-recovery-retirement \
     'refs/recovery/feature-example=Protected commit is reachable from verified origin/main; owner reviewed the retained work.'
   ```

4. Take a fresh snapshot at a new nonexistent path. `mktemp` creates a file
   that the snapshot writer correctly refuses to overwrite; instead use a
   new filename inside a temporary directory. Retain it until verification
   succeeds:

   ```bash
   snapshot_dir="$(mktemp -d)"
   snapshot="$snapshot_dir/before.json"
   python3 .agents/skills/okhp3-replit-repl-janitor/scripts/audit-repo.py \
     --root . --snapshot-recovery "$snapshot"
   ```

5. Only after successful preflight and snapshot, delete the one approved ref
   outside the audit. Supply the recorded commit as the expected old value so
   a moved tip cannot be deleted. Substitute the actual reviewed ID:

   ```bash
   git update-ref -d refs/recovery/feature-example \
     0123456789abcdef0123456789abcdef01234567
   ```

6. Verify against the snapshot, retained ledger, and exact evidence:

   ```bash
   python3 .agents/skills/okhp3-replit-repl-janitor/scripts/audit-repo.py \
     --root . --verify-recovery "$snapshot" \
     --retirement-ledger governance/recovery-retirements/policy.json \
     --approve-recovery-retirement \
     'refs/recovery/feature-example=Protected commit is reachable from verified origin/main; owner reviewed the retained work.'
   ```

7. Require exit zero, `recovery_guard.passed: true`, and the exact validated
   decision in `recovery_guard.retirement_ledger.decisions`. A ledger failure
   returns a redacted JSON error and nonzero exit; a protected-state failure
   reports the recovery holds and nonzero exit. Neither is completed cleanup.
   Stop and preserve the snapshot/ref evidence on failure; do not weaken the
   comparison or automatically undo owner work.
8. Retain the policy and decision records in Git permanently. Retain the
   verification report in the project's approved internal evidence location
   if required by its governance. Temporary snapshots can be removed only
   after successful verification; their removal must never delete the ledger.

The guard does not authorize abandonment of reachable objects. Even an
owner-approved retirement cannot pass if it loses objects previously reachable
from refs. Preserve the work under another retained ref or verified base first.
No prune, garbage collection, publication, or push is part of this procedure.

## Later cleanup: audit retained history

Before relying on earlier retirement decisions during later cleanup, ask the
owner to select a committed baseline containing the approved policy and
records. Use the policy path **at that baseline**, not an inferred checkout
location. The audit reads Git blobs only, from that baseline through the HEAD
commit captured at startup; dirty files are neither used nor changed.

```bash
python3 .agents/skills/okhp3-replit-repl-janitor/scripts/audit-repo.py \
  --root . \
  --audit-retirement-history governance/recovery-retirements/policy.json \
  --ledger-baseline '<owner-selected commit>'
```

Require exit zero and `retirement_history.passed: true`. The JSON identifies
the resolved baseline and head IDs, states checked, retained/additional record
counts, approved migrations, and holds with the exact offending commit.
Removal or any changed record field is a hold, even if a later commit restores
it. New records become retention anchors when first seen, so their later
removal or rewrite also fails. JSON whitespace, key order, and record filename
changes are not substantive; the record is identified by its exact recovery
ref and all decision fields must remain identical. Neither the retired ref nor
its protected object needs to exist for this check.

This is a **first-parent mainline audit**, including each merge's resulting
tree, not an audit of every intermediate side-branch commit. The baseline must
be on HEAD's first-parent chain; missing history, an absent baseline ledger,
malformed records, duplicate fields/refs, unsupported formats, or redirected
locations fail visibly. An empty decisions directory is not tracked by Git;
zero current records are still compared with earlier retained records.
Choose a baseline before the decisions of interest. The audit cannot prove
retention before that baseline, detect rewritten Git history for which no
trusted baseline remains, certify the truth of evidence, or authenticate
human approval. Baseline records may predate a previously approved policy
migration; new records must not predate the policy active when first seen.

### Owner-approved policy or location migrations

A changed policy (including approval metadata or records directory), or a
new policy location, needs renewed owner approval. Record that approval
through the normal owner-review channel, then supply an exact allowance:

```text
--approve-ledger-migration '<full migration commit ID>=<old policy path>,<new policy path>'
```

Repeat for each migration in chronological mainline order. For policy changes
at the same location, use the same path on both sides. Use full lowercase
SHA-1/SHA-256 commit IDs, not branch names. Paths cannot contain commas in
this allowance syntax. Approvals for commits outside the selected interval,
duplicate approvals, incorrect source paths, or allowances with no policy
change are rejected. Merely committing `approved_by` metadata is not an
independent migration allowance.

At each approved commit the audit switches to the new policy, reports
`owner-approved-policy-migration`, and still requires every older decision to
remain substantively identical. Original decision dates are preserved even
when the renewed location approval is later. Migration approval cannot
authorize deletion, rewrite evidence, bypass unsafe JSON, or enable a format
the validator does not support (currently only format 1). This history audit
does not authorize a new retirement or replace pre-removal preflight.

### Prove migrated decisions during a new retirement

For current-HEAD preflight and post-removal verification, supply all three
parts of owner-reviewed provenance: the trusted pre-migration baseline commit,
the policy path at that baseline, and every exact migration allowance through
HEAD. Do not substitute an unreviewed baseline or infer approvals from policy
metadata. No new policy or decision fields are needed.

```bash
python3 .agents/skills/okhp3-replit-repl-janitor/scripts/audit-repo.py \
  --root . \
  --validate-retirement-ledger governance/retained/policy.json \
  --ledger-baseline '<owner-selected pre-migration commit>' \
  --ledger-baseline-policy governance/recovery-retirements/policy.json \
  --approve-ledger-migration \
  '<full migration commit ID>=governance/recovery-retirements/policy.json,governance/retained/policy.json' \
  --approve-recovery-retirement '<exact new recovery ref>=<exact recorded evidence>'
```

For verification, replace `--validate-retirement-ledger` with
`--verify-recovery '<snapshot path>' --retirement-ledger '<current policy path>'`
and repeat the same provenance and exact retirement allowances. Renewed policy
approvals at the same path use identical old/new paths. Repeat migration flags
for successive moves; the baseline must precede all supplied migrations.

Both modes run the full first-parent retention audit and require it to pass
and end at the selected current policy. Only unchanged decisions present just
before the latest approved migration may predate the current policy date.
Decisions introduced during or after that migration still need dates on or
after the renewed approval. Earlier additions must also satisfy the policy
active when first recorded. Removal, any substantive rewrite (even restored
later), missing history, partial proof, or incorrect allowances block the
retirement. Without explicit proof, the original date check remains in force.
Successful results include `migration_provenance` inside the validated ledger
report, with baseline/head IDs, approved migrations, and checked-state count.
When this history check finds holds, preflight and verification instead return
exit 1 with the existing `error` and `root` plus a top-level `retention_holds`
list. Each hold includes the offending `commit` and `reason`. Record-level
holds include the validated `ref`; rewrites also include sorted `changed_fields`
names. Invalid ledgers or policy-level holds may have no safely validated ref
or field names. Historical field values, record paths, and raw error details
are omitted. Every offending commit remains listed, even after restoration.
These failures never include a successful ledger or recovery result.
The current files must still be committed unchanged, and each selected
retirement must still match the exact ref, snapshot commit, live commit object,
and approved evidence. Provenance is not retirement permission.

Without an allowance, policy edits produce `unapproved-policy-change`;
removing the old location produces `invalid-ledger`. Deleted records produce
`removed-record`; altered decision fields produce `rewritten-record` with
field names, never the historical evidence text. Stop on any hold; do not
change the baseline or invent migration approval to make the report pass.
The history mode cannot be combined with fetching, hosted probes, snapshot
verification, or branch deletion planning, and writes no files or refs.