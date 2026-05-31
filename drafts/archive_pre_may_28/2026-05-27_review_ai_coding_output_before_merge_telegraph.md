# Review AI Coding Output Before Merge

The risky part of AI coding is usually not getting code written.

It is deciding too early that the result is safe to merge.

A clean review step is what turns an unattended run from a persuasive transcript into something you can actually trust.

## The only question that really matters

Before merge, ask:

**Would I merge this if a human teammate handed it to me?**

If the answer is not clearly yes yet, the run is not done.

## What to review first

Start with the evidence, not the summary.

1. **Read the task again**
   - what was supposed to change?
   - what was supposed to stay stable?
   - what checks were promised?

2. **Open the diff**
   - does the change stay inside the expected scope?
   - did it touch shared boundaries like auth, config, schema, migrations, or permissions?
   - is the diff small enough to review honestly?

3. **Check what actually ran**
   - tests
   - lint
   - build
   - any task-specific verification named in the spec

4. **Look for unresolved judgment calls**
   - edge cases
   - product choices
   - security or permission changes
   - assumptions the run made without confirmation

5. **Decide whether the finish is boring enough**
   - clear diff
   - real checks
   - open questions called out
   - no archaeology required

That is the standard.

## A fast merge review checklist

Use this list:

- does the diff match the task?
- is anything important missing?
- did the checks really run?
- did the change cross a shared boundary?
- are open risks named clearly?
- would I merge this today?

If you cannot answer those quickly, the handoff is still weak.

## Shared-boundary warning signs

Slow down immediately if the run touched:
- auth or permissions
- billing or money flows
- schema or migrations
- config defaults
- deployment or CI behavior
- anything security-sensitive

Those changes usually deserve a second set of eyes even when the checks are green.

## What a good final handoff looks like

A useful finish should include:
- the task that was attempted
- changed files
- what changed in plain language
- checks that ran
- what failed and was fixed
- what still needs a human decision

If you want a concrete shape, use **[Review bundle example](../examples/review_bundle_example.md)**.

## What not to trust

Do not trust these by themselves:
- a confident summary
- a green claim with no named commands
- a huge diff you do not have time to read
- “nothing major changed”
- “all tests passed” without saying which tests

The source of truth is the diff plus the verification evidence.

## How Ralph Workflow helps

Ralph Workflow is the operating system for autonomous coding: a free and open-source composable loop framework and AI orchestrator.

The point is not to replace your judgment.
It is to make judgment easier by pushing toward:
- clearer task boundaries
- build and verification in the same workflow
- evidence instead of self-certification
- a reviewable morning-after handoff

If you want to try that on one real task:
- [Start here: one real task](../../START_HERE.md)
- [Tomorrow-morning scorecard](../examples/tomorrow_morning_scorecard.md)
- [Good unattended task vs bad one](./good_unattended_task.md)
- [Autonomous AI workflows for production reliability](./autonomous_ai_workflows_production_reliability.md)
- [Claude Code + Codex workflow](./claude_code_codex_workflow.md)

## Primary repo

Use **Codeberg** as the main repo surface:
- <https://codeberg.org/RalphWorkflow/Ralph-Workflow>

GitHub is the mirror:
- <https://github.com/Ralph-Workflow/Ralph-Workflow>
