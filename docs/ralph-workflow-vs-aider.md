# Ralph Workflow vs Aider

> **Codeberg is the primary repo:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
>
> **GitHub is only the mirror:** <https://github.com/Ralph-Workflow/Ralph-Workflow>

If Aider already covers the job cleanly, keep using Aider.

Ralph Workflow is for the point where interactive pair-programming is no longer enough:
- the task is too big to babysit live
- you want planning, implementation, verification, and review to stay connected
- you want a stronger default workflow instead of stitching the process together yourself
- you want to judge the result tomorrow morning by the diff and checks, not by how persuasive the session felt

Ralph Workflow does not replace Aider.
It is the operating system for autonomous coding: a free and open-source composable loop framework and AI orchestrator that can run the coding agents you already use.

## The short version

Use **Aider alone** when you want:
- fast terminal pair-programming inside one repo
- quick interactive edits
- live back-and-forth steering
- git-native assistance on bounded changes

Use **Ralph Workflow** when you want:
- one bounded task contract before coding starts
- explicit verification instead of a loose done claim
- a morning-after handoff you can review quickly
- a strong default workflow for substantial software work
- the option to use the default now and build on top later

## The real difference

The main difference is not raw model access.
It is workflow shape.

Aider is strongest as an interactive coding partner in the terminal.
Ralph Workflow is strongest when the work needs to hold up after the session ends.

That usually means the workflow has to preserve four phases clearly:
1. sharpen the task
2. implement the change
3. run the promised checks
4. hand back a reviewable result

That is the gap Ralph Workflow is built to close.

## Side-by-side

| Question | Aider | Ralph Workflow |
| --- | --- | --- |
| What is it best at? | Interactive git-native pair-programming in your terminal | Structured autonomous coding workflows across phases |
| Best first use | Quick iteration on a bounded change | One real backlog task with explicit checks |
| Default finish | Session output and edits | Reviewable handoff with task + checks + outcome |
| Best fit pain | "Help me change this now" | "Make this hold up tomorrow morning" |
| Workflow model | Chat/session centered | Simple core loop composed into a stronger workflow |
| Extensibility | Strong direct tool loop | Strong default workflow plus composable extension path |

## When Ralph Workflow is the better fit

Reach for Ralph Workflow first when your pain sounds like this:
- "The agent said done, but I still cannot trust the result."
- "The work is too large to babysit step by step."
- "I want a workflow, not more glue between tools."
- "I want a bounded diff with named checks tomorrow morning."
- "I want to use the default now, then extend it later without throwing it away."

## When Aider is the better fit

Stay with Aider first when:
- the task is a small edit
- you want live steering in the same terminal
- there is no clear done condition yet
- review is trivial and the setup cost would dominate
- you mainly want fast git-native pair-programming, not a broader workflow

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
3. [See what makes a good unattended task](../content/guides/good_unattended_task.md)
4. [Review the result like a real merge decision](../content/guides/review_ai_coding_output_before_merge.md)

If the outcome is a clean diff plus real checks you would merge, that is the signal.

## Primary repo

Inspect, star, watch, fork, and open issues on **Codeberg** first:
- <https://codeberg.org/RalphWorkflow/Ralph-Workflow>

Use GitHub only if you need the mirror:
- <https://github.com/Ralph-Workflow/Ralph-Workflow>
