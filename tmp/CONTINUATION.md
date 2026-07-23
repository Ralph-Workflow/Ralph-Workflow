# Continuation prompt — markdown artifact migration (wt-043-md-migration)

Paste everything below into a fresh Claude Code session started in
`/Volumes/Crucial X9/ext-Projects/Ralph-Workflow/wt-043-md-migration`.

---

You are continuing a large in-progress task: **ensure everything in the root
`PROMPT.md` (JSON→markdown artifact migration) is fully implemented** in the
Python package `ralph-workflow/`. Work as a software developer yourself,
orchestrating PARALLEL general-purpose subagents for implementation and
INDEPENDENT verification agents for review. Read root `PROMPT.md` and
`AGENTS.md` first. Non-negotiables: `make verify` must pass IN FULL at the end
(60s combined test budget, no lint/type bypasses, zero dead code, fabrication
guard on public markdown); make SMALL commits VERY OFTEN (one per completed
piece, conventional-commit subjects, NO AI attribution of any kind); after
each commit check `git rev-list --count HEAD..main` and rebase onto main if it
moved (it has been 0 so far); push to `origin wt-043-md-migration` after each
piece; never create branches; exit on the same branch.

## Git sync protocol (PERIODIC, user-mandated — do this after every piece)

After each committed piece: (1) `git fetch origin` and check
`git rev-list --count HEAD..main`; if main moved, `git rebase main`, resolve
conflicts favoring the migration's rewrites (main's changes are usually
module-split refactors — verify each side with `git show main:<file>`),
re-run focused tests, `git push --force-with-lease origin wt-043-md-migration`.
(2) FAST-FORWARD MAIN to this branch so other agents' worktrees receive our
work: `git -C "/Volumes/Crucial X9/ext-Projects/Ralph-Workflow" merge --ff-only wt-043-md-migration`
(main is checked out in that primary worktree; confirm its tree is clean
first with `git -C ... status --porcelain`). Do BOTH periodically — every
rebase of this branch onto main must be followed by fast-forwarding main to
the latest branch tip.

## State when the previous session ended (9 commits pushed, branch 9 ahead / 0 behind main)

Committed and pushed, in order:
1. `979191879 fix(mcp): register commit_cleanup markdown spec` (+ tests/mcp/test_md_commit_cleanup_spec.py)
2. `754929b85 feat(mcp): name markdown replacement for retired JSON tools` (_tool_bridge.py `_RETIRED_JSON_TOOL_REPLACEMENTS` + tests/test_tool_bridge_retired_json_tools.py)
3. `1b84dddfc test(mcp): drop stringified-arguments decode expectation` (deleted obsolete JSON-compensation test)
4. `98c6ec715 feat(mcp): add markdown draft staging tools` (`ralph_stage_md_artifact`, `ralph_get_md_draft`, `ralph_discard_md_draft`, `ralph_finalize_md_artifact`; new `ralph/mcp/artifacts/md_draft_io.py`; tests/test_md_artifact_staging.py)
5. `33cf3703f docs(skills): rewrite submit skills for markdown artifacts` (6 skills in `ralph/skills/content/`, version 2.0.0, catalogues deleted)
6. `2c83963ea docs: describe markdown artifact surface in reference docs` (sphinx docs, artifact-submission-contract.md, mcp/ARCHITECTURE.md)
7. `902c74b11 docs(mcp): rewrite format docs with validated markdown samples` (short format docs + 11 standalone samples in `format_docs/examples/`, materialized via `materialize_all_format_docs`, `tests/test_format_doc_examples.py` proves every example parses with zero errors; `.gitignore` negation added for `examples/plan.md` because `*PLAN.md` swallowed it)
8. `8b37e2235 refactor(prompts): migrate phase templates to markdown artifacts` (all templates in `ralph/prompts/templates/`, `template_variables.py` staging-tool refs, `canonical_submit.py` markdown fallback promotion `.agent/tmp/<type>.md`, `completion_signals.py` docstring)

Environment note: `questionary` had to be `pip install`ed into the pyenv
environment (declared in pyproject but wasn't installed) — if a fresh env,
re-check `python -c "import questionary"`.

## UPDATE at session end — BOTH remaining agents checkpointed and their work is COMMITTED

Two more commits landed after this file was first written (11 total, all pushed):
- `e6af8b1bd refactor(mcp): make plan markdown grammar JSON-free` — plan grammar
  is now fully JSON-free: `### [S-n]` step blocks with labeled field lines
  (`Type:`, `Files:` bullets, `Depends on: S-1`, `Satisfies: AC-01`, `Verify:`),
  `- [SC-1]`/`- [AC-01]`/`- [R-1]`/`- [V-1]` items with indented fields, closed
  key tables in new `markdown/_fields.py`, parity via canonical normalizer,
  stable never-renumbered IDs, step edits take markdown blocks. Full grammar
  spec + example document is in the plan-grammar agent's report (reproduce it
  from `format_docs/plan.md` reconciliation task below). 298 mcp tests green.
