# Active prompt-template revision

## Goal

Make every production-reachable Jinja prompt concise, purposeful, and explicit. Remove top-level prompt templates that have no production runtime path.

## Scope

Revise every top-level template selected by the default policy or directly loaded by production Python code, together with the shared partials those templates include.

Delete `review.jinja`, `review_analysis.jinja`, and `fix_mode.jinja` after tests prove that production code and the default policy cannot select them. Retain fallback, continuation, worker, conflict-resolution, policy-remediation, and simplified-commit templates because production paths load them directly.

## Prompt structure

Each prompt will present information in this order when applicable:

1. Objective: the single outcome of the phase.
2. Inputs: authoritative task, artifact, state, and policy context.
3. Actions: ordered work needed to produce the outcome.
4. Completion contract: required artifact, validation, submission, and terminal signal.

Keep necessary safety, verification, tool, artifact-schema, and unattended-execution constraints. Remove repeated motivation, conversational filler, redundant prohibitions, and instructions already guaranteed by a shared partial or runtime contract. Prefer positive output recipes and observable conditions over long prohibition lists.

## Compatibility

Do not change template variables, artifact schemas, tool names, phase transitions, fallback behavior, or runtime capabilities. Workspace overrides remain supported. Prompt changes affect instruction wording and ordering only, except for deletion of templates proven unreachable in production.

## Tests

Before changing templates, add or update black-box checks that:

- derive the production-reachable template set from default policy and direct production loaders;
- prove deleted templates have no production selection path;
- retain each active template's critical objective and completion contract;
- render every active template successfully with representative variables;
- reject avoidable duplication or obsolete template references where a stable behavioral assertion is possible.

Run focused prompt tests during red-green-refactor, then the repository's prompt integrity and verification-prompt evaluation checks. Finish with `make verify`; every surfaced failure must be fixed.

## Independent review

After implementation and before final verification, run parallel, read-only agent reviews over disjoint prompt groups. Each reviewer independently checks its rendered prompts against the same rubric: one phase objective, authoritative context, ordered actions, explicit output contract, no redundant instruction, no filler, and no loss of safety or runtime requirements. A separate reviewer audits production reachability and every proposed deletion. Resolve all substantive findings, then repeat the affected review before `make verify`.

## Research basis

Apply current official OpenAI prompt-engineering guidance: give clear instructions, provide relevant context, separate instructions from supplied content, specify the desired output, and evaluate prompt changes rather than relying on intuition. Use the repository's own prompt research and artifact contracts where they impose stricter requirements.

Source: [OpenAI prompt engineering guide](https://developers.openai.com/api/docs/guides/prompt-engineering).
