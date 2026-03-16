# Performance: [What's slow or resource-heavy]

## Goal
<!-- What performance level should the system achieve? -->
[e.g., "Dashboard loads in <2 seconds at p95"]

## Current State
<!-- What's the performance problem? Include measurements -->
[e.g., "Dashboard p95 latency: 8.2s. Users abandon page. Root cause appears to be N+1 queries in the analytics widget."]

## Target Metrics
<!-- Specific, measurable targets -->
| Metric | Current | Target |
|--------|---------|--------|
| [e.g., "Page load p95"] | [e.g., "8.2s"] | [e.g., "<2s"] |
| [e.g., "API response p95"] | [e.g., "1.2s"] | [e.g., "<200ms"] |
| [e.g., "Memory usage"] | [e.g., "2.1GB"] | [e.g., "<500MB"] |

## Scope
<!-- What parts of the system are in scope? -->
[e.g., "Dashboard page: analytics widget, recent activity feed, summary cards. Backend: /api/dashboard endpoint."]

## Constraints (optional)
<!-- Limitations on the optimization approach -->
[e.g., "Cannot change data model" or "Must maintain backward compatibility" or "No additional infrastructure"]

## Acceptance
<!-- What must be true for this optimization to be complete? -->
- [ ] [e.g., "All target metrics met in production"]
- [ ] [e.g., "Performance verified with load testing"]
- [ ] [e.g., "No regression in functionality"]
- [ ] [e.g., "Monitoring dashboards updated"]
