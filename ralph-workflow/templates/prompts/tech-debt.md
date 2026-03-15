# Tech Debt: [Brief description]

## Problem
<!-- What's the technical debt? Be specific -->
[e.g., "Authentication system uses MD5 hashing, a deprecated crypto library with known vulnerabilities"]

## Impact
<!-- Why does this matter NOW? What's the cost of not fixing it? -->
- [e.g., "Security vulnerability that could be exploited"]
- [e.g., "Blocks upgrading to Node 20 which we need for other features"]
- [e.g., "Every new auth feature takes 3x longer due to workarounds"]

## Scope
<!-- How much of the codebase is affected? -->
[e.g., "All user-facing auth: login, signup, password reset, session management (~20 files)"]

## Success Criteria
<!-- How do you know the debt is paid? -->
- [e.g., "All passwords use bcrypt with cost factor 12"]
- [e.g., "No references to old crypto library remain"]
- [e.g., "Security scan passes with no auth-related findings"]

## Constraints (optional)
<!-- Limitations on how this can be addressed -->
[e.g., "Can't break existing sessions" or "Must be incremental, not big-bang"]

## Context (optional)
<!-- How did we get here? Related decisions -->
[e.g., "Originally implemented in 2015 when MD5 was still acceptable"]
