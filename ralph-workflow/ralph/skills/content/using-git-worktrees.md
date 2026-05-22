# using-git-worktrees

## Purpose
Using-git-worktrees is the skill for isolating risky or parallel work in separate checkout directories. It reduces interference between long-lived changes and keeps unrelated work from colliding in the same working tree.

Worktrees are especially useful when you need to explore alternatives, keep a clean branch around for comparison, or let separate tasks progress without sharing filesystem state.

## When To Use
- You need isolated branches for risky work.
- Parallel tasks would interfere if run in one checkout.
- You want to keep a clean main worktree while experimenting.
- Multiple agents or sessions need separate sandboxes.

## Key Steps / Approach
1. Create a dedicated worktree for the isolated task.
2. Keep the worktree's scope narrow and clearly named.
3. Avoid cross-worktree file sharing unless explicitly intended.
4. Remove the worktree when the task is done to keep the repo tidy.
5. Treat worktree boundaries as real isolation, not just a convenience.

## Common Pitfalls
- Using a worktree without a clear purpose.
- Letting multiple worktrees drift into the same file set.
- Forgetting to clean up abandoned isolated checkouts.
