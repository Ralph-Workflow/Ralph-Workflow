# Tech Debt: [Brief description]

## Goal
<!-- What should the system state be after addressing this debt? -->
[e.g., "Authentication uses bcrypt with modern security parameters, eliminating CVE exposure"]

## Current State
<!-- What's the technical debt and why is it problematic? -->
[e.g., "Auth uses MD5 hashing from a deprecated library with known vulnerabilities (CVE-2023-XXXX). Library is unmaintained and blocks Node.js upgrade."]

## Business Impact
<!-- Why must this be addressed now? -->
- [e.g., "Security: Known vulnerability exploitable in the wild"]
- [e.g., "Velocity: Blocks Node 20 upgrade needed for other features"]
- [e.g., "Maintenance: Workarounds add 3x development time for auth features"]

## Target State
<!-- What should the architecture/implementation look like after? -->
[e.g., "All password hashing uses bcrypt with cost factor 12. No references to legacy crypto library. Security scans pass with no auth findings."]

## Scope
<!-- What components are affected? -->
[e.g., "Auth module: login, signup, password reset, session management. ~20 files across backend and shared utilities."]

## Constraints (optional)
<!-- Limitations on the approach -->
[e.g., "Cannot invalidate existing sessions" or "Must be incremental, not big-bang"]

## Acceptance
<!-- What must be true for this debt to be considered resolved? -->
- [ ] [e.g., "System matches Target State"]
- [ ] [e.g., "No references to deprecated library remain"]
- [ ] [e.g., "Security scan passes"]
- [ ] [e.g., "No regression in functionality"]
