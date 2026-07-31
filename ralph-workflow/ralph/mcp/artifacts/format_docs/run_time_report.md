# run_time_report artifact format

A runtime-generated summary of one pipeline run. It is written to
`.agent/artifacts/run_time_report.md`; agents do not submit it with
`ralph_submit_md_artifact`.

The report has frontmatter `type: run_time_report`, `outcome:`,
`elapsed_seconds:`, and `final_phase:`, plus these sections: `Summary`,
`Timing`, `Phases`, `Slowest Steps`, and `Signals`. Each section has stable-ID
list items. Its fixed shape makes runs comparable without reading logs.

```markdown
---
type: run_time_report
outcome: completed
elapsed_seconds: 12.500
final_phase: development
---

## Summary
- [SUM-1] completed; total wall-clock time was 12.500s.

## Timing
- [T-1] Total wall-clock time: 12.500s.
- [T-2] Agent-controlled time: unavailable; the runtime does not yet classify it.
- [T-3] Imposed time: unavailable; the runtime does not yet classify waits.
- [T-4] Imposed-time rise: unavailable until two classified reports exist.

## Phases
- [P-1] Final phase: development.
- [P-2] development: 9s.
- [P-3] planning: 3s.

## Slowest Steps
- [SS-1] development: 9s.
- [SS-2] planning: 3s.

## Signals
- [SG-1] Agent calls: 1; retries: 0; continuations: 0; fallbacks: 0.
```

The reporting budget is 1,600 characters. Per-phase values use the maximum
elapsed time for each phase across iterations; only the six slowest phases are
listed. Unavailable telemetry is stated rather than guessed.

See `.agent/artifact-formats/examples/run_time_report.md` for a complete
example.
