# brainstorming

## Purpose
Brainstorming is the skill for opening up solution space before narrowing it. It is useful when a problem has more than one plausible implementation path, when the user has given goals rather than exact steps, or when the repository has multiple patterns that could each reasonably solve the same requirement.

Good brainstorming reduces premature commitment. It helps you compare trade-offs, surface hidden constraints, and identify where a small design choice could create a larger maintenance burden later.

## When To Use
- The request is open-ended or ambiguous.
- Several implementation strategies look viable.
- You need to discover hidden requirements before coding.
- The task could affect architecture, UX, or developer workflow.

## Key Steps / Approach
1. Name the actual problem and list the core constraints.
2. Generate multiple candidate approaches without judging too early.
3. Compare each option on risk, testability, maintainability, and fit with the repo.
4. Eliminate options that add unnecessary complexity or weaken verification.
5. Choose the simplest approach that still satisfies the requirement set.

## Common Pitfalls
- Picking the first decent idea before checking alternatives.
- Ignoring constraints that make a clever solution fragile.
- Confusing brainstorming with implementation planning.
