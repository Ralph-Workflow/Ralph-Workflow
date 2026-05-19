# Ralph Workflow vs OpenCode

If you already use OpenCode, the simplest difference is this:

- **OpenCode** is the coding-agent interface and provider-routing layer you drive directly.
- **Ralph Workflow** is a **free and open-source** orchestration CLI that runs OpenCode or another supported coding agent **on your own machine** and hands back a **reviewable result** after an unattended run.

That makes Ralph Workflow a fit for developers and technical teams with work that is **too big to babysit and too risky to trust blindly**.

Why try it now? Because you do not need to replace OpenCode to use it. Keep your current OpenCode setup, hand Ralph Workflow one real backlog task tonight, and decide tomorrow whether the result is something you would actually merge.

## The core difference

OpenCode is strongest when you want flexible provider access and a direct agent surface you can steer yourself.

Ralph Workflow is strongest when you want the morning-after handoff to be the product.

You write a bounded spec in `PROMPT.md`, Ralph Workflow runs planning, development, verification, and review as one unattended flow, and you come back to a diff, checks, logs, and artifacts you can inspect like normal engineering work.

## Choose Ralph Workflow when

Ralph Workflow is usually the better fit when you want to:

- keep OpenCode as your agent path but stop babysitting the session
- hand off a real backlog task and review it later
- wake up to a bounded diff instead of a long transcript
- route planning, implementation, and review through different agents when needed
- judge the result with one merge / no-merge decision

## Choose OpenCode when

OpenCode is usually the better fit when you want to:

- stay in an interactive coding loop
- switch providers live while you are still steering the work
- explore a codebase conversationally
- make small edits while you remain present
- tune model/provider choices more than the handoff itself

## Best next step if this sounds like the missing piece

Use **Codeberg** as the main public home:

- **Inspect the source on Codeberg:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **Star / watch / fork on Codeberg:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **Report first-run friction on Codeberg:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>
- **Use GitHub only as the mirror:** <https://github.com/Ralph-Workflow/Ralph-Workflow>
