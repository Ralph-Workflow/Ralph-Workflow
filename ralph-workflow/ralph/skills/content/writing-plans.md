---
name: writing-plans
description: Use when planning a multi-step change before editing code
---

# Writing Plans

Build an executor-ready instruction set for the next implementation session.

Inspect the request and repository before drafting. A useful plan explains the
outcome, current behavior, the smallest safe change, and how to prove it.

Cover the whole arc in order: **Orient**, **Characterize**, **Change**, and
**Verify**. Do not skip characterization because work looks easy. Ground paths,
commands, and patterns in repository evidence; write a discovery step for an
honest unknown.

Use the plan format supplied by the active workflow. Its headings are optional
structure, not a checklist. Use stable `### [S-n] Title` steps when a downstream
consumer needs stable IDs. Keep commitments next to the work they describe,
state them once, and describe real risks and runnable completion evidence rather
than filling sections.

For a revision, preserve valid material, check feedback against the request and
repository, and update every affected commitment together. Prefer the standard
markdown artifact edit flow over replacing a whole plan; replace all only when
little of the draft remains useful.
