# Docs route repair review — 2026-05-23

Reviewed in required order:
1. `README.md`
2. `START_HERE.md`
3. `docs/README.md`

## What changed
- Tightened the README route labels so the top-level path now explicitly tells evaluators to choose one real first task and run the default workflow.
- Rewrote the `START_HERE.md` first screen to foreground the simple Ralph-loop core, composition into planning/implementation/verification, and the strong default workflow users can run as-is before building on top.
- Rewrote the `docs/README.md` first screen to keep the same positioning hierarchy and added a direct instruction to start with the default workflow first.
- Rewrote the `docs/first-task-guide.md` first screen so the first-task asset explains the product as an AI agent orchestrator with a simple core, composable workflows, and a strong default path.

## Why it belongs on these surfaces
- The current bottleneck is `distribution_and_message_to_primary_repo_conversion`, so the highest-leverage internal repair is the evaluator path people hit after arriving at the repo.
- README, START_HERE, and the docs map are the top-level conversion route. If they drift into weaker framing, every traffic source pays the cost.
- The first-task guide is the most important proof-adjacent page in that route, so its first screen needs to reinforce the same product story instead of sliding back into older proof-first framing.

## What was pruned / shortened / merged
- Pruned weaker route labels like `shortest honest first run` and `curated docs switchboard` in favor of more action-oriented next steps.
- Shortened the START_HERE framing by removing softer wording about customization timing and replacing it with a clearer `run the default workflow first` instruction.
- No major page removals or merges were needed in this pass; the fix was message hierarchy, not page count.

## Whether duplication was reduced
- Yes. The top-level surfaces now repeat the same core hierarchy more consistently: simple core first, composition second, strong default workflow third, customization later.
- That reduces the previous drift where different entry points described the product in slightly different ways.

## Why the top-level experience is better now
- A repo visitor now gets a clearer answer to what Ralph Workflow is, who it is for, why it is different, and what to do next.
- The route more directly pushes evaluators toward one real first task and the default workflow, which is closer to adoption than abstract orchestration language.
- This should improve conversion quality for Codeberg-directed traffic without waiting on a new external distribution lane.
