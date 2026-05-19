# Spec-Driven AI Agent: Why the Spec Matters More Than the Prompt

If an AI coding agent keeps saying it is done before the job actually holds up, the problem is usually not raw model quality.

It is the lack of a real spec.

Ralph Workflow is built around that reality. It is a **free, open-source** workflow that helps you run existing coding agents on your own machine, but with a sharper contract: define the task clearly, verify the result, and only call it done if you would actually merge it.

**Primary repo:** https://codeberg.org/RalphWorkflow/Ralph-Workflow  
**GitHub mirror:** https://github.com/Ralph-Workflow/Ralph-Workflow

## What makes an AI agent spec-driven?

A spec-driven agent does not start from "just try your best."

It starts from a short written contract:
- what needs to change
- what must stay untouched
- what checks matter
- what "done" actually means

That matters because prompts ask for effort.

Specs define success.

## Prompt-first vs spec-first

A prompt-first task often sounds like this:

> Add rate limiting to the user API.

A spec-first task is tighter:

> Add rate limiting to `/api/users` using an in-memory token bucket. Do not change auth behavior. Return `429` with `Retry-After` on breach. Add isolated tests. Success means existing auth tests still pass, new rate-limit tests pass, and the diff stays reviewable.

That second version gives both the agent and the reviewer something concrete to work against.

## Why this matters for unattended work

If you want to hand off real engineering work overnight, you need more than a clever agent.

You need a loop that can:
1. sharpen the task before coding starts
2. build against explicit constraints
3. run checks instead of trusting self-reports
4. hand back a reviewable diff in the morning

That is the core Ralph Workflow promise:
- work too big to babysit
- too risky to trust blindly
- reviewable when you come back

## What a good first spec looks like

Keep it short.

```md
## Task
Add rate limiting to the user creation endpoint.

## Constraints
- in-memory token bucket
- no new external services
- do not change auth flow
- add isolated tests

## Done means
- existing tests still pass
- new rate-limit tests pass
- endpoint returns 429 correctly
- diff stays small enough to review
```

That is enough.

Not a PRD. Not a novel. Just a contract the agent can satisfy and you can verify.

## Why Ralph Workflow fits this search intent

Ralph Workflow is useful if you already have agents like Claude Code, Codex, or OpenCode and want a better way to run substantial work on your own machine.

It is **not** another chat box.

It is a workflow for:
- plan/build/verify loops
- explicit task boundaries
- reviewable output instead of vibes
- waking up to something you can actually inspect

## Fastest honest evaluation

1. Pick one real backlog task
2. Write a one-paragraph spec
3. Run Ralph Workflow tonight
4. Come back to the diff and checks
5. Ask: **would I merge this?**

If yes, the loop worked.

If not, sharpen the spec and rerun.

## Next step

If this matches how you want to use coding agents, start on **Codeberg**:
- inspect the repo
- star it if you want to track it
- watch it for updates
- open an issue if your first run exposes friction

**Codeberg (primary):** https://codeberg.org/RalphWorkflow/Ralph-Workflow  
**GitHub mirror:** https://github.com/Ralph-Workflow/Ralph-Workflow
