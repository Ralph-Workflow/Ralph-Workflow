# Spec-Driven AI Agents: Why the Handoff Problem Is the Real Bottleneck

The conversation about AI coding agents usually focuses on model capability: which model is smarter, which has a longer context window, which produces fewer hallucinations. But after running AI coding sessions for months at scale, the real bottleneck is not generation — it is the handoff.

A spec-driven AI agent is one that receives a written specification before it starts, produces output against that spec, and hands back a bounded result: what changed, what passed, and what could not be decided. Without that structure, the agent can produce an impressive transcript and leave you with a result you cannot trust.

## What a Spec Actually Does for an AI Agent

A spec is not a prompt. A prompt is "build a user auth system." A spec is:

- What the system should do, written as outcomes not implementation steps
- What counts as done: not "the code looks right" but "the tests pass, the API contracts are met, and the edge cases are documented"
- What the agent should not touch: the database schema, the CI configuration, the existing test suite
- What to hand back when finished: a diff, check results, and a list of decisions that required human judgment

When the agent has that before it starts, the run has a finish line. Without it, the agent keeps going until it decides to stop, which is usually when it runs out of context or hits a rate limit.

## The Handoff Problem

AI agents fail handoffs in predictable ways. The most common:

**The agent finishes but you cannot evaluate the result.** No bounded diff, no check output, just a confident paragraph saying the work is done. You spend the next hour figuring out what actually changed.

**The agent escalates to you mid-run.** Not because it hit a real blocker, but because it never had a written definition of what it should handle autonomously. Every ambiguous case becomes a prompt back to you.

**The agent produces a result that looks right but fails silently.** Tests were not run, edge cases were not considered, the API contract was not checked. The morning after looks fine until production.

A spec-driven agent produces a result you can evaluate in under five minutes, or it escalate before it diverges.

## What Spec-Driven Actually Means in Practice

Spec-driven does not mean writing a 40-page PRD before every task. It means writing enough to define the finish line:

One paragraph on what the task is. One paragraph on what success looks like. A list of three to five things the agent should not touch without asking.

That is enough to give the agent a bounded task and you a checkable result.

## The Tooling Gap

Most AI coding tools run without a spec layer. You open a session, describe what you want, and the agent goes. The result is unbounded — it can do more than you asked, less than you needed, or something in between that only becomes clear when something breaks.

Ralph Workflow is an attempt to close that gap. It runs the planning loop before the coding loop: draft the spec, critique it, revise until it is solid. Only then does the coding agent run. The result is bounded and checkable instead of impressive and unknown.

The spec is the contract. Everything else is just running until the contract is satisfied.
