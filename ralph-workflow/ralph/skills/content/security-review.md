# security-review

## Purpose
Security-review is the skill for checking changes against common application risks before they ship. It is especially important when a change affects credentials, input handling, filesystem access, subprocess execution, web access, or external integrations.

Security issues often hide in seemingly small changes. A deliberate review helps catch SSRF, injection, path traversal, unsafe defaults, and overly broad trust assumptions before they become user-facing problems.

## When To Use
- Any network, filesystem, or subprocess boundary changes.
- A feature handles secrets, auth, or untrusted input.
- A prompt or tool surface can affect external execution.
- A dependency or default path could expand attack surface.

## Key Steps / Approach
1. Identify the trust boundaries the change touches.
2. Check for obvious injection, traversal, and SSRF risks.
3. Prefer allowlists, narrow scopes, and explicit validation.
4. Keep defaults safe and avoid exposing credentialed behavior as baseline.
5. Verify the fix with tests or static checks that prove the risk is mitigated.

## Common Pitfalls
- Trusting unvalidated external data.
- Expanding access more than the feature requires.
- Treating security review as a last-minute checkbox.
