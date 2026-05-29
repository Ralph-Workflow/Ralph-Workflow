---
title: Spec-Driven AI Agent Runs — How to Define Done Before the Agent Starts
date: 2026-05-20
type: technical
keyword: spec-driven AI agent
cta: install_ralphworkflow
---

# Spec-Driven AI Agent Runs — How to Define Done Before the Agent Starts

The most common reason AI agent runs produce frustrating results is not model quality. It is that nobody defined what success looks like before the agent started.

A spec-driven AI agent run is one where the human specifies the acceptance criteria before the agent begins work, and the agent's output is judged against those criteria — not against the agent's own self-assessment.

## Why Spec-First Changes Everything for Unattended Work

When you run an agent without a spec, you are running it on vibes. The agent will produce something. It will look busy. It will probably look like progress. But "looks like progress" and "solved the actual problem" are different things.

A spec fixes this by making the finish line explicit:

- What needs to be true when the run is done?
- What constraints must hold?
- What would make this obviously wrong?

The agent then works against measurable criteria instead of conversational confidence.

## The Spec Structure That Actually Works

A useful spec for an AI agent run is not a detailed implementation plan. It is a set of constraints and acceptance tests:

**Constraints** (what must not break):
- existing tests still pass
- no changes to shared config without explicit callout
- no auth or permission model changes

**Acceptance criteria** (what must be true):
- feature X implemented as described
- new tests cover the new behavior
- diff is readable in 5 minutes

**Open questions** (what needs human judgment):
- which existing components does this touch?
- does the approach have downstream implications?

## The Verification Step

A spec without verification is a plan, not a constraint. The verification step runs the acceptance tests and reports:
- what passed
- what failed
- what could not be evaluated

This is the difference between "the agent thinks it is done" and "the work is actually complete."

## Ralph Workflow and Spec-First Runs

Ralph Workflow is built around this pattern. It runs AI coding agents (Claude Code, Codex, OpenCode) against specs you define, executes verification automatically, and produces a morning-after receipt that names the diff, the check results, and the open questions.

The spec-first approach is not unique to Ralph. You can run it manually. But Ralph makes it the default mode instead of an afterthought.

## Getting Started

Write your first spec for an AI agent run:
1. Name the specific feature or change
2. List the constraints that must hold
3. Write one acceptance test or human-readable check
4. Define what "risky" looks like so the agent can call it out

Then run the agent and judge the result against the spec — not the transcript.

Try it with Ralph Workflow: [Ralph Workflow on Codeberg](https://codeberg.org/RalphWorkflow/Ralph-Workflow).
