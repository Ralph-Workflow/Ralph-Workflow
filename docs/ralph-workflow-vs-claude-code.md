# Ralph Workflow vs Claude Code

> **Codeberg is the primary repo:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
>
> **GitHub is only the mirror:** <https://github.com/Ralph-Workflow/Ralph-Workflow>

If Claude Code already handles the job cleanly for you, keep using it.

Ralph Workflow is for the point where a good coding agent is not the whole problem anymore:
- the task is too big to babysit live
- you want planning, implementation, verification, and review to stay connected
- you want to come back to something reviewable, not just a transcript
- you want a strong default workflow before you build custom orchestration

Ralph Workflow does not replace Claude Code.
It is the operating system for autonomous coding: a free and open-source composable loop framework and AI orchestrator that can run the agent CLIs you already use.

## The short version

Use **Claude Code alone** when you want:
- fast interactive iteration
- a short coding session in one repo
- live steering in the same terminal
- quick edits where the chat context is the workflow

Use **Ralph Workflow** when you want:
- one bounded task contract before coding starts
- explicit verification instead of a loose "done" claim
- a morning-after handoff you can judge quickly
- a stronger default workflow for substantial software work
- the option to use the default now and build on top later

## The real difference

The main difference is not model quality.
It is workflow shape.

Claude Code is strongest as an interactive coding tool.
Ralph Workflow is strongest when you need the work to hold up after the session ends.

That usually means the workflow has to preserve four phases clearly:
1. sharpen the task
2. implement the change
3. run the promised checks
4. hand back a reviewable result

That is the gap Ralph Workflow is built to close.

## Side-by-side

| Question | Claude Code | Ralph Workflow |
| --- | --- | --- |
| What is it best at? | Interactive agentic coding in-session | Structured autonomous coding workflows across phases |
| Best first use | Quick iteration and direct steering | One real backlog task with explicit checks |
| Default finish | Session output | Reviewable handoff with task + checks + outcome |
| Best fit pain | "Help me build this right now" | "Make this hold up tomorrow morning" |
| Workflow model | Chat/session centered | Simple core loop composed into a stronger workflow |
| Extensibility | Strong tool experience | Strong default workflow plus composable extension path |

## When Ralph Workflow is the better fit

Reach for Ralph Workflow first when your pain sounds like this:
- "The agent said done, but I still cannot trust the result."
- "The work is too large to babysit step by step."
- "I want a workflow, not more glue between tools."
- "I want to review a bounded diff with named checks tomorrow morning."
- "I want a strong default before I design my own orchestration."

## When Claude Code is the better fit

Stay with Claude Code first when:
- the task is a small edit
- you want live back-and-forth steering
- there is no clear done condition yet
- review is trivial and the setup cost would dominate

## Best way to evaluate Ralph Workflow honestly

Do not compare it on a toy prompt.

Use one meaningful backlog task that is:
- too big to babysit in one chat
- small enough to review in one sitting
- clear enough to verify
- safe enough to roll back

Then run this path:
1. [Start here on one real task](../START_HERE.md)
2. [Pick the right first task](./first-task-guide.md)
3. [See the Claude Code + Codex workflow shape](../content/guides/claude_code_codex_workflow.md)
4. [Review the result like a real merge decision](../content/guides/review_ai_coding_output_before_merge.md)

If the outcome is a clean diff plus real checks you would merge, that is the signal.

## Primary repo

Inspect, star, watch, fork, and open issues on **Codeberg** first:
- <https://codeberg.org/RalphWorkflow/Ralph-Workflow>

Use GitHub only if you need the mirror:
- <https://github.com/Ralph-Workflow/Ralph-Workflow>
