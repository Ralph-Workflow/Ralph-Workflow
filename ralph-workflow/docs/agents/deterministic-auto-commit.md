# Deterministic Auto-Commit for Engine-Owned Writes

Ralph Workflow's own code writes files in the background: deterministic
changes whose content is already known (skill updates, `.gitignore` seed,
starter prompt seed, project-policy bootstrap). Without an auto-commit,
those changes leak into the working tree and either get swept into an agent's
later commit or get lost. This page is the contract for the deterministic
auto-commit that captures them at the write site with a fixed commit message.

## What gets committed

Every deterministic engine-owned writer commits its own writes
immediately, at the producer boundary, with a fixed conventional-commit
subject. The current set of routes:

| Writer | Trigger | Subject |
| --- | --- | --- |
| Project-scope skill install (run start, `--init`, `--force-init-skills`) | `install_project_baseline_skills_with_diff` produces a byte-exact diff | `chore(skills): sync baseline bundle` |
| `.gitignore` auto-seed | `auto_seed_default_gitignore` appended missing patterns | `chore(gitignore): seed ralph defaults` |
| Starter prompt seed (`--init`) | `write_text_if_changed` seeds starter `PROMPT.md` | `chore(prompt): seed starter template` |
| Policy preflight | `agents_md.bootstrap` + `_seed_missing_starters` write to canonical / `AGENTS.md` / `CLAUDE.md` | `chore(policy): sync project-policy readiness` |
| Post-pipeline `condense_placeholder_block` | `_finalize_ready_state` rewrites the AGENTS.md managed block | `chore(policy): sync project-policy readiness` |

## Isolation guarantees

The deterministic chore commit is restricted to the byte-exact set of
paths the writer just modified. It will NEVER sweep in:

- **Agent or user edits.** Every producer-level call records the
  pre-write content hash of the path BEFORE writing, then asks git
  to compare the recorded hash to the current HEAD blob. A mismatch
  (HEAD != pre-write hash) means the user or agent edited the file
  in between the writer's snapshot and the commit attempt. Such
  paths are SKIPPED with a warning and stay in the agent / user
  commit flow.
- **Pre-staged unrelated entries.** A user who pre-staged an
  unrelated file (or partially staged a file, including a staged
  deletion) sees their exact pre-staged index state preserved across
  the chore commit. The pre-staged index entries are snapshotted
  via `git ls-files --stage`, the whole set is unstaged, the
  in-scope paths are committed, and the pre-staged entries are
  restored byte-for-byte (deletions via `git update-index --force-remove`).
- **Pre-staged deletions.** A user who staged a deletion sees that
  deletion preserved; the chore commit does not silently revert the
  deletion to the pre-deletion index entry.
- **Post-start agent edits to skill paths.** A path the user or
  agent edited mid-run is NEVER swept into the deterministic
  chore commit. The pre-write hash check at the producer boundary
  catches it.

## Failure semantics

- **No diff = no commit.** A seed with nothing missing (the
  `.gitignore` already covered, the starter `PROMPT.md` already present,
  the skill bundle already current, the policy surfaces already committed)
  is a NOOP. No commit, no error. Pre-staged entries are preserved untouched.
- **Commit failure and skips are visible.** Both `FAILED` and `SKIPPED`
  outcomes are logged at `WARNING` level across all deterministic writers
  (skills installer, project policy, starter prompt seed) and never break
  the pipeline. A failed attempt or partial staging failure also rolls back
  the index completely: newly staged paths are unstaged (`git reset HEAD -- <paths>`),
  and the full pre-staged snapshot is restored byte-for-byte (including staged
  deletions via `git update-index --force-remove`), leaving the index
  identical to its pre-attempt state. There is never a half-staged index.
- **Non-git workspace.** A non-git workspace (e.g. a fresh project
  that has not been `git init`-ed yet) is a `NOT_REPO` outcome.
  The deterministic writer logs the outcome and leaves the file
  content on disk for the user; the next `git init` + run will
  commit it. The pipeline never blocks on a non-git workspace.

## The producer-level primitive

Every deterministic writer routes through
`ralph.git.scoped_auto_commit.commit_deterministic_writes`. The
helper takes:

- `paths` — the byte-exact set of paths the writer touched.
- `pre_contents` — a `dict[path, git_blob_sha | None]` recorded
  BEFORE the write, where `None` means "the path did not exist
  before the write". The blob hash is git's own `git hash-object`
  hash, directly comparable to the value `git ls-files --stage`
  reports for HEAD.
- `subject` — the literal conventional-commit subject.
- `create_commit_fn` / `stage_fn` — the production git operations.

The helper returns a `ScopedCommitResult` with an explicit status
(`CREATED` / `NOOP` / `NOT_REPO` / `FAILED` / `SKIPPED`), the commit
SHA on `CREATED`, and the list of `skipped_paths` (paths already
dirty at HEAD that were left for the agent flow). Callers surface
non-CREATED outcomes to the operator as appropriate; the helper
itself never raises.

## Acceptance cases

`tests/test_skills_auto_commit.py` and
`tests/project_policy/test_policy_auto_commit.py` cover the
acceptance contract end-to-end with real-git fixtures. They run
on the default `make test` profile via
`REQUIRED_AUTO_INTEGRATE_E2E_FILES` so the isolation / rollback
contract cannot rot silently. The audit
(`ralph.testing.audit_skill_auto_commit`) pins the
producer-level discipline: any future refactor that re-adds the
phase-seam skill commit, removes the `commit_deterministic_writes`
wiring, or reintroduces the `authored_paths` scope expansion fails
the audit and the gate.

## Cross-links

- [AGENTS.md §'Non-negotiables'](../../../AGENTS.md) -- the
  no-exemption rule for red gates.
- `ralph/git/scoped_auto_commit.py` -- the shared isolation
  primitive.
- `ralph/skills/_auto_commit.py` and
  `ralph/project_policy/_auto_commit.py` -- the producer-level
  helpers.
- [Verification Guide](../../../docs/agents/verification.md) -- the budget / audit
  policy that owns the auto-commit's runtime discipline.

## Documentation review note

- *What changed.* Added the starter `PROMPT.md` seed row with fixed subject `chore(prompt): seed starter template`. Clarified `WARNING`-level logging across all writers for both `FAILED` and `SKIPPED` outcomes. Documented exact index rollback on partial staging failures and pre-staged deletion preservation.
- *Why this surface owns it.* This document is the canonical reference contract for deterministic background commits.
- *What was pruned or left alone.* The core producer-level isolation primitive structure and acceptance cases were retained and refined to reflect actual production behavior.
- *How duplication was contained.* Kept concise reference tables and explicit invariants aligned with `ralph/git/scoped_auto_commit.py`.
- *Why the route is clearer.* Operators and contributors now have full visibility into every engine-owned background commit route, message format, and rollback guarantee.