- `9bde786a3 refactor(pipeline): consume markdown artifacts downstream` —
  materialize handoff crash fixed (reads handoff/artifact md directly),
  handoffs.py reduced to HANDOFF_PATHS + handoff_path_for_artifact (renderers
  DELETED), commit phase + CLI read `.agent/artifacts/commit_message.md` via
  `load_phase_artifact(..., artifact_type="commit_message")` (frontmatter
  `type:` is the variant commit/skip — always pass the override), fan_out
  writes the parallel summary handoff itself, parallel_display reads
  authoritative markdown.

### KNOWN RED (fix first in the next session, before the big test wave)
1. `tests/test_format_doc_examples.py::test_shipped_example_validates_with_zero_errors[plan]`
   — `format_docs/examples/plan.md` + `format_docs/plan.md` still describe the
   OLD JSON-in-list-items grammar; rewrite both to the new grammar (use the
   example document from commit e6af8b1bd's test file
   `tests/mcp/test_md_plan_spec.py` as ground truth), keep the sample an
   opinionated exemplar, zero error diagnostics.
2. 8 CLI generate-commit tests asserting the old JSON flow — exact list and
   per-test fix guidance is in the "Downstream continuation" section below.
3. 8 plan tests in `tests/test_phases_commit_planning_fix.py` writing
   `.agent/artifacts/plan.json` — author minimal valid plan MARKDOWN docs
   (new grammar) and write `.agent/artifacts/plan.md` instead.
4. Skills `submit-plan-artifact.md`/`submit-plan-step-edits.md` + templates
   `planning*.jinja` worked examples still show the old plan JSON-item shape —
   reconcile to the new grammar; re-validate every embedded example.
5. `audit_repo_structure` flagged: `tests/test_md_artifact_staging.py` (two
   top-level classes) and a noqa in `tests/test_format_doc_examples.py` /
   `prompts/materialize.py` — check `python -m ralph.testing.audit_repo_structure`.

### Downstream continuation (verbatim guidance from the downstream agent)
- 8 CLI tests: test_cli_commands_1.py::test_generate_commit_msg_writes_commit_message_artifact
  (assert `.agent/artifacts/commit_message.md`, not `.agent/tmp/commit_message.json`);
  ::test_generate_commit_msg_extracts_commit_subject_from_markdown_wrapper (raw-string
  coercion path is gone — rewrite via md doc or the `.agent/tmp/commit_message.md`
  fallback promotion in `canonical_submit.promote_fallback_artifact`);
  ::test_start_commit_bridge_exposes_write_file_for_commit_session and the two
  prompt-mention tests (align expectations with `commit_message.jinja` markdown
  flow + `ralph_submit_md_artifact`); test_cli_commands_2.py::test_generate_commit_preserves_artifacts_when_commit_fails
  (md path); ::test_show_commit_msg_reads_artifact_without_staged_changes (write
  the md doc; txt fallback removed); ::test_generate_commit_msg_accepts_raw_commit_payload_written_by_agent
  (raw JSON acceptance gone — rewrite for markdown fallback promotion or delete
  if covered by promote_fallback_artifact tests).
- Traps: commit_message frontmatter values are UNQUOTED (`subject: fix(auth): x`);
  concurrent-agent worktree churn is over now, but always re-read files before
  editing; import-broken test files listed in the test-wave section remain.

## Subagents that WERE running (now checkpointed — kept for historical context)

Check `git status` first. If their work is present and complete, verify and
commit it; if absent or half-done, re-dispatch the agents with the prompts
summarized below.

**A. Downstream-consumption fixes agent** (owns: `ralph/prompts/materialize.py`
handoff-resolution + stale `.json` artifact paths at ~lines 958/968,
`ralph/mcp/artifacts/handoffs.py` (DELETE the JSON→md derivation layer:
`render_markdown_handoff`/`sync_markdown_handoff`/`ensure_markdown_handoff_from_artifact`
+ `_renderers`, fix docstring), `ralph/phases/commit.py` (rewire from
`.agent/tmp/commit_message.json` to the markdown artifact via
`load_phase_artifact`), `ralph/mcp/artifacts/commit_message.py` (remove dead
`write_commit_message_artifact` + stale constant), `ralph/prompts/commit/__init__.py`
(JSON tool refs), `ralph/display/parallel_display.py:2565`, `ralph/pipeline/fan_out.py:208`
callers). Known audit facts: `_resolve_agent_handoff` crashes with
JSONDecodeError on markdown plans (handoffs.py:145 json.loads); commit phase
gate can never see submitted markdown commit_message. TDD, typed tests, no
real I/O. Commit as its own piece when green.

