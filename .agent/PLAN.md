## Summary

Implement the configurable orchestration layer described in docs/plans/2026-04-09-configurable-orchestration.md. The goal is to move orchestration policy decisions out of implicit Rust control-flow and into validated TOML configuration, while eliminating the legacy XSD/XML compatibility layer entirely. The current codebase has several concrete bugs: (1) the pipeline.toml sequence is wrong — it still uses `commit_message/final_validation/finalizing` instead of the `development_commit`/`review_commit` checkpoint model; (2) artifacts.toml uses invented decision-outcome vocabulary instead of the typed MCP `completed/partial/failed` status; (3) the fix drain incorrectly maps to the developer chain in ralph-workflow.toml instead of its own fixer chain; (4) analysis.rs still reads the legacy `fix_result.xml` file instead of using artifact state; (5) sibling drain inference remains in the resolution path; and (6) five XSD schema files still exist in prompts/xsd/ and must be deleted. The ralph-workflow-policy crate and phase TOML files already exist as a skeleton; this plan fills the gaps between current state and the Section 1 target flow across all nine implementation phases.

### Scope

-  **3** Update .agent/pipeline.toml (fix sequence + decision routes), .agent/artifacts.toml (typed MCP status vocabulary), and .agent/ralph-workflow.toml (add fixer chain, fix drain binding) to match Section 1 target flow (policy-toml-updates)
-  **6** Update ralph-workflow-policy config types: PipelineConfig (phase_documents, cycle_accounting fields), ArtifactsConfig (development_analysis/review_analysis/development_commit/review_commit sections with status_field + allowed_statuses), phase TOML files in ralph-workflow-policy/phases/ (policy-crate-types)
-  **2** Remove sibling drain inference tiers 2–3 from agents/fallback.rs and ensure unified config resolve_agent_drains_checked() fails fast on missing bindings when forbid_sibling_drain_inference=true (drain-resolution)
-  **3** Rename invoke_analysis_agent and invoke_fix_analysis_agent to explicit development_analysis/review_analysis functions; remove legacy fix_result.xml read; update DevelopmentAnalysisDecision and ReviewAnalysisDecision enums to completed/partial/failed vocabulary; update all match sites (analysis-boundary)
-  **2** Update capability_mapping.rs to separate Analysis DrainClass from Planning DrainClass; ensure session/mod.rs preserves drain identity across retries, continuations, and resume (drain-identity)
-  **2** Strengthen reducer/boundary/json_artifact.rs to enforce run, drain, attempt, artifact_type identity checks; convert completion detection in agent.rs, planning.rs, run_fix.rs from legacy file-presence to artifact submission state (artifact-acceptance)
-  **5** Delete all 5 XSD files from ralph-workflow/src/prompts/xsd/; remove schema_contract_tests.rs XSD tests; remove any dual-mode XML fallback branches from boundary modules; delete all code that only exists to serve the legacy extraction path (legacy-deletion)
-  **2** Add per-drain-scope invariant tests to reducer/orchestration/rules.rs; add regression tests for: planning wrong-chain selection, stale artifact rejection, analysis wrong-next-phase routing, parallel worker scope escape (orchestration-hardening)

### Skills & MCP Recommendations

- **Skill:** test-driven-development — Every new behavior must start with a failing test per project TDD mandate
- **Skill:** simplify — After all changes, run the project simplify review for quality

## Implementation Steps

### Step 1 (file-change) [critical]:  Update .agent/pipeline.toml to match Section 1 target flow

**Target Files:**
- `.agent/pipeline.toml` (modify)

**Location:** Entire file — replace the content to match the Section 1 flow declared in the plan document

**Rationale:** Current pipeline.toml has wrong phase sequence (commit_message/final_validation/finalizing instead of development_commit/review_commit), decision routes expose too many alternatives (development_analysis routes to planning and review directly instead of only to development or development_commit), and the file is missing the phase_documents section, cycle_accounting section, and phase_side_effects entries required by PipelineConfig.

Replace the entire file with a version that matches the normative defaults specified in Section 7 of the plan document. The new file must:

1. Add`phase_documents`array at top level referencing the five phase TOML files in ralph-workflow-policy/phases/: planning.toml, development.toml, review.toml, development_commit.toml, review_commit.toml
2. Change`[top_level_phases]`to use`default_sequence = ["planning", "development", "development_commit", "review", "review_commit"]`— note development_commit and review_commit replace old commit_message/final_validation/finalizing
3. Add`[cycle_accounting]`section with:`development_counter_increment_phase = "development_commit"`,`development_counter_increment_when = "after_development_cycle_completion"`,`review_counter_increment_phase = "review_commit"`,`review_counter_increment_when = "after_review_cycle_completion"`
4. Keep`[embedded_decision_points]`but change key names to match new vocabulary:`development = ["development_analysis"]`,`review = ["fix", "review_analysis"]`
5. Fix`[decision_routes]`to binary vocabulary only:`development_analysis = ["development", "development_commit"]`and`review_analysis = ["fix", "review_commit"]`— remove planning, review, commit_message as direct analysis destinations
6. Add`[phase_side_effects]`entries for submit_development_commit_artifact, apply_development_commit, submit_review_commit_artifact, apply_review_commit as shown in the plan's pipeline.toml example
7. Keep`[budgets]`,`[artifact_acceptance]`,`[validation]`, and`[parallel_execution]`sections unchanged
8. Remove the`recovery_phase = "awaiting_dev_fix"`line (or verify it is still needed — if the runtime requires it, keep it but document it as a runtime mechanics field, not a policy-visible phase)

