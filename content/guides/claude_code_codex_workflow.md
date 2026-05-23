# Claude Code + Codex Workflow: Plan, Build, Review

If you are already using Claude Code and Codex together, the hard part is usually not getting code written.

It is getting a handoff you can trust the next morning.

That usually breaks in one of four places:
- the task was never sharpened enough before implementation started
- the build step and the verification step got split across tools with no clean finish line
- one tool made assumptions the next tool silently inherited
- the final artifact was a transcript, not a reviewable result

## A boring workflow that holds up better

A practical Claude Code + Codex workflow usually looks like this:

1. **Plan the task**
   - choose one bounded backlog item
   - write a one-paragraph spec
   - define what must stay unchanged
   - name the checks up front

2. **Build in an isolated run**
   - keep the scope narrow
   - avoid mixing multiple unrelated changes
   - keep the diff reviewable

3. **Verify before calling it done**
   - run the tests, lint, build, or other checks you named in the spec
   - record what passed, what failed, and what was fixed
   - call out any open questions explicitly

4. **Review the morning-after artifact**
   - inspect changed files
   - inspect the checks that actually ran
   - ask one question: **would I merge this?**

That is the workflow gap most teams feel when they say they are "using Claude Code and Codex together" but still do a lot of manual glue work.

## Copy-paste spec template

```md
Change:
[what should change]

Keep unchanged:
[what must stay stable]

Done means:
[observable outcome]

Checks:
[tests, lint, build, or other verification]
```

Example:

```md
Change:
Add one filter and CSV export to the billing history page.

Keep unchanged:
Do not change invoice creation or billing calculations.

Done means:
Users can filter billing history by date range and export matching rows to CSV.

Checks:
Relevant billing tests pass and any new billing-history tests pass.
```

## Where manual glue gets painful

The common failure mode is not model quality.

It is that the workflow never clearly separates:
- task sharpening
- implementation
- verification
- final review

That is why worktrees alone are not enough.
They help isolate code changes, but they do not define done, run checks, or force a clean review bundle.

## Where Ralph Workflow fits

Ralph Workflow is the operating system for autonomous coding: a free and open-source composable loop framework and AI orchestrator.

Its job is not to replace Claude Code or Codex.
Its job is to make the whole loop more reviewable:
- sharpen the task before coding starts
- keep build and verification in the same workflow
- end with evidence, not just a claim
- give you a strong default workflow you can use as-is or build on top

If that is what you are trying to get from Claude Code + Codex, start here:
- [Try one real task](../../START_HERE.md)
- [Good unattended task vs bad one](./good_unattended_task.md)
- [Review bundle example](../examples/review_bundle_example.md)

## When not to use this workflow

Do not start with:
- a vague product idea
- risky production surgery
- a multi-system rewrite
- any task where nobody agrees what done means

The first run should be real, but easy to judge.

## Primary repo

Use **Codeberg** as the main repo surface:
- <https://codeberg.org/RalphWorkflow/Ralph-Workflow>

GitHub is the mirror:
- <https://github.com/Ralph-Workflow/Ralph-Workflow>
