# Refactor: [What you want improved]

## Problem
<!-- What's wrong with the current state? Why refactor now? -->
[e.g., "UserService is 2000 lines, impossible to test, and every change risks breaking something"]

## Goal
<!-- What architectural improvement do you want? -->
[e.g., "Split into focused services: UserAuthService, UserProfileService, UserPreferencesService"]

## Benefits
<!-- What will be better after this refactor? -->
- [e.g., "Each service can be tested in isolation"]
- [e.g., "Teams can work on different services without conflicts"]
- [e.g., "Easier to understand and onboard new developers"]

## Scope
<!-- What should change? What must NOT change? -->
- **Include:** [e.g., "UserService and direct dependencies"]
- **Exclude:** [e.g., "Public API contracts must stay identical"]

## Behavior Preservation
<!-- Any behaviors that MUST remain exactly the same? -->
[e.g., "All API responses must be byte-identical" or "All existing tests must pass without modification"]

## Context (optional)
<!-- Constraints or patterns to follow -->
[e.g., "Follow existing service patterns in /services/"]