### Step 2 (file-change) [critical]:  Update .agent/artifacts.toml to typed MCP status vocabulary

**Target Files:**
- `.agent/artifacts.toml` (modify)

**Location:** Entire file — replace with version matching the Section 7 artifacts.toml example

**Rationale:** Current artifacts.toml has [analysis] with invented decision-outcome vocabulary (needs_more_work, needs_replanning, ready_for_review, ready_to_commit, needs_another_review) that is not aligned with the MCP typed status contract. The plan mandates replacing this with the binary MCP status field (completed/partial/failed) and splitting [analysis] into separate [development_analysis] and [review_analysis] entries. Also missing: [development_commit] and [review_commit] entries.

Replace the entire file content. The new file must have entries matching the Section 7 artifacts.toml example:

1. Keep`[planning]`with artifact_type="plan" and required_sections as-is
2. Keep`[development]`with artifact_type="development_result"; optionally add`continuation_template`field reference
3. Remove the old`[analysis]`section entirely — it is replaced by two separate entries
4. Add`[development_analysis]`with:`artifact_type = "analysis_decision"`,`submission_mode = "mcp_artifact"`,`status_field = "status"`,`allowed_statuses = ["completed", "partial", "failed"]`
5. Add`[review_analysis]`with the same fields as development_analysis (identical contract, separate lookup key)
6. Keep`[review]`with artifact_type="issues"; keep`[fix]`with artifact_type="fix_result"
7. Replace old`[commit]`with two entries:`[development_commit]`and`[review_commit]`— both with artifact_type="commit_message", submission_mode="mcp_artifact", and artifact_output_path=".agent/tmp/commit_message.json"
8. Remove the old generic`[commit]`entry

### Step 3 (file-change) [critical]:  Fix .agent/ralph-workflow.toml drain binding: add fixer chain, update fix drain

**Target Files:**
- `.agent/ralph-workflow.toml` (modify)

**Location:** [agent_chains] section and [agent_drains] section

**Rationale:** Currently fix = "developer" in [agent_drains], which incorrectly routes the fix drain through the developer chain. Fix is write-capable but semantically distinct from development: it addresses reviewer findings. The plan requires each drain to have an explicit dedicated chain. The [agent_drains] must also add analysis = "reviewer" explicitly (already present) and ensure all six drains are bound.

1. In`[agent_chains]`, add a`fixer`chain entry. Copy the agent list from the`developer`chain as the starting point (same write-capable agents), then adjust as needed for the fix role. Example:`fixer = ["claude", "ccs/mm", "opencode/openai/gpt-5.1-codex-mini"]`
2. In`[agent_drains]`, change`fix = "developer"`to`fix = "fixer"`
3. Verify all six drains are present and explicitly bound: planning="plan", development="developer", review="reviewer", fix="fixer", commit="commit", analysis="reviewer"
4. Run verification to confirm drain resolution no longer reports missing or inferred bindings

### Step 4 (file-change) [high]:  Update ralph-workflow-policy config types to match new pipeline.toml shape

**Target Files:**
- `ralph-workflow-policy/src/config/mod.rs` (modify)

**Location:** PipelineConfig struct, TopLevelPhasesConfig, CycleAccountingConfig, DecisionRoutesConfig, ArtifactsConfig

**Rationale:** The policy crate types must match the TOML shape changes made in steps 1 and 2. Specifically: TopLevelPhasesConfig must have default_sequence as the canonical field name; DecisionRoutesConfig must reflect the binary route model (only two destinations per analysis point); ArtifactsConfig must support the new [development_analysis], [review_analysis], [development_commit], [review_commit] entries with status_field and allowed_statuses fields. Any type that does not match the updated TOML will fail deserialization at startup.

1. Read`ralph-workflow-policy/src/config/mod.rs`fully before editing to understand all existing types
2. In`TopLevelPhasesConfig`: ensure`default_sequence`is the primary field (not just`sequence`); if both exist via serde aliases, that is acceptable but the canonical field used in pipeline.toml must be`default_sequence`
3. In`CycleAccountingConfig`: ensure the struct has all four fields:`development_counter_increment_phase`,`development_counter_increment_when`,`review_counter_increment_phase`,`review_counter_increment_when`
4. In`DecisionRoutesConfig`: rename`fix_analysis`field to`review_analysis`to match the new binary route vocabulary (if currently named differently)
5. Add or update`ArtifactEntryConfig`(or equivalent per-drain artifact struct) to support:`status_field: Option<String>`and`allowed_statuses: Vec<String>`in addition to the existing artifact_type/submission_mode fields
6. Ensure`ArtifactsConfig`has named fields (or a HashMap) that can hold development_analysis, review_analysis, development_commit, review_commit entries in addition to the existing ones
7. Update validation in`ralph-workflow/src/config/validation/policy_files.rs`to validate the new fields and reject configs where status_field is set but allowed_statuses is empty
8. Write tests in`ralph-workflow-policy/src/config/`(or update existing io_tests) that deserialize the updated pipeline.toml and artifacts.toml and assert the correct values parse

**Depends on:** Step 1, Step 2


### Step 5 (file-change) [high]:  Remove sibling drain inference tiers 2 and 3 from agents/fallback.rs and drain resolution

**Target Files:**
- `ralph-workflow/src/agents/fallback.rs` (modify)
- `ralph-workflow/src/config/unified/types.rs` (modify)

