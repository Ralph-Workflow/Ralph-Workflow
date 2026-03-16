# Migration: [What schema change is needed]

## Goal
<!-- What capability does this schema change enable? -->
[e.g., "Support email verification feature by tracking when users verify their email"]

## Schema Change
<!-- What's changing? -->
```sql
-- Example
ALTER TABLE users ADD COLUMN verified_at TIMESTAMP NULL;
CREATE INDEX idx_users_verified_at ON users(verified_at);
```

## Data Migration (optional)
<!-- Does existing data need to be transformed? -->
[e.g., "Backfill: Set verified_at = created_at for existing users (treat as verified)"]

## Rollback Plan
<!-- How do we undo this if needed? -->
```sql
-- Example
ALTER TABLE users DROP COLUMN verified_at;
```

## Deployment Constraints
<!-- How must this be deployed? -->
- [e.g., "Zero-downtime required (online migration)"]
- [e.g., "Can tolerate 5-minute maintenance window"]
- [e.g., "Must be backward-compatible with current app version"]

## Dependencies (optional)
<!-- What depends on or is depended upon by this change? -->
[e.g., "App deploy must happen after migration" or "Migration depends on previous migration X"]

## Acceptance
<!-- What must be true for this migration to be complete? -->
- [ ] [e.g., "Migration runs successfully in all environments"]
- [ ] [e.g., "Rollback tested and documented"]
- [ ] [e.g., "Data migration completed correctly"]
- [ ] [e.g., "Application works with new schema"]
