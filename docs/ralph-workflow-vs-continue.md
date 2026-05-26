# Ralph Workflow vs Continue

> **Codeberg is the primary repo:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
>
> **GitHub is only the mirror:** <https://github.com/Ralph-Workflow/Ralph-Workflow>

If Continue already gives you the exact in-editor workflow you want, keep using it.

Ralph Workflow is for the point where a good AI coding assistant is not enough anymore:
- the task is too big to babysit live in the editor
- you want planning, implementation, verification, and review to stay connected
- you want a morning-after handoff you can judge quickly
- you want a strong default workflow before building custom orchestration

Ralph Workflow is the operating system for autonomous coding: a free and open-source composable loop framework and AI orchestrator.
It can work with the coding agents you already use, but its real value is the workflow shape around them.

## The short version

Use **Continue** when you want:
- in-editor iteration inside VS Code or JetBrains
- quick prompting against the current file or codebase context
- a familiar assistant experience with multi-model flexibility
- fast pair-programming where the editor session is the workflow

Use **Ralph Workflow** when you want:
- one bounded task contract before execution starts
- explicit verification instead of a loose done claim
- a reviewable handoff with diff, checks, outcome, and open risks
- a stronger default workflow for substantial software work
- the option to use the default now and build on top later

## The real difference

The difference is not just IDE versus terminal.
It is whether the coding agent is the whole system or one part of a larger workflow.

Continue is strongest as an in-editor AI code assistant.
Ralph Workflow is strongest when the work needs to hold up after the session ends.

That usually means preserving four phases clearly:
1. sharpen the task
2. implement the change
3. run the promised checks
4. hand back a reviewable result

That is the gap Ralph Workflow is built to close.

## Side-by-side

| Question | Continue | Ralph Workflow |
| --- | --- | --- |
| What is it best at? | In-editor AI assistance and quick iteration | Structured autonomous coding workflows across phases |
| Best first use | Fast pair-programming in your editor | One real backlog task with explicit checks |
| Default finish | Editor/session output | Reviewable handoff with task + checks + outcome |
| Best fit pain | "Help me work faster in the IDE" | "Make this hold up tomorrow morning" |
| Workflow model | Assistant centered | Simple core loop composed into a stronger workflow |
| Extensibility | Strong model/tool flexibility inside the editor | Strong default workflow plus composable extension path |

## When Ralph Workflow is the better fit

Reach for Ralph Workflow first when your pain sounds like this:
- "The agent said done, but I still cannot trust the result."
- "The task is too big to keep steering in one editor session."
- "I want explicit planning, implementation, verification, and review."
- "I need something reviewable tomorrow morning, not just chat context."
- "I want a strong default before I invent my own orchestration."

## When Continue is the better fit

Stay with Continue first when:
- the task is small and interactive
- you want to stay inside your editor the whole time
- success is mostly about coding speed, not workflow rigor
- there is no clear done condition yet
- review is trivial and setup cost would dominate

## Best way to evaluate Ralph Workflow honestly

Do not compare it on a toy prompt.

Use one meaningful backlog task that is:
- too big to babysit in one chat or editor session
- small enough to review in one sitting
- clear enough to verify
- safe enough to roll back

Then run this path:
1. [Start here on one real task](../START_HERE.md)
2. [Pick the right first task](./first-task-guide.md)
3. [See when unattended coding is a good fit](../content/guides/good_unattended_task.md)
4. [Review the result like a real merge decision](../content/guides/review_ai_coding_output_before_merge.md)

If the outcome is a clean diff plus real checks you would merge, that is the signal.

## Primary repo

Inspect, star, watch, fork, and open issues on **Codeberg** first:
- <https://codeberg.org/RalphWorkflow/Ralph-Workflow>

Use GitHub only if you need the mirror:
- <https://github.com/Ralph-Workflow/Ralph-Workflow>