**B. Plan-grammar redesign agent** (owns `ralph/mcp/artifacts/markdown/`
parser/spec/references, `specs/plan.py`, the edit handler in
`ralph/mcp/tools/md_artifact.py`, tool descriptions in `_specs_artifacts.py`,
tests/mcp/test_md_plan_spec.py + step-edit tests). Mission: plan step bodies /
Summary / Design / ACs are currently JSON blobs inside markdown list items
(`json.loads` in specs/plan.py ~lines 56-175) — replace with a native,
small, closed markdown sub-grammar (### [S-1] step blocks with labeled field
lines, `- [AC-1] ... (satisfied by: S-1)` items), NO JSON anywhere, keeping
FULL validation parity via the existing `normalize_plan_content` mapping
(required presence, ID shape/uniqueness, dangling refs, cycles, per-step
contracts, size caps, shell-invocation guard — hard errors; vocabulary —
warnings). Step-edit `replacement` param becomes a markdown block, round-trip
test required, truncated-doc diagnostics test required.

## Remaining work after A and B land (dispatch as parallel waves; every wave verified by INDEPENDENT agents)

1. **Plan-grammar reconciliation wave** (after B): update to the new grammar —
   `format_docs/plan.md` + `format_docs/examples/plan.md` (must pass
   tests/test_format_doc_examples.py), skills `submit-plan-artifact.md` +
   `submit-plan-step-edits.md` (they currently document per-section JSON field
   rules and a JSON step-edit example), templates `planning*.jinja` worked
   examples (re-render + re-validate all embedded examples), and the
   staging/verify flows if tool params changed.
2. **Test-migration wave — the big one** (~38 test files fail collection on
   removed symbols from `ralph.mcp.tools.artifact`, `ralph.mcp.tools.names`
   (`SUBMIT_ARTIFACT_TOOL`, `PLAN_DRAFT_WRITE_TOOLS`, `SUBMIT_PLAN_SECTION_TOOL`),
   and deleted `ralph.mcp.tools.plan_draft_edit`). Get the list via
   `python -m pytest tests/ --co -q 2>&1 | grep ^ERROR`. Split among 4-6
   parallel agents by area: (a) rollback/submit-ops tests → rewrite against
   `md_artifact` handlers + `canonical_submit`; (b) plan artifact/staging/
   draft/step-edit tests → new grammar + staging tools; (c) prompt/skill/
   format-doc content tests (test_prompt_types, test_prompts_materialize*,
   test_prompt_template_files, test_internal_skills_mcp_prompts,
   test_skill_instructions_round_trip, test_artifact_format_docs...) → new
   markdown expectations; (d) commit plumbing/CLI tests
   (test_cli_commit_command, test_generate_commit_canonical_path,
   test_commit_plumbing_uses_canonical_submit...) → markdown commit_message
   flow; (e) tool-spec consistency + misc (test_tool_spec_*_consistency,
   tests/mcp/test_tool_bridge_tool_specs_web_search, test_rfc013_db_close,
   test_submit_artifact_writes_receipt, smoke tests). Also known failing
   content tests: tests/test_prompts_developer.py (8 failures pinning removed
   plan tools), tests/test_prompts_commit.py, test_canonical_artifact_submit.py
   (20 failures pinning removed JSON contract),
   tests/test_mcp_tool_json_repair_boundary.py. RULE: rewrite tests to cover
   equivalent behavior on the markdown surface — do NOT delete coverage unless
   the covered behavior itself was deliberately removed (JSON repair, plan
   drafts JSON): document every deletion. Respect the 60s total test budget —
   prefer consolidating; no time.sleep/subprocess in unit tests.
3. **Dead-code cleanup wave** (zero-dead-code rule): retired names in
   `ralph/mcp/tools/_side_effects.py` REGISTRY (lines ~69-99),
   `ralph/mcp/transport/pi.py:48` TERMINAL_TOOLS retired entries,
   `ralph/mcp/explore/family_baseline.py:178,188`, orphaned
   `ralph/mcp/artifacts/plan/_draft_io.py` (write/finalize half; keep only the
   stale-draft clearing used by phases/execution.py:132-151 or migrate it to
   md drafts), `_normalize_artifact_payload` in tools/artifact.py (no
   callers), unused `_SUBMIT_ARTIFACT_DESCRIPTION` in `_spec_helpers.py`,
   `legacy_json.py` docstring refs, `policy_remediation_analysis_decision`
   format doc with no registered spec (either register a spec or document
   why), old JSON plan validation halves left unreferenced after the plan
   grammar lands. Each removal proven by grep + full test run.
