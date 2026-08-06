---
name: writing-plans
description: Use when planning a multi-step change before editing code
---

# Writing Plans

Inspect the request and repository before drafting. A plan is the next executor's instruction set, not a summary.

Cover the work in order: **Orient**, **Characterize**, **Change**, and **Verify**. Ground paths, commands, and patterns in repository evidence; write a discovery step for an honest unknown.

Use stable `### [S-n] Title` steps. Each step has a purpose and one allowed `Type`: `file_change`, `file_create`, `file_delete`, `refactor`, `config_change`, `discovery`, or `verify`. Work steps name `Files`, a concrete `Verify`, and observable `Expect`; verification and discovery steps carry their required proof or inspectable location. Add dependencies only where ordering exists.

For a revision, repair every referenced finding in place while preserving material that still satisfies the current contract. Prefer the standard markdown artifact edit flow over replacing a whole plan; replace all only when little of the draft remains useful.
