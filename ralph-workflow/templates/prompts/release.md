# Release: [Version Number]

> **How to use this template:** This template is for preparing and executing releases. It covers version bumping, changelog generation, tagging, and release validation.

## Goal
[Clear description of what's being released]

**Tips for a good release goal:**
- ✅ "Release v2.0.0 with new authentication system"
- ✅ "Patch release v1.2.1 to fix critical security issue"
- ❌ "Do a release" (too vague)

**EXAMPLE:**
```markdown
## Goal
Prepare and execute release v1.5.0 including the new search feature, performance improvements, and bug fixes from the past 2 weeks of development.
```

## Questions to Consider
Before releasing, verify:

**Readiness:**
- Are all intended changes included?
- Are there unintended changes?
- Have all PRs been merged and tested?
- Is the changelog complete and accurate?

**Testing:**
- Has the release been tested in staging/pre-production?
- Have critical user paths been validated?
- Have rollback procedures been tested?
- Are there known issues or limitations?

**Communication:**
- Who needs to be notified? (team, users, stakeholders)
- What documentation needs updating?
- Are there breaking changes to communicate?
- Is there a migration guide if needed?

**Risk Assessment:**
- What's the worst case if something goes wrong?
- Can we rollback quickly if needed?
- Are there data migrations or schema changes?
- What's the deployment strategy? (blue-green, canary, rolling)

## Acceptance Checks
- [Version number bumped in all necessary files]
- [Changelog updated with all significant changes]
- [Release notes prepared]
- [Git tag created and pushed]
- [Release built and tested]
- [Documentation updated]
- [Announcement/communication sent]

## Release Details

### Version
[Semantic version: major.minor.patch]

### Type of Release
- [ ] Major (breaking changes, vX.0.0)
- [ ] Minor (new features, v1.X.0)
- [ ] Patch (bug fixes, v1.1.X)

### Changelog
[Summary of changes since last release]

**EXAMPLE:**
```markdown
### Changelog

**Added:**
- User authentication with OAuth2 support
- Search with filters for date and category

**Changed:**
- Improved performance of dashboard loading
- Updated dependencies to latest stable versions

**Fixed:**
- Fixed crash when uploading files with special characters
- Fixed memory leak in background worker

**Breaking Changes:**
- Removed legacy API endpoints (v1)
- Changed database schema for user sessions
```

### Breaking Changes
[List any breaking changes and migration steps]

**EXAMPLE:**
```markdown
### Breaking Changes

**Database Migration Required:**
Run `bun run migrate` after deployment to update user_sessions table.

**API Changes:**
- `/api/v1/users` is removed, use `/api/v2/users` instead
- Response format for `/api/v2/users` now includes `email_verified` field
```

## Code Quality Specifications

**Release Best Practices:**
- Use semantic versioning (MAJOR.MINOR.PATCH)
- Maintain an accurate changelog
- Tag releases in git with annotations
- Test releases in staging before production
- Have a rollback plan for each release
- Document breaking changes clearly
- Communicate changes to all stakeholders

**Pre-Release Checklist:**
- All tests pass
- No critical or high-severity bugs
- Documentation is updated
- Dependencies are up-to-date
- Security vulnerabilities are addressed
- Performance is acceptable
- Monitoring and alerts are configured

**Post-Release:**
- Monitor for errors and anomalies
- Be ready to rollback if needed
- Gather feedback from users
- Document any issues encountered
- Update metrics and dashboards

**Tagging Convention:**
```bash
# Create annotated tag
git tag -a v1.5.0 -m "Release v1.5.0: New search feature and performance improvements"

# Push tag to remote
git push origin v1.5.0
```