**Location:** In fallback.rs: the FallbackConfig or chain resolution code that says "Analysis defaults to developer chain if empty", "Fix defaults to reviewer chain if empty", "Planning defaults to analysis then developer if empty", "Commit defaults to reviewer or fix chain". In unified/types.rs: resolve_agent_drains_checked() method

**Rationale:** The plan's Section 3A describes the three-tier fallback as the root cause of wrong-chain routing bugs. Tier 2 (sibling drain inheritance) and tier 3 (legacy role-family chain names like "developer" and "reviewer" backing multiple drains) must be removed so that a missing drain binding produces an immediate validation error instead of silently inheriting from a sibling. The OrchestrationConfig already has forbid_sibling_drain_inference=true; the code must enforce this by making the resolution function return ResolveDrainError instead of falling through.

Read both files completely before editing. Then:

1. In`fallback.rs`: find and remove all code that falls back to a sibling or role-family chain when a drain's chain is not explicitly set. Any such fallback that fires when`forbid_sibling_drain_inference = true`must be converted to return a`ResolveDrainError::ForbiddenImplicitInference`(or equivalent existing variant) instead
2. In`unified/types.rs`: in the drain resolution path, ensure that when`OrchestrationConfig::forbid_sibling_drain_inference`is true (which is the default), missing drain bindings produce a hard error surfaced as a field-precise configuration diagnostic. The error must name the drain, the file (agents.toml / ralph-workflow.toml), and the required field
3. Write a unit test in`ralph-workflow/src/config/unified/io_tests/validation_rules.rs`(or a new test file in that directory) that: (a) creates a config with fix drain missing, (b) loads it with forbid_sibling_drain_inference=true, (c) asserts the result is an error naming the fix drain — not a silently resolved developer chain
4. Also test that a config with all six drains explicitly bound resolves cleanly

**Depends on:** Step 3


### Step 6 (file-change) [high]:  Update DevelopmentAnalysisDecision and ReviewAnalysisDecision to completed/partial/failed vocabulary

**Target Files:**
- `ralph-workflow/src/reducer/state/pipeline/phase_fields.rs` (modify)

**Location:** DevelopmentAnalysisDecision and ReviewAnalysisDecision enum definitions and all match sites in the codebase

**Rationale:** The plan mandates typed MCP result schemas using a top-level status enum with values completed, partial, failed. The current enums use an invented vocabulary (needs_more_work, needs_replanning, ready_for_review, ready_to_commit, needs_another_review) that does not correspond to actual MCP schema fields. All match arms on these enums must be updated. This is required before the analysis boundary can be updated to produce the correct decision variants.

First read`ralph-workflow/src/reducer/state/pipeline/phase_fields.rs`and locate DevelopmentAnalysisDecision and ReviewAnalysisDecision. Then:

1. Replace`DevelopmentAnalysisDecision`variants with:`Completed`(development iteration done, route to development_commit),`Partial`(more development work needed, route back to development),`Failed`(more development work needed, route back to development). Note: Partial and Failed both route to development — the distinction is the artifact status reported by the agent, not a different routing destination.
2. Replace`ReviewAnalysisDecision`variants with:`Completed`(review iteration done, route to review_commit),`Partial`(more fix work needed, route back to fix),`Failed`(more fix work needed, route back to fix)
3. Run`cargo xtask lsp-forbidden-allow-expect`locally or via remote build to find all match sites that need updating — these will produce exhaustive match errors
4. Update every match site in reducer/state_reduction/, reducer/orchestration/rules.rs, and any boundary modules to use the new variant names
5. Write a test that asserts Partial and Failed both route to the development (or fix) drain, and Completed routes to development_commit (or review_commit)

### Step 7 (file-change) [high]:  Update analysis.rs boundary to remove fix_result.xml read and use explicit decision point names

**Target Files:**
- `ralph-workflow/src/reducer/boundary/analysis.rs` (modify)

**Location:** invoke_analysis_agent() and invoke_fix_analysis_agent() functions; any read of fix_result.xml legacy path

**Rationale:** analysis.rs currently reads .agent/tmp/fix_result.xml (a legacy XML file) to provide context for the fix analysis agent. After XSD/XML removal, no boundary module may read XML files. The fix_result artifact context must come from the accepted MCP artifact state. The function names invoke_analysis_agent and invoke_fix_analysis_agent should be renamed to invoke_development_analysis_agent and invoke_review_analysis_agent to make the explicit decision point names visible in the code.

1. Read the full`ralph-workflow/src/reducer/boundary/analysis.rs`file before editing
2. Rename`invoke_analysis_agent`to`invoke_development_analysis_agent`— update all callers in reducer/orchestration/ and state_reduction/
3. Rename`invoke_fix_analysis_agent`to`invoke_review_analysis_agent`— update all callers
4. Remove the call to`read_fix_result_content()`(which reads .agent/tmp/fix_result.xml). Replace it with reading the accepted fix artifact from MCP artifact state. If the artifact state does not yet have a clean accessor, use the artifact path from artifacts.toml (.agent/tmp/fix_result.json if it exists) and read that JSON file instead — do NOT read any .xml file
5. Update the prompt assembly to not reference XML-specific terminology (fix_result.xml path, XSD validation results)
6. Map the agent's analysis response to the new`DevelopmentAnalysisDecision`and`ReviewAnalysisDecision`variants (completed/partial/failed) defined in step 6
7. Write unit tests that confirm: (a) no .xml path is referenced, (b) the decision outcome mapping is correct for each status value

