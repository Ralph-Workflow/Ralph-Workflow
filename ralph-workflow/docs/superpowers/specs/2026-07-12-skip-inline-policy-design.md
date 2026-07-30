# Skip inline policy when AGENTS.md has significant content — design

Date: 2026-07-12
Status: approved

## Problem

Ralph's project-policy-readiness preflight unconditionally appends its managed
policy block ("inline policy") to any pre-existing AGENTS.md. A repo that
already maintains its own agent policy gets Ralph's block bolted on with no
say in the matter; the only escape hatch is knowing to hand-write the
byte-exact opt-out marker. Users with an established AGENTS.md should be
offered the choice up front, with clear messaging: Ralph's policy is a good
default if you are not confident in your existing setup, but you can keep
yours if you prefer.

## Decision summary

* The choice surfaces during `ralph run` preflight, before the managed block
  is appended, and only when stdin AND stdout are TTYs. Non-interactive runs
  (CI, unattended) keep today's behavior — append the block, never hang.
* Choosing to keep the existing policy persists as the existing byte-exact
  opt-out marker `<!-- ralph-workflow-policy: skip -->` appended to
  AGENTS.md. This reuses the SKIPPED preflight path, is visible in the repo,
  and never asks again.
* Either answer is one-time: accepting writes the managed block (markers now
  present), declining writes the opt-out marker (SKIPPED forever after).

## Trigger conditions (all must hold)

1. AGENTS.md exists in the workspace.
2. It contains NO Ralph markers: no `AGENTS_BLOCK_BEGIN`, no
   `AGENTS_BLOCK_END`, no `OPT_OUT_MARKER`. Any marker means the repo has
   already been bootstrapped or already decided.
3. Its content is *significant*: at least one markdown heading line
   (a line starting with `#`) OR at least 10 non-empty lines. Both
   thresholds are deterministic constants in
   `ralph/project_policy/markers.py`, matching the module's byte-exact
   style. No NLP, no fuzzy matching.
4. `sys.stdin.isatty()` and `sys.stdout.isatty()` are both true (via an
   injected seam, see below).

## Prompt wording

```
AGENTS.md already contains project instructions — this repo may already
have its own agent policy.

  • Keep your existing policy: Ralph won't touch AGENTS.md and will skip
    policy enforcement (writes an opt-out marker).
  • Use Ralph's managed policy: a good default if you're not confident in
    the existing setup — appends a managed block; your content is
    preserved byte-for-byte.

Add Ralph's managed policy block? [Y/n]
```

Default answer is Yes (append the block), so pressing Enter keeps current
behavior.

## Architecture

The preflight orchestrator (`ralph/project_policy/preflight.py`) is
deterministic by contract — no AI, no interaction. The interactive check
therefore lives in the CLI layer,
`ralph/project_policy/cli_integration.run_project_policy_readiness`,
between workspace construction and the call to
`run_policy_readiness_preflight`.

New pieces:

* `agents_md.has_significant_unmanaged_content(workspace) -> bool` — pure
  predicate implementing trigger conditions 1–3; testable with
  `MemoryWorkspace`.
* `agents_md.write_opt_out(workspace) -> list[str]` — appends
  `OPT_OUT_MARKER` (with surrounding newlines) to AGENTS.md, preserving
  prior bytes; returns the changed-file list, mirroring `bootstrap`.
* Two significance constants in `markers.py` (heading prefix is `#`;
  non-empty line threshold is 10).
* Injected seams on `run_project_policy_readiness`:
  `confirm_factory: Callable[[str], bool] | None = None` and
  `is_tty: Callable[[], bool] | None = None` (defaults: `typer.confirm`
  pattern as in `cleanup.py`, and the real isatty checks). Same DI style as
  the existing `emit_factory` / `workspace_factory` parameters.

Flow in `run_project_policy_readiness`:

1. Build workspace (existing).
2. If `has_significant_unmanaged_content(workspace)` and `is_tty()`:
   show the prompt. If the user declines, call `write_opt_out(workspace)`
   and emit one line naming the choice and the marker written.
3. Proceed to `run_policy_readiness_preflight` unchanged — a written
   opt-out marker yields SKIPPED via the existing path.

No changes to preflight.py, validators, remediation, or the cache.

## Error handling

* Prompt I/O failures (e.g. EOF on stdin despite isatty) are caught; the
  run proceeds with today's default behavior (append block). Interactivity
  must never block or crash a run.
* `write_opt_out` failures propagate like other workspace writes (the
  preflight already surfaces workspace errors).

## Testing

Unit (MemoryWorkspace):
* significance predicate: missing file → False; managed-block present →
  False; opt-out present → False; 9 non-empty lines, no heading → False;
  one heading only → True; 10 non-empty lines, no heading → True.
* `write_opt_out`: appends marker preserving prior bytes; result satisfies
  `is_opted_out`; idempotence not required (never called twice by design)
  but a second call must not corrupt the file.

Integration (`tests/project_policy/test_cli_integration_helpers.py` /
`test_run_integration.py` style, injected seams):
* significant content + TTY + decline → opt-out written, preflight returns
  SKIPPED, exit success, no managed block appended.
* significant content + TTY + accept → block appended, normal flow.
* significant content + no TTY → no prompt, block appended (today's
  behavior).
* insignificant content + TTY → no prompt.
* existing markers + TTY → no prompt.

## Out of scope

* A CLI flag / config option for the same choice (can layer on later).
* Prompting in `ralph init` or any other command.
* Any change to what "opted out" means downstream.
