# finishing-a-development-branch

## Purpose
Finishing-a-development-branch is the skill for wrapping up implementation responsibly. It focuses on cleanup, verification, and handoff decisions so the work can be merged or paused without leaving loose ends behind.

A branch is not done just because the main feature seems to work. You still need to check tests, ensure docs or prompts are current, and make sure the repo is in a state someone else can trust.

## When To Use
- The requested work is functionally complete.
- Verification has passed or has been narrowed to pre-existing issues.
- You need to decide whether to hand off, merge, or pause.
- The remaining work is cleanup rather than new scope.

## Key Steps / Approach
1. Run the final verification commands for the touched area.
2. Confirm no accidental changes or dead code remain.
3. Update user-facing docs when behavior or commands changed.
4. Summarize proof clearly for the next maintainer or reviewer.
5. Leave the branch easy to continue or merge.

## Common Pitfalls
- Rushing to completion before cleanup.
- Leaving stale docs or test debt behind.
- Treating a partially verified change as finished.
