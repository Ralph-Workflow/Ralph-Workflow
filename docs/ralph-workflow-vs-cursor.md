# Ralph Workflow vs Cursor

> **Codeberg is the primary repo:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
>
> **GitHub is only the mirror:** <https://github.com/Ralph-Workflow/Ralph-Workflow>

If Cursor already fits the way you work, keep using it.

Ralph Workflow is for the point where an AI-first editor is not enough anymore:
- the task is too big to drive turn by turn
- you want planning, implementation, verification, and review to stay connected
- you want a strong default workflow instead of stitching together editor habits
- you want to come back to a bounded result you can judge quickly

Ralph Workflow is the operating system for autonomous coding: a free and open-source composable loop framework and AI orchestrator.
It does not try to replace your editor.
It gives you a stronger workflow around the coding agents you already use.

## The short version

Use **Cursor** when you want:
- interactive pair programming inside the editor
- fast local iteration with inline help
- short feedback loops while you stay at the keyboard
- chat and code changes in one editing surface

Use **Ralph Workflow** when you want:
- one bounded task contract before coding starts
- explicit checks instead of a vague sense that the run probably worked
- a reviewable morning-after handoff
- a strong default workflow for substantial software work
- the option to use the default now and build on top later

## The real difference

The core difference is not which tool feels smarter in the moment.
It is what happens when the session ends.

Cursor is strongest as an AI-first coding editor.
Ralph Workflow is strongest when the work needs to survive beyond an interactive editing session.

That usually means preserving four phases clearly:
1. sharpen the task
2. implement the change
3. run the promised checks
4. hand back a reviewable result

That is the gap Ralph Workflow is built to close.

## Side-by-side

| Question | Cursor | Ralph Workflow |
| --- | --- | --- |
| What is it best at? | Interactive AI coding inside the editor | Structured autonomous coding workflows across phases |
| Best first use | Fast pair programming in one repo | One real backlog task with explicit checks |
| Default finish | Editor/session progress | Reviewable handoff with task + checks + outcome |
| Best fit pain | "Help me build this while I steer" | "Make this hold up tomorrow morning" |
| Workflow model | Editor-centered | Simple core loop composed into a stronger workflow |
| Extensibility | Strong editor UX | Strong default workflow plus composable extension path |

## When Ralph Workflow is the better fit

Reach for Ralph Workflow first when your pain sounds like this:
- "The AI can write code, but I still do too much manual babysitting."
- "The work is large enough that I need a real finish line."
- "I want the checks, risks, and outcome to come back together."
- "I want a workflow, not just a smarter editor tab."
- "I want to use a default path first, then customize later."

## When Cursor is the better fit

Stay with Cursor first when:
- the task is a fast edit or exploratory build
- you want to steer continuously in the editor
- the done condition is still changing while you work
- review is trivial and workflow setup would dominate

## Best way to evaluate Ralph Workflow honestly

Do not compare it on autocomplete quality.
Compare it on whether a real task survives the full loop.

Use one meaningful backlog task that is:
- too big to babysit line by line
- small enough to review in one sitting
- clear enough to verify
- safe enough to roll back

Then run this path:
1. [Start here on one real task](../START_HERE.md)
2. [Pick the right first task](./first-task-guide.md)
3. [See when unattended coding is a good fit](../content/guides/good_unattended_task.md)
4. [Review the result like a real merge decision](../content/guides/review_ai_coding_output_before_merge.md)

If the output gives you a clean diff plus real checks you would merge, that is the signal.

## Primary repo

Inspect, star, watch, fork, and open issues on **Codeberg** first:
- <https://codeberg.org/RalphWorkflow/Ralph-Workflow>

Use GitHub only if you need the mirror:
- <https://github.com/Ralph-Workflow/Ralph-Workflow>
