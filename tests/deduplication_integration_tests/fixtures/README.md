# Deduplication Test Fixtures

This directory contains curated log snippets from real `.agent/logs/` files that demonstrate problematic deduplication patterns.

## Fixture Categories

### 1. Snapshot Glitches
- **Pattern**: Agent sends full accumulated content instead of incremental delta
- **Example**: `snapshot_glitch.json`
- **Expected**: Deduplicator detects strong overlap (>30 chars, >50% ratio) and extracts only new portion

### 2. Consecutive Duplicates
- **Pattern**: Same delta sent 3+ times consecutively (resend glitch)
- **Example**: `consecutive_duplicate.json`
- **Expected**: "3 strikes" heuristic activates and filters duplicates

### 3. Boundary Edge Cases
- **Pattern**: Overlaps ending mid-word/mid-sentence
- **Example**: `boundary_edge_case.json`
- **Expected**: Deduplication only occurs at safe boundaries (whitespace, punctuation, newline)

### 4. Intentional Repetition
- **Pattern**: User intends repetition (e.g., "echo echo echo")
- **Example**: `intentional_repetition.json`
- **Expected**: Short tokens preserved, weak overlap not deduplicated

## Adding New Fixtures

1. Extract problematic pattern from `.agent/logs/*.log`
2. Sanitize/redact sensitive content
3. Add descriptive filename following pattern: `{category}_{description}.json`
4. Update this README with expected behavior

## Fixture Format

Each fixture should be a valid NDJSON file matching the Claude CLI stream event format:

```json
{"type":"stream_event","event":{"type":"message_start",...}}
{"type":"stream_event","event":{"type":"content_block_start",...}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"..."}}}
...
```

## Running Tests

```bash
# Run all deduplication integration tests
cargo xtask test --test deduplication_integration_tests

# Run with output
cargo xtask test --test deduplication_integration_tests -- --nocapture

# Run specific test
cargo xtask test test_no_duplicate_renders_in_all_logs -- --nocapture
```