**Depends on:** Step 6


### Step 8 (file-change) [high]:  Update reducer/orchestration/rules.rs decision routing for binary vocabulary

**Target Files:**
- `ralph-workflow/src/reducer/orchestration/rules.rs` (modify)

**Location:** DrainRule entries for the Analysis drain — specifically the analysis_routes field or equivalent that maps AnalysisDecision variants to next drain

**Rationale:** The rules.rs file declares which drains analysis may route to. With the new vocabulary (completed routes to development_commit or review_commit; partial/failed loop back to development or fix), the routing table must be updated. The old many-destination routing (planning, review, commit_message as direct analysis destinations) must be removed — analysis can only route to the immediately next binary destination.

1. Read`ralph-workflow/src/reducer/orchestration/rules.rs`fully before editing
2. Update the development analysis routing rule:`DevelopmentAnalysisDecision::Completed`routes to`AgentDrain::Commit`(via development_commit);`Partial`and`Failed`route to`AgentDrain::Development`
3. Update the review analysis routing rule:`ReviewAnalysisDecision::Completed`routes to`AgentDrain::Commit`(via review_commit);`Partial`and`Failed`route to`AgentDrain::Fix`
4. Remove any rule that allows analysis to route directly to planning, review, or commit_message — those transitions are now mediated only through the commit phase's post-commit routes
5. Add invariant tests that assert: no analysis decision outcome produces a direct transition to planning; no analysis decision outcome produces a direct transition to review

**Depends on:** Step 6


### Step 9 (file-change) [high]:  Fix DrainClass mapping in capability_mapping.rs — separate Planning from Analysis

**Target Files:**
- `ralph-workflow/src/mcp_server/capability_mapping.rs` (modify)

**Location:** drain_class_for_session() function — the line that maps Planning and Analysis both to DrainClass::Planning

**Rationale:** The plan's Section 3B identifies that Planning and Analysis drains are collapsed into the same DrainClass::Planning. This creates ambiguous identity: a session started for planning can be identity-equivalent to an analysis session despite having different capability and write semantics. Analysis must have its own DrainClass so the runtime can distinguish analysis sessions from planning sessions.

1. Read`ralph-workflow/src/mcp_server/capability_mapping.rs`fully
2. Check if`DrainClass`enum has a dedicated Analysis variant. If not, add`DrainClass::Analysis`to the enum (which lives in mcp-server crate or ralph-workflow)
3. In`drain_class_for_session()`, change the Planning + Analysis combined match arm to separate arms:`AgentDrain::Planning`maps to`DrainClass::Planning`;`AgentDrain::Analysis`maps to`DrainClass::Analysis`(or the appropriate read-only analysis class)
4. Also verify`drain_to_policy_mode()`and`drain_to_access_mode()`correctly map Planning as ReadOnly and Analysis as ReadOnly (analysis is read-only)
5. Write a test asserting Planning and Analysis produce different DrainClass values

### Step 10 (file-change) [medium]:  Verify and fix drain identity preservation in session/mod.rs through continuations and retries

**Target Files:**
- `ralph-workflow/src/agents/session/mod.rs` (modify)

**Location:** Session resume, continuation, and retry code paths that re-derive SessionDrain or DrainClass

**Rationale:** The plan requires that drain identity survive normalization, retries, continuations, and resume. If the session module re-derives the drain from a compatibility adapter or role-family mapping rather than preserving the original drain, continuations may drift into the wrong drain. This must be explicitly verified and fixed.

1. Read`ralph-workflow/src/agents/session/mod.rs`fully to identify where SessionDrain is set on continuation/retry/resume
2. Search for any place where a session's drain is re-computed or overwritten during retry/continuation logic. It must come from the persisted drain identity, not re-derived from role or chain name
3. If any path allows drain normalization to change the drain after initial session creation, fix it to use the original drain
4. Add a test in`ralph-workflow/src/agents/session/tests/`that: creates a session with drain=Fix, performs a continuation, and asserts the resulting session still has drain=Fix (not Developer or Reviewer)

### Step 11 (file-change) [medium]:  Update phase TOML files in ralph-workflow-policy/phases/ to match target flow

**Target Files:**
- `ralph-workflow-policy/phases/planning.toml` (modify)
- `ralph-workflow-policy/phases/development.toml` (modify)
- `ralph-workflow-policy/phases/review.toml` (modify)
- `ralph-workflow-policy/phases/development_commit.toml` (modify)
- `ralph-workflow-policy/phases/review_commit.toml` (modify)

**Location:** All five phase TOML files — verify they match the exact structure from the plan's Section 7 phase examples

**Rationale:** The phase TOML files exist as skeletons but must be verified to match the target structure exactly. In particular: development.toml must use embedded_decision_points = ["development_analysis"] (not just "analysis"); transitions must use status = "completed"/"partial"/"failed" (not old vocabulary); review.toml must use subflow_template_keys with review_analysis key; post_commit_routes must route to planning/review for development and review/complete for review based on budget comparison.

Read each phase TOML file and compare against the Section 7 examples in the plan document. The key correctness checks are:

1. `planning.toml`: phase_id="planning", drain="planning", next=["development"]. Verify no extra fields.
2. `development.toml`: embedded_decision_points=["development_analysis"] (not ["analysis"]); transition when status="partial" routes next=["development"]; transition when status="failed" routes next=["development"]; transition when status="completed" routes next=["development_commit"]; post_commit_route less_than_budget routes to planning; post_commit_route budget_exhausted routes to review
3. `review.toml`: subflow=["fix","review_analysis"] (not ["fix","analysis"]); transition when status="completed" routes next=["review_commit"]; post_commit_route budget_exhausted routes to "complete" (not a fabricated built-in phase)
4. `development_commit.toml`: drain="commit"; side_effects reference submit_development_commit_artifact and apply_development_commit; artifact path=".agent/tmp/commit_message.json"
5. `review_commit.toml`: drain="commit"; side_effects reference submit_review_commit_artifact and apply_review_commit
6. Deserialize each file against the PhaseDefinition type in the policy crate to confirm it parses without error

**Depends on:** Step 4


### Step 12 (file-change) [medium]:  Strengthen artifact acceptance in reducer/boundary/json_artifact.rs

**Target Files:**
- `ralph-workflow/src/reducer/boundary/json_artifact.rs` (modify)

**Location:** Artifact acceptance validation — where artifacts are read from filesystem or MCP and accepted as valid for the current phase

**Rationale:** The plan requires artifact acceptance to check run identity, drain identity, attempt identity, and artifact type — not just whether a file exists. Currently artifact presence may be accepted without verifying it matches the current run/drain/attempt. Stale or wrongly-tagged artifacts must be rejected with a clear error. This is Phase 6 of the plan ("Reducer-visible artifact acceptance identity").

1. Read`ralph-workflow/src/reducer/boundary/json_artifact.rs`fully
2. Add artifact identity struct or extend the existing artifact record with: run_id, drain, attempt_number, artifact_type fields
3. When reading an accepted artifact from disk, verify: artifact.run_id == current_run_id; artifact.drain == current_drain; artifact.artifact_type matches the expected type from artifacts.toml for the current phase
4. If artifact_acceptance config has`require_current_run_identity = true`(which it does), reject artifacts from previous runs with a diagnostic naming the stale run_id
5. If artifact_acceptance config has`require_current_drain_identity = true`, reject artifacts whose recorded drain does not match the expected drain for this phase
6. Write tests: (a) stale run artifact is rejected; (b) wrong-drain artifact is rejected; (c) correct artifact is accepted; (d) missing artifact returns the appropriate not-yet-submitted state

### Step 13 (file-change) [medium]:  Convert completion detection from legacy file-presence to artifact submission state

**Target Files:**
- `ralph-workflow/src/reducer/boundary/agent.rs` (modify)
- `ralph-workflow/src/reducer/boundary/planning.rs` (modify)
- `ralph-workflow/src/reducer/boundary/run_fix.rs` (modify)

**Location:** Completion detection logic in each boundary module — wherever code checks for file existence to determine if a phase has completed

**Rationale:** The plan's Phase 7 requires that completion detection no longer depends on legacy temp-file paths. If any boundary module checks for the presence of a file like plan.xml, development_result.xml, fix_result.xml, commit-message.txt, or similar legacy artifacts to determine completion, that check must be replaced with a check against the accepted artifact submission state from json_artifact acceptance (step 12).

1. Read each of the three boundary files fully, searching for file existence checks (e.g.,`artifact_paths::plan_output_path()`,`artifact_paths::development_result_path()`, path checks against .xml or legacy .txt files)
2. For each such check, replace it with: query the artifact acceptance state for the current drain/phase from the reducer state, checking whether a valid artifact has been submitted and accepted for the current run
3. Remove any retry cleanup logic that deletes or resets legacy output files — retry state should be based on artifact submission state, not file cleanup
4. Ensure the "not yet submitted" state correctly drives the agent loop to invoke the agent again rather than treating absent artifact as completion
5. Write tests for each boundary: verify that when no artifact is submitted, the phase is not considered complete; verify that when the correct artifact is submitted, the phase transitions correctly

**Depends on:** Step 12


### Step 14 (file-change) [high]:  Delete XSD files and remove all code that references them

**Target Files:**
- `ralph-workflow/src/prompts/xsd/commit_message.xsd` (delete)
- `ralph-workflow/src/prompts/xsd/development_result.xsd` (delete)
- `ralph-workflow/src/prompts/xsd/fix_result.xsd` (delete)
- `ralph-workflow/src/prompts/xsd/issues.xsd` (delete)
- `ralph-workflow/src/prompts/xsd/plan.xsd` (delete)
- `ralph-workflow/src/prompts/schema_contract_tests.rs` (modify)
- `ralph-workflow/src/prompts/syntax_validation.rs` (modify)
- `ralph-workflow/src/prompts/mod.rs` (modify)

**Location:** All XSD files in prompts/xsd/, all code that imports or references these files, schema_contract_tests.rs, syntax_validation.rs

**Rationale:** The plan's Goal 3 and Section 10 mandate full removal of the XSD schema infrastructure. The five XSD files define legacy structured-output contracts that predate MCP. After steps 7 and 13 ensure analysis.rs and boundary modules no longer read XML files, the XSD files themselves can be deleted. Any tests that validate XSD schema contracts must be deleted or rewritten to test the MCP artifact contract instead.

1. Before deleting, search the entire codebase for all imports of the XSD files:`cargo xtask`grep for "xsd", ".xsd", "schema_contract", "xsd_validation" — identify every reference
2. Read`ralph-workflow/src/prompts/schema_contract_tests.rs`fully. If it only tests XSD validation, delete the entire file. If it also tests MCP artifact contracts, keep only the MCP-relevant tests.
3. Read`ralph-workflow/src/prompts/syntax_validation.rs`. If it validates XSD syntax, remove the XSD-specific validation logic. If it validates template syntax (double-curly), keep that part.
4. Check`ralph-workflow/src/prompts/mod.rs`for any`mod schema_contract_tests;`or similar XSD-related module declarations and remove them
5. Delete all five XSD files only after all references are removed (deletion without removing references will fail compilation)
6. Run`cargo xtask verify`(on rw-build-server) to confirm no compilation errors after deletion

