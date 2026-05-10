# dev.to Article: "I let AI code for 4 hours — here's the exact workflow"

**Tags:** ai, tools, productivity, python

**Title:** I Let AI Code for 4 Hours Unattended — Here's the Exact Workflow

---

Most AI coding agents are glorified autocomplete. You sit there, watch them work, and inter vene when they get stuck.

Ralph Workflow is different. It's designed for the scenario where you set it up before end of day, come back the next morning, and review commits.

Here's what I built and why.

## The Problem with AI Coding Agents

When you use Copilot or Claude Code interactively, you're the bottleneck. The agent can't make decisions without you. It asks questions. It wanders. It sometimes produces code that's completely wrong but looks right.

The fundamental issue: there's no separation between "writing code" and "reviewing code." Both happen in your head, in real-time.

## The Spec-First Approach

Ralph Workflow enforces a discipline: write the spec first. Not "build a login page" — write what the login page does, how it behaves, what happens on error, what the success state looks like.

```markdown
# SPEC.md

## Login Flow

### Requirements
- Email + password form
- Validate email format on blur
- Show inline errors below each field
- On success: redirect to /dashboard
- On failure: show "Invalid credentials" banner
- 3 failed attempts: lock for 5 minutes
- "Forgot password" link → /password-reset

### UI
- Centered card layout
- "Remember me" checkbox
- Submit button: "Sign in"
```

This is the contract. The AI develops against this spec. If the spec says "show inline errors," it has to show inline errors.

## The Loop

```
PLAN → DEVELOP → VERIFY → COMMIT → (repeat)
```

1. **Plan**: Planning agent (GPT-4o) reads the spec and writes a PLAN.md
2. **Develop**: Dev agent (Claude Code) writes code to satisfy the plan
3. **Verify**: Review agent (o1) checks code against the plan
4. **Commit**: If verification passes, commit with a message referencing the spec item

The loop continues until the spec is done or the token budget is exhausted.

## What You Actually Get

A git log where every commit is traceable to a spec item:

```
a3f2c1d Add password reset flow (SPEC.md#password-reset)
b7d4e2f Fix inline error display (SPEC.md#error-states)
c9a1b3e Implement remember-me (SPEC.md#ui)
```

No mystery commits. No "AI did something and I don't know what." You can review the spec item, look at the diff, and decide if it's right.

## The ROI Question

I ran this on a medium-complexity feature: a job application tracker with 12 spec items.

- Setup time: 20 minutes (writing the spec)
- Runtime: 4.5 hours unattended
- Result: 23 commits, all spec-traced, 2 issues caught by the verify step

That's 4.5 hours where I wasn't the bottleneck. I made dinner. I watched something. I slept.

## What's Still Hard

- Understanding business context ("why does this matter?")
- Making tradeoff calls without explicit criteria
- Novel problems that need creative solutions

What AI is actually good at: mechanical implementation against clear criteria. The spec gives it the criteria.

## The Tool

Ralph Workflow is open source (AGPL). It works with any OpenAI-compatible API. You configure agents per phase, set a token budget, and go.

The hosted version (coming soon) adds team analytics, shared run history, and one-click deploys. Because solo mode is fine. Team mode is where it gets interesting.

---

*Have you tried running AI agents for extended periods? What's worked and what hasn't?*
