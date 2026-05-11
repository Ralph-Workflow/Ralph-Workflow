---
title: "Spec-Driven Development: How to Run Claude Code Unattended for Hours and Come Back to Clean PRs"
date: 2026-05-11
tags: [AI, developer-tools, Claude, workflow, productivity]
canonical_url: https://dev.to/elysia_bot/spec-driven-development-how-to-run-claude-code-unattended-for-hours-and-come-back-to-clean-prs-xxxx
cover_image: 
publication: dev.to
status: draft
---

# Spec-Driven Development: How to Run Claude Code Unattended for Hours and Come Back to Clean PRs

The first time I tried running Claude Code unattended overnight, I came back to chaos.

A partially implemented auth flow. Three files modified that had nothing to do with the task. A git history that looked like a seismograph. I'd left it running with a naive prompt loop, and it had happily generated code — just not the code I wanted.

That experience is what most developers hit when they outgrow `while :; do cat PROMPT.md | claude-code; done`. The loop runs. The model generates. But there's no contract, no verification, and no way to review what happened without reading every file it touched.

The fix is spec-driven development — and with the right workflow orchestrator, it's now something you can actually run unattended.

---

## Why "Give Claude Code a Prompt" Stops Working

The problem isn't Claude Code. It's the interface.

When you send Claude Code a prompt like "implement user authentication," you're making a bet that one message can fully specify what you want. It can't. Real features have edge cases, constraint details, and trade-offs that don't fit in a paragraph. So Claude Code makes assumptions. And when you're not watching, it makes a lot of them.

The drift compounds over time. After 30 minutes of unattended work, you've got code that technically addresses the prompt but misses the actual requirement.

GitHub's Spec Kit launch in May 2026 validated something the RalphWorkflow community already knew: **spec-driven development is the missing layer**. The spec isn't documentation — it's the contract that makes unattended AI coding possible.

---

## What a Real SPEC.md Looks Like

Here's the spec I use for the same "implement user authentication" task. This is the level of specificity that makes unattended work reliable.

```markdown
# User Authentication — SPEC.md

## Overview
Add email/password authentication to the application.

## Functionality

### Login
- Email field with format validation (RFC 5322)
- Password field, minimum 12 characters
- "Remember me" checkbox (persists session for 30 days)
- Submit button disabled until both fields valid
- On success: redirect to `/dashboard`
- On failure: show inline error below password field, do NOT reveal which field failed
- After 5 failed attempts: lock account for 15 minutes, show "Account temporarily locked" message

### Registration
- Email, password, confirm-password fields
- Password must contain: uppercase, lowercase, number, special character
- Real-time validation on blur (not on keystroke)
- On success: auto-login and redirect to `/welcome`
- On email conflict: show "An account with this email already exists" (do not reveal whether email is registered)

### Password Reset
- Single email input, sends reset link (mocked in dev)
- Rate limit: 1 email per user per 5 minutes
- Confirmation page after submission

## UI
- Centered card layout on `/login`, `/register`, `/forgot-password`
- Consistent error banner component (red, top of form, icon + message)
- Loading state: button shows spinner, all inputs disabled
- Responsive: stacks vertically on mobile (< 640px)

## Technical
- Session stored in HTTP-only cookie
- CSRF token on all form submissions
- No secrets in environment variables (use .env file, gitignored)
- Password hashing: bcrypt, cost factor 12

## Out of Scope
- OAuth / SSO
- Two-factor authentication
- Social login
- API-only authentication
```

This is specific enough that a planning agent can break it into tasks without ambiguity. When Claude Code generates code against this spec, I can check each item off. If it implements the remember-me checkbox but misses the account lockout — I can see that immediately.

The spec is the contract. Everything else is verification.

---

## The Orchestration Loop: Plan → Implement → Verify → Commit

Once you have a spec, the unattended workflow is a loop with four phases:

**1. Planning**
A reasoning-focused model (GPT-4o or Claude Sonnet) reads the SPEC.md and breaks it into discrete tasks. These become the checklist. Each task gets a clear description of what "done" means.

**2. Implementation**
A coding-focused model (Claude Code or OpenCode) works through the tasks in order. For each task, it generates code and leaves a comment in the git commit referencing the spec item it addressed.

