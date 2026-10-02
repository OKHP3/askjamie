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

Decision dates use valid `YYYY-MM-DD` calendar dates and cannot predate the
location/format approval. Object IDs must be full lowercase SHA-1 or SHA-256
commit IDs. During a selected retirement, the protected commit must match the
pre-removal snapshot and still resolve to a commit. Evidence must exactly
match the explicit approval allowance. The approver label and evidence are
retained verbatim; no environment variables or secret values are consulted.

## Procedure

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
the validator does not support (currently only format 1). The current
pre-removal preflight remains a separate check with its original chronology
rules; this history audit does not authorize a new retirement or replace it.

Without an allowance, policy edits produce `unapproved-policy-change`;
removing the old location produces `invalid-ledger`. Deleted records produce
`removed-record`; altered decision fields produce `rewritten-record` with
field names, never the historical evidence text. Stop on any hold; do not
change the baseline or invent migration approval to make the report pass.
The history mode cannot be combined with fetching, hosted probes, snapshot
verification, or branch deletion planning, and writes no files or refs.