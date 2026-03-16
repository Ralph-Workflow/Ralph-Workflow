# Debug: [Brief description of the issue]

## Goal
<!-- What should be accomplished? -->
[e.g., "Identify root cause of production OOM crashes and provide a fix"]

## Symptoms
<!-- What's being observed? Be specific -->
[e.g., "Production servers crash every 2-3 hours. Kubernetes restarts pods. Users see 502 errors during restart."]

## Environment
<!-- Where does this occur? -->
[e.g., "Production only. Cannot reproduce locally or in staging."]

## Evidence
<!-- Error messages, logs, metrics, stack traces -->
```
[e.g., "java.lang.OutOfMemoryError: Java heap space
  at com.example.CacheService.load(CacheService.java:142)"]
```

## Timeline (optional)
<!-- When did this start? What changed? -->
[e.g., "Started Tuesday after deploy of commit abc123. That commit added the new caching layer."]

## Hypotheses (optional)
<!-- Initial theories to investigate -->
- [e.g., "Cache is unbounded and grows until OOM"]
- [e.g., "Memory leak in new connection pooling"]

## Acceptance
<!-- What must be true for this debug session to be complete? -->
- [ ] [e.g., "Root cause identified with evidence"]
- [ ] [e.g., "Fix implemented and verified"]
- [ ] [e.g., "Issue does not recur after fix deployed"]