**Depends on:** Step 7, Step 13


### Step 15 (file-change) [medium]:  Add drain-scoped invariant tests to orchestration/rules.rs and orchestration/tests.rs

**Target Files:**
- `ralph-workflow/src/reducer/orchestration/rules.rs` (modify)
- `ralph-workflow/src/reducer/orchestration/tests.rs` (modify)
- `ralph-workflow/src/reducer/orchestration/io_tests/` (modify)

**Location:** rules.rs: DrainRule entries and any associated test assertions. tests.rs: invariant test functions.

**Rationale:** Phase 9 of the plan requires that every orchestration rule declares which drains it may fire in, whether it preserves drain identity or performs a validated transition, and has a focused invariant test. The rule table is the normative reference for the orchestrator; if it lacks test coverage, orchestration bugs can regress silently.

Add the following regression test cases (failing tests first per TDD mandate):

1. `test_planning_never_routes_to_wrong_chain`: load a config where developer chain and plan chain are distinct; assert planning drain resolves to plan chain, not developer chain
2. `test_stale_artifact_rejected`: create an artifact with run_id != current_run_id; assert it is rejected by artifact acceptance with the correct error variant
3. `test_development_analysis_routes_only_to_development_or_commit`: assert that DevelopmentAnalysisDecision::Completed routes to Commit drain and DevelopmentAnalysisDecision::Partial/Failed route to Development drain — and never to Planning, Review, Fix
4. `test_review_analysis_routes_only_to_fix_or_review_commit`: assert ReviewAnalysisDecision::Completed routes to Commit drain and Partial/Failed route to Fix drain
5. `test_parallel_worker_cannot_escape_namespace`: assert that a worker with a declared artifact namespace cannot submit an artifact with a different namespace
6. `test_continuation_preserves_drain_identity`: assert that a continuation event for Fix drain does not change the drain to Developer or Reviewer
7. Ensure every DrainRule entry in rules.rs has at least one associated test function with a comment citing which orchestration invariant it covers

**Depends on:** Step 8, Step 12


### Step 16 (file-change) [medium]:  Remove dead code from analysis decision vocabulary and legacy XML paths

**Target Files:**
- `ralph-workflow/src/reducer/state/pipeline/phase_fields.rs` (modify)
- `ralph-workflow/src/reducer/state_reduction/awaiting_dev_fix/` (modify)
- `ralph-workflow/src/files/result_extraction/` (modify)

**Location:** Any dead enum variants from old analysis vocabulary, any legacy awaiting_dev_fix state that only exists for old XML recovery flow, any result_extraction code that is XML-specific

**Rationale:** Zero dead code policy. After steps 6-14, there will be dead enum variants (old analysis decision names), dead match arms, and possibly the entire awaiting_dev_fix state machine path if it only exists for the legacy XML completion flow. All dead code must be found and deleted — not just commented out, not wrapped in #[allow(dead_code)].

1. After step 6, search for any remaining uses of the old analysis decision variant names (needs_more_work, needs_replanning, ready_for_review, ready_to_commit, needs_another_review) — there should be none; if found, fix them
2. Run`cargo xtask lsp-forbidden-allow-expect`to surface any #[allow(dead_code)] that was added (forbidden) or any items that the compiler detects as unreachable
3. Read`ralph-workflow/src/reducer/state_reduction/awaiting_dev_fix/`to determine if this module still serves a purpose after completion detection is artifact-based. If it only exists for legacy XML completion recovery, delete it. If it has a legitimate role in the new artifact-based flow, document that role clearly.
4. Read`ralph-workflow/src/files/result_extraction/`to determine if any of this code is XML-specific. Delete any XML extraction helpers that no longer have callers after steps 7 and 13.
5. After deletions, run cargo build to confirm zero compilation errors

**Depends on:** Step 6, Step 13, Step 14


### Step 17 (file-change) [low]:  Update documentation to reflect MCP-only orchestration flow

**Target Files:**
- `docs/architecture/event-loop-and-reducers.md` (modify)
- `docs/code-style/architecture.md` (modify)

**Location:** Any section that describes legacy XML output files, XSD validation, or dual-mode completion detection

**Rationale:** The plan's Section 10 requires removing legacy structured-output terminology from docs and plans. After the runtime changes land, architecture documentation that still describes the XML flow or XSD validation would mislead contributors. Documentation must match the implemented behavior.

1. Read both docs files to identify sentences that mention XSD, XML, legacy output files, structured output, or dual-mode completion
2. Replace those descriptions with the MCP artifact flow: agents submit typed JSON artifacts via MCP, completion is detected by artifact acceptance state, analysis produces a typed status decision (completed/partial/failed)
3. Update any phase flow diagrams or tables that show commit_message/final_validation/finalizing to show development_commit/review_commit instead
4. Do not add new documentation — only update existing documentation to match the new behavior

**Depends on:** Step 14


## Critical Files

### Primary Files

