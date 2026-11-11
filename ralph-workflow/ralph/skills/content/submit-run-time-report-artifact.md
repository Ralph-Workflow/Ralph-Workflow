---
name: submit-run-time-report-artifact
description: Use when you need the shape of the run time report the pipeline runtime writes after a run, or when you are about to author a run_time_report and need to know the runtime already writes it
version: 2.2.0
---

# submit-run-time-report-artifact

Read `.agent/artifact-formats/run_time_report.md`.

This skill is a REFERENCE for the report the RUNTIME writes at the end of a
pipeline run to `.agent/artifacts/run_time_report.md`. The runtime consumes
the report; an agent never authors it.

## Do Not Submit

Do NOT submit this artifact with `ralph_submit_md_artifact`; the runtime
writes the report itself, and no agent submission is expected for it.

## Shape

Frontmatter: `type: run_time_report`, `outcome:`, `elapsed_seconds:`, and
`final_phase:`.

Sections: `Summary`, `Timing`, `Phases`, `Slowest Steps`, and `Signals`,
plus the optional `Memory Findings` and `Cycle Timebox` sections. Each
section carries stable-ID list items, so runs stay comparable without
reading logs.

The fixed shape is deliberate: a non-completed run whose
`last_failure_category` is `artifact_validation` adds the `SG-2` validation
failure signal, while completed runs and the other failure categories do
not. Per-phase values report the maximum elapsed time for that phase across
the run's iterations, and only the six slowest phases are listed. Unavailable
telemetry is stated rather than guessed, and the optional `Memory Findings`
section stays unset unless the run recorded memory findings.
