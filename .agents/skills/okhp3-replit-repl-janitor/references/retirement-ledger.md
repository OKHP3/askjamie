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