- `.agent/pipeline.toml` (modify) - Full rewrite ~60 lines — add phase_documents, cycle_accounting, fix sequence and decision routes
- `.agent/artifacts.toml` (modify) - Full rewrite ~45 lines — split analysis into development_analysis/review_analysis, add development_commit/review_commit, fix status vocabulary
- `.agent/ralph-workflow.toml` (modify) - ~8 lines — add fixer chain, change fix drain binding
- `ralph-workflow-policy/src/config/mod.rs` (modify) - ~40 lines — ArtifactEntryConfig new fields, DecisionRoutesConfig rename, TopLevelPhasesConfig field name
- `ralph-workflow/src/agents/fallback.rs` (modify) - ~50 lines — remove sibling inference tiers 2 and 3
- `ralph-workflow/src/reducer/state/pipeline/phase_fields.rs` (modify) - ~30 lines — replace multi-outcome decision enums with completed/partial/failed
- `ralph-workflow/src/reducer/boundary/analysis.rs` (modify) - ~60 lines — rename functions, remove fix_result.xml read, update decision mapping
- `ralph-workflow/src/reducer/orchestration/rules.rs` (modify) - ~50 lines — update routing table, add invariant tests
- `ralph-workflow/src/mcp_server/capability_mapping.rs` (modify) - ~15 lines — separate Analysis from Planning DrainClass
- `ralph-workflow/src/reducer/boundary/json_artifact.rs` (modify) - ~80 lines — add identity fields, validation checks
- `ralph-workflow/src/reducer/boundary/agent.rs` (modify) - ~40 lines — artifact-based completion detection
- `ralph-workflow/src/reducer/boundary/planning.rs` (modify) - ~30 lines — artifact-based completion detection
- `ralph-workflow/src/reducer/boundary/run_fix.rs` (modify) - ~30 lines — artifact-based completion detection, drain identity
- `ralph-workflow/src/prompts/xsd/commit_message.xsd` (delete) - Full deletion
- `ralph-workflow/src/prompts/xsd/development_result.xsd` (delete) - Full deletion
- `ralph-workflow/src/prompts/xsd/fix_result.xsd` (delete) - Full deletion
- `ralph-workflow/src/prompts/xsd/issues.xsd` (delete) - Full deletion
- `ralph-workflow/src/prompts/xsd/plan.xsd` (delete) - Full deletion
- `ralph-workflow/src/prompts/schema_contract_tests.rs` (modify) - Delete XSD tests, keep MCP artifact contract tests if any
- `ralph-workflow-policy/phases/development.toml` (modify) - ~10 lines — rename analysis → development_analysis, verify transition status values
- `ralph-workflow-policy/phases/review.toml` (modify) - ~10 lines — rename fix_analysis → review_analysis, verify post-commit routes
### Reference Files

- `docs/plans/2026-04-09-configurable-orchestration.md` - Normative specification — Section 7 has the target TOML examples, Section 13 has the implementation order
- `ralph-workflow-policy/src/config/drain.rs` - OrchestrationConfig and ResolveDrainError definitions — already has correct flags, reference for error variant names
- `ralph-workflow/src/reducer/orchestration/rules.rs` - Existing rule table structure — reference for how to add new routing invariants
- `docs/agents/verification.md` - Verification commands — cargo xtask verify is the canonical check
- `ralph-workflow/src/agents/session/mod.rs` - Session drain identity preservation — check and fix continuations
- `ralph-workflow/src/config/unified/types.rs` - UnifiedConfig and resolve_agent_drains_checked — enforcement point for strict drain bindings

## Risks & Mitigations

**Risk [critical]:** Deleting XSD files before removing all code that references them will cause compilation errors. The five XSD files are referenced from schema_contract_tests.rs and possibly syntax_validation.rs and prompts/mod.rs. Deleting files without first auditing every reference will break the build.
**Mitigation:** Execute step 14 strictly in order: (1) grep entire codebase for all .xsd references, (2) remove all referencing code, (3) run cargo build to confirm zero errors, (4) only then delete the XSD files. Never delete first.

**Risk [high]:** Replacing DevelopmentAnalysisDecision and ReviewAnalysisDecision enum variants (step 6) will produce exhaustive match errors across the entire codebase. Missing even one match site will cause a compilation error. The old vocabulary had 5 variants; the new vocabulary has 3. Any code that matched on needs_replanning or ready_for_review specifically will break.
**Mitigation:** Use cargo's exhaustive match checking as the discovery mechanism — after changing the enum, run cargo build immediately. Every error is a match site that needs updating. Do not use _ catch-all patterns to suppress errors; update each match arm explicitly.

**Risk [high]:** Updating pipeline.toml phase sequence (step 1) from the old sequence to development_commit/review_commit may break the Rust code that drives the phase state machine if it hard-codes the old phase names (commit_message, final_validation, finalizing). If the state machine reads phase names from config but the reducer has fallback code matching old string names, the pipeline will stop advancing.
**Mitigation:** Before updating pipeline.toml, search the Rust source for all occurrences of the old phase name strings: "commit_message", "final_validation", "finalizing", "awaiting_dev_fix". Update those references to match the new names. Then update pipeline.toml. Run integration tests to verify the pipeline advances through all phases.

**Risk [high]:** Removing sibling drain inference from fallback.rs (step 5) without ensuring all drains are explicitly bound in ralph-workflow.toml first will cause the pipeline to fail at startup with missing drain binding errors. If a user's agents.toml only binds planning/development/review and relies on sibling inference for analysis, commit, and fix, removing inference will break their run.
**Mitigation:** Steps 3 (add fixer chain, update all 6 drains in ralph-workflow.toml) must be completed before step 5 (remove inference). The updated .agent/ralph-workflow.toml must bind all six drains explicitly. Verify by running startup validation before running end-to-end. The test added in step 5 catches this.