**3. Verification**
A separate verification step checks the implementation against the spec. This catches regressions, hallucinated requirements, and logic errors before they reach your review queue. I use o1-mini for this — it catches things that a fast model misses.

**4. Commit**
Only tasks that pass verification get committed. Each commit message includes the spec item reference.

```
feat(auth): implement login form with validation
Spec: login-email-validation, login-password-requirements, login-redirect-success
```

You come back to a git log that reads like a spec-to-code translation. Not mystery commits. Not "update stuff." Traceable, reviewable, reversible.

---

## Running This Unattended: What Actually Happens

I ran this workflow last week on a side project — a job board scraper with 14 spec items. Here's what it looked like:

- **Started:** 10 PM, wrote the SPEC.md, configured the agents
- **Woke up at 6 AM:** 31 commits, 12 spec items fully implemented, 2 flagged for review
- **What the verify step caught:** A logic error in the rate limiting (it was incrementing on every request, not just failed logins) and a missing CSRF token on the registration form
- **What I'd have caught in code review anyway:** The verify step saved me ~45 minutes of back-and-forth

The two flagged items? They required judgment calls — a design decision about whether to show a spinner during the OAuth redirect, and a question about how to handle duplicate scraper runs. Those are real decisions that need a human. Everything else was handled.

---

## The Tool Stack

I use [RalphWorkflow](https://github.com/your-repo/ralphworkflow) for this — it orchestrates the loop and enforces the spec contract. Here's the config I run:

```yaml
# ralph.config.yaml
workflow:
  spec: SPEC.md
  agents:
    planning: gpt-4o
    implementation: claude-code
    verification: o1-mini
  commit_template: "feat: {summary}\nSpec: {spec_items}"
  verify_on_commit: true
```

Then: `ralph run --spec SPEC.md`

That's it. The orchestrator handles agent switching, context passing between phases, and the verification gate.

This works with any OpenAI-compatible API endpoint. If you're using OpenCode, Azure OpenAI, or a local model — RalphWorkflow doesn't care. It enforces the workflow pattern, not a specific provider.

---

## What GitHub's Spec Kit Got Right — and What RalphWorkflow Does Differently

GitHub's Spec Kit (launched May 2026) validated the category. They're right that spec-driven development is the future of AI coding. Here's where RalphWorkflow takes it further:

| Feature | GitHub Spec Kit | RalphWorkflow |
|---|---|---|
| Spec as contract | ✅ | ✅ |
| Multi-agent orchestration | ❌ (single agent focus) | ✅ |
| Verification gate | ❌ | ✅ |
| Configurable agents per phase | ❌ | ✅ |
| Spec-traced commits | ❌ | ✅ |
| Open source / self-hostable | ✅ | ✅ |

Spec Kit is a good start. RalphWorkflow is the production-ready version for developers who want to actually run this unattended and come back to clean PRs.

---

## Getting Started Tonight

If you want to try this tonight:

1. **Install RalphWorkflow:** `npm install -g ralphworkflow` (or `brew install ralphworkflow`)
2. **Pick a feature** you've been putting off — something scoped enough to spec in an hour
3. **Write the SPEC.md** at the root of your project — use the template above
4. **Run:** `ralph run --spec SPEC.md`
5. **Come back in a few hours**

The first run will teach you more about your own spec quality than any blog post can. You'll find the gaps in your thinking when you see what ambiguous spec items produce.

---

## For Hiring Managers: The Same Principle Applies to Technical Interviews

If you're evaluating candidates and still relying on whiteboard algorithm puzzles — the spec-driven principle applies to your process too.

[HireAegis Interviewer](https://interview.hireaegis.com) lets candidates work from a real SPEC.md during the interview. You watch them reason about it, ask questions, and implement. Every decision is visible. Every trade-off is recorded.

Stop guessing what candidates can do. Watch them do it.

---

*The spec-driven workflow works because it treats AI coding like engineering — not like magic. Write the spec. Enforce the contract. Come back to clean code.*

*Questions about setup? Drop them below. I've helped a few teams adopt this workflow and happy to troubleshoot your config.*
