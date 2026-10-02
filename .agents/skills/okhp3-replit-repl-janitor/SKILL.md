---
name: okhp3-replit-repl-janitor
description: >
  OverKill Hill P³ one-Repl repository cleanup workflow for safely auditing and
  tidying a single Replit workspace's Git checkout. Use when a user asks to
  clean up merged or abandoned branches and pull requests, purge stale
  subrepl-* or agent/* branches, normalize file and folder names, or remove
  repository detritus. Also activate for "decrapify this Repl", "tidy this
  repo", "prune dead branches", or "fix inconsistent filenames". This is the
  authoritative one-time cleanup workflow for one Replit checkout; use
  okhp3-repository-janitor for recurring maintenance and multiple local clones,
  and okhp3-repository-organizer for structural
  reorganization.
license: MIT
metadata:
  author: Jamie Hill (OverKill Hill P³)
  version: "1.0.1"
  category: developer-tooling
  origin: okhp3/skillz
  homepage: https://overkillhill.com
  author-github: https://github.com/OKHP3
  in_scope:
    - Auditing one Replit checkout's local branches against a verified base ref
    - Resolving pull-request state before classifying branches for keep, merge, delete, or review
    - Treating Replit-generated branch names as hints rather than deletion proof
    - Auditing kebab-case naming with documented structural exceptions
    - Finding nested detritus folders and preparing an owner-approved cleanup plan
    - Executing only the exact cleanup items the owner explicitly approves
  out_of_scope:
    - Recurring repository maintenance and worktree reconciliation
    - Multi-repository mirror estates across computers or clones
    - Deep repository classification, taxonomy design, or governance scaffolding
    - Autonomous merges, deletions, renames, force-pushes, or publication
    - Rewriting main or deleting stashes, archive refs, or unreviewed work
---

# okhp3-replit-repl-janitor

**OverKill Hill P³** · [overkillhill.com](https://overkillhill.com) · [github.com/OKHP3](https://github.com/OKHP3)

Safely clean one Replit workspace's Git checkout without confusing generated
branch names with proof, a closed pull request with a merged one, or an untidy
filename with permission to rename it. The workflow is read-only first,
evidence-led, and destructive only after the owner approves exact line items.

---

## Scope

| In scope | Out of scope |
|---|---|
| One-time cleanup of one Replit checkout | Recurring maintenance; use `okhp3-repository-janitor` |
| Branch and PR classification against a verified base | Multi-clone reconciliation; use `okhp3-repository-janitor` |
| Naming and detritus audit | Structural redesign; use `okhp3-repository-organizer` |
| Exact, owner-approved cleanup execution | Autonomous deletion, merging, renaming, or publishing |

---

## Safety contract

1. **Start read-only.** Run the bundled audit without `--fetch`; it reads the
   current checkout and prints JSON. Fetch separately only when network access
   is appropriate. Never prune during discovery.
2. **Fail visibly.** A missing base ref, failed Git command, detached HEAD, or
   non-repository path is evidence, not an empty result. Stop and report it.
3. **Separate three facts.** "Merged into `origin/main`", "closed pull request",
   and "abandoned with no PR" are different states. Never substitute one for
   another.
4. **Names are hints, not verdicts.** `subrepl-*`, `replit-agent`, and `agent/*`
   suggest generated work but do not prove it is safe to delete. The current
   branch is never a deletion candidate.
5. **Plan before touching.** Return the exact `keep`, `merge`, `delete`, and
   `review` branch buckets plus file actions. Wait for explicit approval of
   individual destructive lines.
6. **Rename atomically.** A file rename must update every importer and link in
   the same change. A public URL needs a redirect or transition plan.
7. **Protect recovery paths.** Never rewrite `main`, force-push, or delete
   stashes or archive refs under this skill.

---

## Workflow

### 1. Establish a trustworthy baseline

Record the repository root, current branch, configured remotes, base ref, and
working-tree status before making recommendations:

```bash
git status --short --branch
git remote -v
git rev-parse --verify origin/main
```

If remote-tracking refs may be stale, run `git fetch --all` separately and
inspect its result. Do **not** add `--prune`: pruning before classification
destroys evidence about stale remote branches.

> **NON-NEGOTIABLE — NO EARLY PRUNING:** Never run `git fetch --all --prune`,
> `git fetch --prune`, or `git remote prune` until **every** stale remote branch
> has been classified. Refresh refs without pruning during discovery.

Run the deterministic local audit:

```bash
python3 .agents/skills/okhp3-replit-repl-janitor/scripts/audit-repo.py \
  --root . \
  --base origin/main
```

The script is read-only by default. `--fetch` is available only as an explicit
opt-in and still never prunes.

### 2. Resolve pull-request state

The audit reports Git facts; it does not infer hosted pull-request state. For
every unmerged branch, use the `git-remote` skill or GitHub CLI:

```bash
gh pr list --head '<branch>' --state all
gh pr view <number> --json state,mergeStateStatus,reviewDecision,statusCheckRollup,headRefOid
```

Do not infer "no PR" from a failed network call. Record the lookup as unknown
and place the branch in `review`.

For read-only hosted evidence and cleanup holds, pass each exact remote/branch
pair with `--hosted-branch origin=feature/example` (repeat as needed).
`--hosted-ref` is an alias; `PROVIDER:BRANCH` is also accepted. The audit probes
the remote ref without fetching or pruning. For GitHub remotes it reads
protection, deployment, and PR evidence through an installed, authenticated
`gh` CLI; unavailable evidence remains an explicit hold.
Deployment and PR history are read in 100-record pages until a short or empty
page confirms completion. A failed or malformed page discards that partial
history and retains its unknown-evidence hold, with the failing page identified.
Run hosted inspection separately from
`--check-delete`; combining those options is rejected before fetching or
preparing deletion commands.

Each hosted `git ls-remote` or `gh api` command has a **30-second timeout**.
A timed-out remote probe is `inaccessible` with an unknown ref and a
`hosted-remote-inaccessible` deletion hold. A timed-out GitHub request leaves
its protection, deployment, or PR evidence `unknown` and retains the
corresponding unknown-evidence hold. The reason states that the command timed
out and gives the time limit. Partial output is discarded, never interpreted
as a missing branch, empty history, or deletion approval. Other evidence
lookups and requested provider/ref pairs still run; the limit is per command,
not a total audit deadline (remote and protection probes plus all deployment
and PR history pages for each present GitHub ref).
This limit does not apply to local Git checks or the separate opt-in `--fetch`.

### 3. Classify every branch

Every non-current, non-`main` branch belongs in exactly one bucket:

| Bucket | Evidence rule |
|---|---|
| `keep` | Active work, an open wanted PR, or deliberately retained history |
| `merge` | Approved PR, required checks passing, exact head verified |
| `delete` | Verified merged into the base, or owner-confirmed abandoned with no wanted PR |
| `review` | Unique commits, unknown PR state, failed lookup, or unclear intent |

Generated naming affects the explanation, never the bucket by itself.

### 4. Audit naming and detritus

Apply `references/naming-conventions.md`:

- default to kebab-case;
- preserve PascalCase `.tsx`/`.jsx` components;
- preserve camelCase `useFoo.ts` hooks;
- preserve root governance, tool-required, and web-standard filenames;
- flag spaces, mixed case, underscores, and uppercase extensions when no
  exception applies.

For detritus folders such as `attached_assets/`, `_unused/`, `_drafts/`,
`_scratch/`, `_old/`, `tmp/`, `temp/`, and `unused/`, inspect contents before
choosing delete or gitignore-and-untrack. A folder name is evidence for triage,
not permission to discard its contents.

### 5. Present the plan and stop

Use this exact report shape:

```markdown
## Branches & PRs
- KEEP: <branch> — <evidence>
- MERGE: <branch> — <PR and check evidence>
- DELETE: <branch> — <merged or owner-confirmed abandonment evidence>
- REVIEW: <branch> — <unknown fact and smallest next check>

## A. DELETE
- <path> — <why it is verified disposable>

## B. GITIGNORE-AND-UNTRACK
- <path> — <why it is generated but currently tracked>

## C. TRIAGE-THEN-DELETE
- <path> — <what must be inspected first>

## D. RENAME
- <old> → <new> — <rule, import/link impact, validation>
```

Stop. General approval of "the cleanup" is not approval of every destructive
line; obtain an exact go/no-go for each merge, delete, and rename.

### 6. Execute approved items in small batches

Before each branch operation, run the bundled pre-delete check with the exact
branch and SHA recorded in the approved plan:

```bash
python3 .agents/skills/okhp3-replit-repl-janitor/scripts/audit-repo.py \
  --root . \
  --check-delete \
  --branch '<branch>' \
  --reviewed-head '<reviewed SHA>'
```

The JSON result records both `reviewed_head` and the freshly read
`current_head`. If the bucket is `review`, stop, record the hold, and run no
deletion command. Only a `delete` result may be executed, and its
`deletion_commands` must be run in the emitted order. The sequence is
remote-first (`git push origin --delete <branch>`) and local second
(`git branch -d <branch>`). The check is read-only and never executes either
command.

For an approved merge:

1. Confirm PR approval and required checks.
2. Confirm the PR head SHA matches the reviewed branch head.
3. Squash-merge through the pull request.
4. Fetch and verify the merged result is reachable from the base.
5. Delete the exact remote branch first.
6. Delete its local tracking branch second.

For approved files, use `git rm` and `git mv` so the change is explicit. Plain
`rm` is acceptable only for an untracked or gitignored working file.

#### Retain recovery-ref retirement decisions

Recovery refs are not ordinary branch-delete candidates. Retiring one requires
the owner's approval of the **exact ref, protected commit, evidence, decision
date, approver, ledger location, and JSON format**. An elapsed retention period
alone is not evidence. Do not infer approval from a record supplied by tooling.

Before the first retirement, have the owner approve a repository-private
ledger location. The suggested layout is
`governance/recovery-retirements/policy.json` plus a `decisions/` directory;
this is a proposal, not an already approved location. Keep this ledger outside
public-site release artifacts and never use `.scratch`, `/tmp`, `.git`, or a
generated report as the retained record. See
`references/retirement-ledger.md` for the exact format and full procedure.

Commit the approved policy and one append-only JSON record per exact recovery
ref **before** taking the pre-removal snapshot. Store the full protected commit
ID, evidence explaining why the work no longer needs this ref, ISO decision
date, and a public approver handle or role (not credentials or personal contact
details). Review/redact the content before committing. The validator rejects
unknown fields and common credential patterns, but cannot certify that free
text contains no secret.

The audit remains read-only: it writes only an explicitly requested snapshot,
never deletes a ref or creates approval records. Run ledger preflight with
`--validate-retirement-ledger '<approved policy path>'` and
`--approve-recovery-retirement '<exact ref>=<exact recorded evidence>'`.
Then take a snapshot at a **new, nonexistent path**, remove only the explicitly
approved ref using its reviewed commit as Git's expected old value, and run
`--verify-recovery '<snapshot path>' --retirement-ledger '<approved policy path>'`
with the same exact retirement allowance. Do not combine recovery modes with
fetch, hosted probes, or branch-delete checks.

A retirement is complete only when verification exits zero, reports
`recovery_guard.passed: true`, and includes the validated retained decision in
`recovery_guard.retirement_ledger.decisions`. Missing, uncommitted, modified,
malformed, duplicate, or mismatched records block completion. Without a retained
record, an approval flag alone cannot make retirement pass. Unapproved ref
changes, stash changes, or loss of previously reachable objects still fail.
Keep the ledger and verification evidence after cleanup; a JSON stdout report
is not a replacement for the committed decision.

During later cleanup, verify append-only retention from an owner-selected
committed baseline with `--audit-retirement-history '<baseline policy path>'`
and `--ledger-baseline '<baseline commit>'`. Require exit zero and
`retirement_history.passed: true`. The read-only audit checks every first-parent
state through HEAD, including intermediate deletions or rewrites later
restored, without requiring retired refs or objects to exist. Policy/location
migrations need exact owner-reviewed `--approve-ledger-migration
'<full commit>=<old policy>,<new policy>'` allowances and must preserve all
old decision fields. See `references/retirement-ledger.md` for scope,
approval syntax, and failure reporting; do not infer migration approval
from committed metadata or move the baseline to conceal a hold.

### 7. Verify and report

After every approved batch:

```bash
python3 .agents/skills/okhp3-replit-repl-janitor/scripts/audit-repo.py \
  --root . \
  --base origin/main
git status --short
```

If a tracked source file moved or was renamed, run the relevant validation and
restart the affected workflow, then inspect its logs and preview. Report what
changed, what remains, and which recovery path is still available.

---

## Output contract

Return:

- repository root, current branch, base ref, and whether refs were fetched;
- the four branch buckets with evidence for every item;
- the four file-action sections;
- unresolved unknowns and the smallest safe next check;
- exact writes performed, or an explicit statement that discovery was
  read-only;
- post-change audit, Git status, and workflow validation results when execution
  was authorized.
- for recovery retirement, the committed ledger policy and record paths,
  exact ref and protected commit, evidence, decision date, approver, and
  successful recovery/ledger validation; otherwise report retirement incomplete.

When hosted branches are requested, JSON includes
`hosted_lifecycle.cleanup_plan` with `keep`, `merge`, `delete`, and `review`
arrays. Each held item retains its exact `provider` and `ref`, and includes:

- `blocking_reasons`: the sorted, deduplicated stable machine reason codes.
  Tooling must use these codes, not explanation text.
- `blocking_reason_explanations`: an array in the same order, with one
  `reason_code` and concise reviewer-facing `explanation` for each hold.
  Present the explanation alongside its code; do not replace or hide codes.

For example:

```json
{
  "provider": "origin",
  "ref": "feature/example",
  "blocking_reasons": ["hosted-ref-protected"],
  "blocking_reason_explanations": [{
    "reason_code": "hosted-ref-protected",
    "explanation": "The hosted branch is protected; keep it."
  }]
}
```

| Stable reason code | Reviewer meaning | Bucket |
|---|---|---|
| `hosted-remote-inaccessible` | Remote access failed; check access | `review` |
| `hosted-ref-missing` | Branch was not found; confirm its location | `review` |
| `hosted-ref-protected` | Branch is protected | `keep` |
| `hosted-ref-has-deployments` | Deployment records exist; review their use | `keep` |
| `hosted-open-pull-request` | An open PR still depends on this branch | `keep` |
| `hosted-closed-unmerged-pull-request` | A closed PR was not merged; review its work | `review` |
| `hosted-protection-unknown` | Protection could not be confirmed | `review` |
| `hosted-deployment-evidence-unknown` | Deployment evidence is unavailable | `review` |
| `hosted-pull-request-evidence-unknown` | PR evidence is unavailable | `review` |
| `hosted-evidence-unknown` | A blocked item supplied no reason; review source evidence | `review` |

Unknown future codes are preserved with an explicit unrecognized-hold
explanation. Mixed holds use `keep` if any reason requires retention, while
still explaining every reason. Hosted holds never populate `merge` or `delete`;
unblocked entries are not deletion approvals and are omitted from this plan.

---

## Failure handling

| Condition | Result |
|---|---|
| Base ref missing | Stop; ask which verified base ref to use |
| Git command or fetch fails | Stop; show the failed command and stderr |
| Detached HEAD | Audit may continue, but no branch deletion may be recommended until the active work is identified |
| PR lookup unavailable | Put affected branches in `review`; never infer abandonment |
| Hosted command times out | Preserve an inaccessible or unknown-evidence hold with a timeout reason; continue other requested checks, never approve deletion |
| Unique unmerged commits | Preserve in `review` unless the owner explicitly abandons them |
| Rename affects public URL | Require redirect or transition plan before execution |
| Approval is broad or ambiguous | Ask for exact approved line items |

---

## Resources

- `scripts/audit-repo.py` — deterministic, no-fetch-by-default JSON audit of
  branches, naming violations, and nested detritus.
- `scripts/recovery-guard.py`: read-only snapshot integrity and protected-state
  comparison used by the audit.
- `scripts/retirement-ledger.py`: validates committed owner-approved policy and
  retirement records before the guard accepts retirement.
- `references/retirement-ledger.md`: retained decision format, owner approval,
  redaction, and preflight/post-removal procedure.
- `references/naming-conventions.md` — portable kebab-case policy and structural
  exceptions.
- `references/foundry-architecture.md` — Phase 1 intent, scope, and brand
  decision for this renamed skill.
- `evals/evals.json` — three live-evaluation prompts with four anchored
  expectations each.
- `benchmarks/benchmark.json` — version-matched Foundry evidence after live
  execution.

---

## About

Built by [Jamie Hill](https://overkillhill.com) · [OverKill Hill P³](https://overkillhill.com)
Published at [github.com/OKHP3](https://github.com/OKHP3)
Part of the [OKHP3/skillz](https://github.com/OKHP3/skillz) Agent Skill library.
MIT License -- free to use, fork, and adapt. A nod to the source is appreciated.
