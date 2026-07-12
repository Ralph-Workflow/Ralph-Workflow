# Policy schema upgrade prompt — all-or-nothing choice — design

Date: 2026-07-12
Status: approved

## Problem

When a repo already has customized policy files under
`docs/ralph-workflow-policy/` written against an older (but still valid)
schema, `ralph run`'s preflight detects the drift and offers to upgrade
them to the current schema. The original prompt asked **once per file**
("Upgrade `docs/ralph-workflow-policy/testing-policy.md` through the
remediation agent?"). With up to 20 policy files that is a wall of
near-identical questions, and a user who wants to keep their current
policy has to answer "no" over and over. There was also no hint about how
to reverse a freeze once it was written.

## Decision summary

* The preflight asks **exactly one** question for the whole set, not one
  per file. It first lists every outdated file, then asks a single
  all-or-nothing question.
* **Upgrade everything** (accept / default path): every listed file is
  handed to the remediation agent, which rewrites it to the current
  schema. No freeze markers are written.
* **Skip everything** (decline): every listed file is frozen at its
  current schema — a `<!-- ralph-policy-schema: freeze vN -->` marker is
  written to the top of each — so Ralph never re-prompts for them.
* After a skip, Ralph emits the exact undo instructions: delete the
  `<!-- ralph-policy-schema: freeze vN -->` line at the top of the file
  (or change `freeze vN` back to `vN`) and rerun; the upgrade offer
  returns.

There is no partial / per-file mode. If a user wants to freeze some files
and upgrade others, they skip everything, then hand-edit the freeze marker
off the files they want upgraded and rerun.

## Behavior

Implemented in
`ralph/project_policy/cli_integration.py::_maybe_resolve_schema_upgrade`,
called from `run_project_policy_readiness` before the readiness preflight.

1. Collect every file under `CANONICAL_DIR` whose first non-blank line is a
   valid-but-older schema marker. A missing/malformed marker or a
   future-version marker fails closed (returns `False`, blocking the run);
   a freeze marker older than the current schema is respected and skipped.
2. If nothing is outdated, return `True` (continue).
3. Non-interactive runs (no TTY) return `False` with a message telling the
   user to rerun interactively — an unattended run must never silently
   upgrade or freeze policy files, and must never hang on a prompt.
4. Interactive runs: emit the list of outdated files, then ask the single
   all-or-nothing question. Accept → upgrade all. Decline → freeze all and
   emit the undo instructions.
5. A crashing prompt (EOF despite isatty, broken pipe) is caught; the run
   is blocked (`False`) with "no implicit upgrade was applied" rather than
   guessing an answer.

## Prompt wording

```
A newer Ralph policy schema (v2) is available for 3 customized policy file(s):
  • docs/ralph-workflow-policy/testing-policy.md
  • docs/ralph-workflow-policy/linting-policy.md
  • docs/ralph-workflow-policy/security-policy.md

Upgrade all 3 file(s) to v2 through the remediation agent? (Declining
freezes every file at its current schema.) [Y/n]
```

On decline:

```
Froze 3 policy file(s) at their current schema — Ralph will not upgrade them:
  • docs/ralph-workflow-policy/testing-policy.md
  • docs/ralph-workflow-policy/linting-policy.md
  • docs/ralph-workflow-policy/security-policy.md

Changed your mind? Remove the skip: delete the
`<!-- ralph-policy-schema: freeze vN -->` line at the top of the file
(or change `freeze vN` back to `vN`) and rerun — Ralph will offer the
upgrade again.
```

## Testing

`tests/project_policy/test_skip_inline_policy_prompt.py`:

* `test_schema_upgrade_asks_once_for_all_files` — multiple outdated files
  produce exactly one prompt; accepting leaves all files untouched (no
  freeze markers).
* `test_declining_schema_upgrade_freezes_all_and_shows_undo` — one decline
  freezes every file, and the emitted message names each frozen file and
  the undo steps.
* `test_declining_schema_upgrade_freezes_existing_policy` — single-file
  freeze still works.
* `test_future_schema_freeze_fails_closed` — a future/invalid marker
  blocks the run.

## Out of scope

* A per-file interactive picker (deliberately removed — hand-edit the
  freeze marker for per-file control).
* A CLI flag to preselect upgrade/freeze non-interactively.
