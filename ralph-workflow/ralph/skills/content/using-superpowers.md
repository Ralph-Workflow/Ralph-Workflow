# using-superpowers

## Purpose
This skill is the baseline meta-practice for working inside Ralph Workflow. It keeps the operator aligned with the shipped workflow bundle, the repository's verification expectations, and the idea that the agent should act like a disciplined senior engineer rather than a free-form chatbot. It is the first skill to consider whenever a task involves planning, implementation, debugging, review, or handoff.

Using this skill consistently prevents drift between the prompt surface and the real capability layer. It also reinforces that the repo ships a deliberate default baseline instead of requiring the user to assemble workflow behavior from scratch.

## When To Use
- At the start of every developer or planning task.
- When the prompt references skills, verification, or execution quality.
- Before deciding whether to delegate, test, or inspect repository state.
- When you need to keep the workflow story coherent across prompts and code.

## Key Steps / Approach
1. Read the user request and identify the actual outcome, not just the surface wording.
2. Map the task to the most relevant downstream workflow skills and repository files.
3. Prefer concrete verification and evidence over vague success claims.
4. Record assumptions when they matter and avoid inventing missing context.
5. Finish only after the repo state, tests, and artifacts all agree with the request.

## Common Pitfalls
- Treating the skill as decorative rather than operational.
- Skipping verification because the change looks small.
- Forgetting to align prompt wording with the real capability layer.
