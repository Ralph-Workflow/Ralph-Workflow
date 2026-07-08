# Task for scout

You are a read-only research subagent. Working dir: /Volumes/Crucial X9/ext-Projects/Ralph-Workflow/wt-026-documentation

For each of these files, read the file content (use head or read_file) and report the EXACT contents of the file paths/assertions. I need to know what these tests will assert against, so I can determine if the proposed documentation consolidation plan would break them.

1. ralph-workflow/tests/test_pro_support_cross_repo_marker.py — read ENTIRE file. Report: what it imports, what paths it asserts exist, what content it asserts is present.

2. ralph-workflow/tests/test_parallel_mode_docs_namespaced_payload_docs.py — read ENTIRE file. Report what it asserts about docs/sphinx/parallel-mode.md.

3. ralph-workflow/tests/test_parallel_mode_docs_banned_phrases_across_all_docs.py — read ENTIRE file.

4. ralph-workflow/tests/test_parallel_docs_no_worktree_language.py — read ENTIRE file.

5. ralph-workflow/tests/test_sphinx_documentation_setup.py — read ENTIRE file. Report what files it REQUIRES to exist.

6. ralph-workflow/tests/test_readme_long_content_summary_doc.py — read ENTIRE file.

7. ralph-workflow/tests/test_repo_root_operational_docs_sync.py — read ENTIRE file.

8. ralph-workflow/tests/test_docs_context_completeness_repo_root_completeness.py — read ENTIRE file.

9. ralph-workflow/tests/test_docs_context_completeness_sphinx_page_completeness.py — read ENTIRE file.

10. ralph-workflow/tests/test_docs_readme_scope.py — read ENTIRE file.

11. ralph-workflow/tests/test_documentation_command_sync.py — read ENTIRE file.

12. ralph-workflow/tests/test_pro_support_hooks.py — read lines 470-510 only.

Report back:
- For each test file: does it hardcode the existence of a file that the plan wants to delete (parallel-mode.md, policy-explanation.md, quick-reference.md, reference.md, developer-reference.md, changelog.md, ralph-loop.md, policy-driven-pipeline.md, phase-routing.md, artifact-lifecycle.md, verification-model.md, watchdogs-and-timeouts.md, transcript.md, supervising-api.md, local-web-access.md, docs/agents/pro-contract.md, docs/migration/policy-v2.md, docs/migration/parallel-mode.md, docs/VERIFICATION_GATE.md)?
- For each: would deleting the target file break the test (i.e., would pytest fail)?
- Identify any test that would still pass if the file were deleted (e.g., tests that use skipIf-missing).

Use read_file tool only. Do not modify anything. Be concise but complete.

---
**Output:**
Write your findings to exactly this path: /Volumes/Crucial X9/ext-Projects/Ralph-Workflow/wt-026-documentation/.pi-subagents/artifacts/outputs/d966cca9/file
This path is authoritative for this run.
Ignore any other output filename or output path mentioned elsewhere, including output destinations in the base agent prompt, system prompt, or task instructions.

## Acceptance Contract
Acceptance level: reviewed
Completion is not accepted from prose alone. End with a structured acceptance report.

Criteria:
- criterion-1: Implement the requested change without widening scope
- criterion-2: Return evidence sufficient for an independent acceptance review

Required evidence: changed-files, tests-added, commands-run, validation-output, residual-risks, no-staged-files

Review gate: required by reviewer.

Finish with a fenced JSON block tagged `acceptance-report` in this shape:
Use empty arrays when no items apply; array fields contain strings unless object entries are shown.
```acceptance-report
{
  "criteriaSatisfied": [
    {
      "id": "criterion-1",
      "status": "satisfied",
      "evidence": "specific proof"
    }
  ],
  "changedFiles": [
    "src/file.ts"
  ],
  "testsAddedOrUpdated": [
    "test/file.test.ts"
  ],
  "commandsRun": [
    {
      "command": "command",
      "result": "passed",
      "summary": "short result"
    }
  ],
  "validationOutput": [
    "validation output or concise summary"
  ],
  "residualRisks": [
    "none"
  ],
  "noStagedFiles": true,
  "diffSummary": "short description of the diff",
  "reviewFindings": [
    "blocker: file.ts:12 - issue found, or no blockers"
  ],
  "manualNotes": "anything else the parent should know"
}
```