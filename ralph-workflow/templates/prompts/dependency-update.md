# Dependency Update: [Dependency Name and Version]

> **How to use this template:** Use this template when updating dependencies, upgrading major versions, or managing breaking changes. Focus on compatibility testing and migration guides.

## Goal
[EXAMPLE: Upgrade React from v17 to v18 with new concurrent features and automatic batching]

## Questions to Consider

**Impact Assessment:**
- What are the breaking changes in the new version?
- What features/bugfixes do we gain from the update?
- What is the scope of the change? (minor bump, major version rewrite)
- How many places in the codebase use this dependency?
- Are there transitive dependencies that also need updating?

**Compatibility:**
- Are there API changes that require code modifications?
- Are there behavior changes that affect functionality?
- Are there deprecated features we're using?
- Are there new dependencies required?
- Are there peer dependency conflicts?

**Testing:**
- What tests exist to verify the update?
- What manual testing is needed?
- Are there known issues with the new version?
- Is there a migration guide to follow?

## Update Plan

**Phase 1: Preparation**
1. [Read changelog and migration guide]
2. [Identify breaking changes and required code changes]
3. [Check for known issues in the new version]
4. [Create a feature branch for the update]

**Phase 2: Dependency Update**
[Describe the update process]
[EXAMPLE:
1. Update package.json to react@18.2.0
2. Run bun install to update lockfile
3. Update ReactDOM.createRoot() for new API
4. Update type definitions if using TypeScript
]

**Phase 3: Code Migration**
[Describe required code changes]
[EXAMPLE:
1. Replace ReactDOM.render() with createRoot()
2. Remove deprecated lifecycle methods
3. Update state patterns for new concurrent features
4. Fix any TypeScript type errors
]

**Phase 4: Testing and Verification**
1. [Run all tests to ensure no regressions]
2. [Run manual testing for affected functionality]
3. [Check browser console for warnings]
4. [Performance test to ensure no degradation]

## Update Specification

**Dependency:**
[Name and version being updated]
[EXAMPLE: react: 17.0.2 -> 18.2.0]

**Type of Update:**
- [ ] Minor/patch version (low risk)
- [ ] Major version (high risk, breaking changes)
- [ ] Security update (urgent)
- [ ] Feature update (optional)

**Breaking Changes:**
[List breaking changes from changelog]
[EXAMPLE:
- ReactDOM.render() deprecated, use createRoot()
- Automatic batching may affect timing of side effects
- StrictMode effects double-invoked in dev
]

**Required Code Changes:**
[List code changes needed]
[EXAMPLE:
- Update src/main.tsx to use createRoot()
- Remove componentWillMount() lifecycle methods
- Update useEffect() dependencies to fix batching issues
]

## Acceptance
- [Dependency successfully updated to target version]
- [All breaking changes addressed]
- [All tests pass with no regressions]
- [Manual testing completed for affected functionality]
- [No console warnings or errors]
- [Performance is not degraded]
- [Documentation updated if API changed]
- [Changelog updated with dependency version]

## Code Quality Specifications

Write clean, maintainable code:
- Single responsibility: one reason to change per function/class
- Small units: functions < 30 lines, classes < 300 lines
- Clear names that reveal intent
- Early returns; minimize nesting depth
- Explicit error handling; no silent failures
- No magic numbers; extract constants
- DRY: extract duplicated logic
- Validate at boundaries; trust internal data
- Test behavior, not implementation

**Dependency Update Best Practices:**
- **Read Changelog:** Always read the full changelog for breaking changes
- **Check Security:** Use tools like `bun pm audit` or `cargo audit` for vulnerabilities
- **Update Lockfile:** Commit the updated lockfile (package-lock.json, Cargo.lock)
- **One at a Time:** Update one major dependency at a time, not multiple
- **Test Thoroughly:** Major version updates require extensive testing
- **Peer Dependencies:** Check for peer dependency conflicts
- **Transitive Updates:** Be aware of transitive dependency updates
- **Rollback Plan:** Know how to rollback if the update causes issues
- **Monitor:** Watch for issues after deployment

**Testing Strategy for Dependency Updates:**
- Run full test suite before and after update
- Add tests for any new features being adopted
- Test edge cases that may be affected by behavior changes
- Run integration tests with external dependencies
- Check for deprecated API usage
- Verify type definitions if using TypeScript
- Manual testing for UI/UX changes
- Load testing for performance-critical updates

**Common Pitfalls:**
- Skipping the changelog and missing breaking changes
- Updating multiple major dependencies at once
- Not testing enough (assuming semver guarantees compatibility)
- Forgetting to update documentation
- Not monitoring for issues after deployment
- Ignoring deprecation warnings
