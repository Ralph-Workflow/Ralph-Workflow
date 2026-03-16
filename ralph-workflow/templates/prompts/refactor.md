# Refactor: [What you want improved]

## Goal
<!-- What architectural improvement do you want to achieve? -->
[e.g., "Decompose UserService into cohesive, single-responsibility modules"]

## Current State
<!-- What's the current architecture and why is it problematic? -->
[e.g., "UserService is a 2000-line monolith handling auth, profiles, preferences, and notifications. Changes to one concern risk breaking others. Testing requires mocking the entire service."]

## Target State
<!-- What should the architecture look like after? -->
[e.g., "Four focused modules:
- UserAuthModule: login, logout, password reset
- UserProfileModule: profile CRUD, avatar handling
- UserPreferencesModule: settings, notification preferences  
- Each module has clear boundaries and can be tested independently"]

## Invariants
<!-- What must NOT change? -->
- [e.g., "Public API signatures remain identical"]
- [e.g., "All existing tests pass without modification"]
- [e.g., "No database schema changes"]
- [e.g., "No changes to wire format (JSON responses)"]

## Migration Path (optional)
<!-- If this can't be done atomically, what are the phases? -->
[e.g., "Phase 1: Extract UserAuthModule. Phase 2: Extract UserProfileModule. Phase 3: Clean up remaining code."]

## Acceptance
<!-- What must be true for this refactor to be complete? -->
- [ ] [e.g., "Architecture matches Target State"]
- [ ] [e.g., "All invariants are preserved"]
- [ ] [e.g., "Each module can be tested in isolation"]
- [ ] [e.g., "No regression in system behavior"]