4. **Holistic prompt review wave** (EXPLICIT user requirement): independent
   review agents read the ENTIRE agent-facing prompt surface AS ONE SYSTEM —
   all templates, all 6 skills, all format docs + examples, retry hints in
   `ralph/phases/required_artifacts.py`, tool descriptions in
   `_specs_artifacts.py`, the removed-tool error strings — checking: identical
   tool names everywhere, identical grammar descriptions, no contradictions,
   no leftover JSON vocabulary (payload/envelope/escape/enum), prompt-
   engineering quality (imperative, short, numbered flows, diagnostic-driven
   repair guidance, cheap-model friendly). Findings fixed + committed.
5. **Independent verification wave** (EXPLICIT user requirement: "verify
   EVERYTHING with independent agents"): for each earlier piece, a separate
   agent adversarially reviews the diff against PROMPT.md requirements (e.g.
   staging draft persistence/resumability, validator identity between
   verify/submit/finalize, retired-tool mapping completeness, format-doc
   claims vs spec code, template variable coverage). Also run
   `/code-review` style scrutiny on the full branch diff: `git diff main...HEAD`.
6. **Final gate**: `cd ralph-workflow && make verify` — must pass IN FULL.
   Then re-check PROMPT.md end-to-end (every "Consequences" bullet) and
   AGENTS.md hidden gates (docs/agents/verification.md audits: modules.rst,
   noqa allowlist, disallow_any_expr, audit_mcp_timeout, audit_resource_lifecycle,
   audit_test_policy — see memory `ralph-verify-gate-gotchas`). Fix everything
   surfaced; a red gate is yours regardless of cause.

## User requirements accumulated mid-session (ALL binding)

- Parallel agents for implementation AND independent verification agents; do
  not declare done until EVERYTHING passes.
- Small commits VERY often, piece by piece; push each; rebase onto main as it
  moves; fast-forward semantics, never leave work uncommitted at the end.
- Skills REPLACED with markdown variants, written with skill-creator
  discipline (trigger-oriented descriptions, tight structure), versions
  bumped (2.0.0 already done) to force update.
- All prompts get prompt-engineering treatment + independent review,
  holistically across the whole surface.
- EXAMPLE artifacts for all major types: standalone sample files REFERENCED
  by the format docs; samples must be (1) machine-validated in CI forever
  (tests/test_format_doc_examples.py) and (2) opinionated exemplars teaching
  good engineering (good plans: small verifiable steps, dependencies, AC↔step
  links, verification commands; model conventional commits; honest proofs) —
  readable by frontier AND cheap models.
- AGENTS.md commit rule says use `ralph --generate-commit`; the user
  explicitly demanded frequent per-piece commits, which overrode it this
  session (plain `git commit` with clean conventional subjects, hook-checked).
  Keep that approach unless the user says otherwise.

## Facts that save you time

- Tool surface (ralph/mcp/tools/names.py): `ralph_submit_md_artifact`,
  `ralph_verify_md_artifact`, `ralph_edit_md_plan_step`, `ralph_stage_md_artifact`,
  `ralph_get_md_draft`, `ralph_discard_md_draft`, `ralph_finalize_md_artifact`.
- Validator: `parse_and_validate(content, get_spec(type))` in
  `ralph/mcp/artifacts/markdown/_spec.py` — the ONLY gate, shared by
  verify/submit/finalize. Diagnostics carry line/section/rule_id/severity.
- Canonical write: `.agent/artifacts/<type>.md` + byte-identical handoff copy
  (`canonical_submit.py`). Drafts: `<artifact_dir>/.<type>.draft.md`.
- Legacy JSON on disk → clear rejection via `legacy_json.parse_or_reject`
  ("re-author it as markdown") — PROMPT-compliant.
- Proof matching is ID-based (`S-N`, unit ids, analysis `How To Fix` item ids)
  in `ralph/phases/execution.py:313-470`.
- 4 audit reports (MCP surface, validation parity, downstream, docs) were
  produced this session; their key findings are all reflected in the work
  list above — trust the list, re-audit only what you change.
- Traversal guard for commit_cleanup lives at execution time
  (`ralph/git/commit_cleanup.py:48`), not validation — don't "fix" that.
- Use the scratchpad dir for throwaway scripts, `tmp/` at repo root for repo
  temp files; never create markdown scratch files in repo root or docs/.
