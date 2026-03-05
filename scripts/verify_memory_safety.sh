#!/usr/bin/env bash
# Memory safety verification script
#
# This script runs all memory safety tests to verify:
# - Bounded memory growth in execution history
# - Thread lifecycle cleanup
# - Arc reference pattern correctness
# - Channel backpressure behavior
# - Unsafe code correctness
#
# All tests must pass with no ERROR/WARNING diagnostics (informational output is acceptable).
#
# Usage:
#   bash scripts/verify_memory_safety.sh

set -euo pipefail

VERBOSE=0
if [[ "${1:-}" == "--verbose" ]]; then
  VERBOSE=1
  shift
fi

log() {
  if [[ "$VERBOSE" -eq 1 ]]; then
    printf '%s\n' "$*"
  fi
}

run() {
  local name="$1"
  shift

  local output
  if output=$("$@" 2>&1); then
    log "✓ ${name}"
    return 0
  fi

  printf '%s\n' "✗ ${name}" >&2
  if [[ "$VERBOSE" -eq 1 ]]; then
    printf '%s\n' "$output" >&2
  fi
  return 1
}

FAILED=0

run "Memory safety integration tests" \
  cargo test -p ralph-workflow-tests --test integration_tests memory_safety --quiet \
  || FAILED=1

run "Bounded growth tests" \
  cargo test -p ralph-workflow-tests --test integration_tests memory_safety::bounded_growth --quiet \
  || FAILED=1

run "Thread lifecycle tests" \
  cargo test -p ralph-workflow-tests --test integration_tests memory_safety::thread_lifecycle --quiet \
  || FAILED=1

run "Arc pattern tests" \
  cargo test -p ralph-workflow-tests --test integration_tests memory_safety::arc_patterns --quiet \
  || FAILED=1

run "Channel bounds tests" \
  cargo test -p ralph-workflow-tests --test integration_tests memory_safety::channel_bounds --quiet \
  || FAILED=1

run "Unsafe pattern tests" \
  cargo test -p ralph-workflow-tests --test integration_tests memory_safety::unsafe_patterns --quiet \
  || FAILED=1

run "Benchmark tests" \
  cargo test -p ralph-workflow --lib benchmarks --quiet \
  || FAILED=1

run "Executor unit tests" \
  cargo test -p ralph-workflow --lib executor::tests --quiet \
  || FAILED=1

if [[ "$FAILED" -eq 0 ]]; then
  exit 0
fi

exit 1
