# receiving-code-review

## Purpose
Receiving-code-review is the discipline of turning review comments into better code without becoming defensive or making unrelated changes. It keeps feedback tied to the issue that triggered it and helps preserve the original intent of the change.

Good review intake improves quality and reinforces trust. It also keeps the response small and targeted, which is important when a reviewer has pointed to a specific bug, missing test, or maintainability concern.

## When To Use
- A reviewer has left actionable comments.
- A merge request needs follow-up changes.
- You must distinguish real defects from style preferences.
- The next step is correction rather than new feature work.

## Key Steps / Approach
1. Read the review carefully and classify each comment by severity and scope.
2. Fix the concrete issue that was raised before adding anything else.
3. Keep the change as small as possible while preserving the intended behavior.
4. Re-run the relevant checks after each round of edits.
5. Respond with evidence, not argument, when the issue is resolved.

## Common Pitfalls
- Over-editing in response to a small review note.
- Arguing with a valid defect instead of fixing it.
- Ignoring the evidence the reviewer surfaced.