**Risk [medium]:** Renaming invoke_analysis_agent to invoke_development_analysis_agent (step 7) requires updating all callers. If a caller is in a test file or a rarely-executed code path, it may be missed. The analyzer boundary functions are used by the orchestration and state_reduction layers.
**Mitigation:** After the rename, run cargo build — the compiler will surface every missing call site as an unresolved function error. Fix each one before proceeding. Do not use the old function names as aliases.

**Risk [medium]:** Strengthening artifact acceptance (step 12) by adding run_id and drain checks may reject valid artifacts in resume scenarios where a pipeline is resumed across runs (checkpoint resume). If the artifact was written in run A and the pipeline is resumed in run B, the run_id check will reject it.
**Mitigation:** Read the ArtifactAcceptanceConfig to understand what require_current_run_identity actually means in the checkpoint resume context. If checkpoint resume is a supported flow, the acceptance check must be conditioned on whether the current phase is being resumed from a checkpoint (in which case the persisted run_id should be propagated). Do not silently accept stale artifacts — instead ensure the checkpoint correctly restores the accepted run_id alongside the phase state.

**Risk [medium]:** The awaiting_dev_fix state machine path in reducer/state_reduction/ may not be purely legacy XML — it may also handle MCP-based recovery flows. Deleting it without understanding its full role could remove valid recovery behavior.
**Mitigation:** Read awaiting_dev_fix/ completely before taking any deletion action. Only delete code that has zero callers after the other changes in this plan land. If the module has callers in the MCP artifact flow, keep it and update it to remove only the XML-specific parts.

**Risk [low]:** The plan requires adding a fixer chain to ralph-workflow.toml that maps to real agent providers. If the agent list for the fixer chain is incorrect (e.g., references a provider not available in the test environment), end-to-end tests that exercise the fix drain will fail with agent-not-found errors.
**Mitigation:** Copy the fixer chain agent list from the existing developer chain as a baseline — the same write-capable agents are appropriate for fix work. This is a safe starting point that can be tuned per project later.

## Verification Strategy

1. **Run cargo xtask verify (on rw-build-server as required by project policy) after each phase group of steps is complete. Accept only zero ERROR/WARNING output.**
   Expected: All lint, format, and test lanes pass. No #[allow(...)] suppressions introduced. No .unwrap()/.expect() outside permitted boundary modules.

2. **After step 5 (remove sibling inference): start the pipeline with a config that has only four drains bound (planning/development/review/commit) and assert it fails at startup with a diagnostic naming the missing fix and analysis drains, not silently inheriting from siblings.**
   Expected: Startup produces ResolveDrainError for fix and analysis drains. Error messages name the specific drain, the config file (.agent/ralph-workflow.toml), and the required field.

3. **After step 6 (decision enum update): run cargo build immediately after changing the enum variants. Zero compilation errors means all match sites were updated. Then run cargo xtask test -p ralph-workflow --lib reducer::state_reduction::tests::metrics to confirm no metric regressions.**
   Expected: Zero compilation errors. Metric tests pass with no change in iteration counter behavior.

4. **After step 14 (XSD deletion): run cargo xtask verify to confirm no references to .xsd files remain in any compilation unit, test file, or include directive.**
   Expected: Clean build with zero references to XSD files. prompts/xsd/ directory no longer exists or is empty.

5. **After step 15 (invariant tests): run cargo xtask test -p ralph-workflow-tests --test integration_tests to verify regression tests pass. Specifically verify: test_planning_never_routes_to_wrong_chain, test_stale_artifact_rejected, test_development_analysis_routes_only_to_development_or_commit, test_review_analysis_routes_only_to_fix_or_review_commit.**
   Expected: All new invariant tests pass. No existing tests regressed.

6. **After all phases complete: run cargo xtask lsp-forbidden-allow-expect to confirm zero #[allow(...)] and zero unauthorized .expect()/.unwrap() calls were introduced.**
   Expected: Command exits clean with zero violations found.

7. **After all phases complete: verify that .agent/artifacts.toml parses correctly against the ArtifactsConfig type by running the policy crate's io_tests that deserialize both pipeline.toml and artifacts.toml.**
   Expected: All policy crate deserialization tests pass. Both files parse without serde errors. The [development_analysis] and [review_analysis] sections produce entries with status_field="status" and allowed_statuses=["completed","partial","failed"].

8. **After steps 13 and 14: grep the entire source tree for the string "fix_result.xml" and "development_result.xml". These must return zero matches in production code (test fixtures referencing these names to assert they are absent is acceptable).**
   Expected: Zero production code references to legacy XML artifact filenames.

---

## Follow-up: MCP Reachability Preflight

**Context**: With MCP-only tool enforcement now active (per plan `.sisyphus/plans/mcp-only-tool-enforcement.md`), agents fail silently if Ralph's MCP server is unreachable — they have no native tools to fall back on, producing garbage output that may pass the parser.

**Required**: Add a reachability preflight to `ralph/mcp/startup.py` (or equivalent) that:
1. Attempts a `GET /mcp` (or `POST initialize`) probe to the MCP endpoint before `invoke_agent` is called.
2. On failure, raises `McpUnreachableError` which the pipeline reducer treats as a fatal (non-retryable) error.
3. Logs the probe result at DEBUG level for troubleshooting.

**Related**: Metis gap B6 from planning session 2026-04-16.
