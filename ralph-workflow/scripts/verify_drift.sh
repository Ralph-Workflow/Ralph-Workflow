#!/usr/bin/env bash
# Fail-closed Pro-contract drift checks extracted from the Makefile.
# Policy: docs/ralph-workflow-policy/gate-script-policy.md
set -euo pipefail
set -o monitor

RALPH_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$RALPH_ROOT"

CHECK_TIMEOUT_SECONDS="${VERIFY_DRIFT_TIMEOUT_SECONDS:-10}"
CHECK_DIR="$(mktemp -d -t verify_drift.XXXXXX)"
chmod 700 "$CHECK_DIR"
cleanup() {
  if [ -n "${WATCHDOG_PID:-}" ]; then
    kill -KILL -"$WATCHDOG_PID" 2>/dev/null || true
    kill -KILL "$WATCHDOG_PID" 2>/dev/null || true
  fi
  if [ -n "${CHECK_PID:-}" ]; then
    kill -KILL -"$CHECK_PID" 2>/dev/null || true
    kill -KILL "$CHECK_PID" 2>/dev/null || true
  fi
  rm -rf "$CHECK_DIR"
}
trap cleanup EXIT

run_checks() {
if grep -RIn --include="*.py" \
  -e "PROMPT.md" -e "Path(\"PROMPT.md\")" ralph/ \
  --exclude-dir=__pycache__ --exclude-dir=.venv --exclude-dir=.pytest_cache --exclude-dir=.mypy_cache \
  | grep -v -E "ralph/pro_support/(prompt|env|__init__)\.py|ralph/prompts/|tests/|\.agent/|cli/(main|commands/(init|diagnose|run|smoke))\.py|phases/_agent_internal_paths\.py|config/welcome\.py|onboarding\.py|files/operations\.py|agents/invoke/(__init__|_commands|_command_builders/|_runtime_resolvers/)|mcp/(artifacts/product_spec|server/(_in_memory_transport|_fallback_http_handler))\.py|phases/integrity\.py|policy/validation/_api\.py|policy/defaults/|display/prompt_reader\.py|pipeline/(prompt_prep|orchestrator)\.py|prompts/(master_prompt|materialize|developer/)|parallel/worker_runtime\.py|_retry_progress_guard\.py|policy/loader\.py|phases/required_artifacts\.py|testing/audit_di_seam\.py|workspace/context\.py" \
  ; then
  echo "drift: hardcoded source-prompt construction outside the resolver" >&2
  echo "Fix the resolver boundary. Governing policy: docs/ralph-workflow-policy/gate-script-policy.md § Default requirements." >&2
  exit 1
fi

if grep -RIn --include="*.py" -e ".ralph/run.json" ralph/ \
  --exclude-dir=__pycache__ --exclude-dir=.venv --exclude-dir=.pytest_cache --exclude-dir=.mypy_cache \
  | grep -v -E "ralph/pro_support/(marker|watcher|__init__|env)\.py|pro_support_inventory\.md" \
  ; then
  echo "drift: .ralph/run.json referenced outside ralph/pro_support/marker.py" >&2
  echo "Fix the marker boundary. Governing policy: docs/ralph-workflow-policy/gate-script-policy.md § Default requirements." >&2
  exit 1
fi

if grep -RIn --include="*.py" -e "time\.sleep" ralph/pro_support/ \
  --exclude-dir=__pycache__ --exclude-dir=.venv --exclude-dir=.pytest_cache --exclude-dir=.mypy_cache \
  | grep -v -E "ralph/pro_support/(watcher|state_query)\.py" \
  ; then
  echo "drift: ralph/pro_support must not use time.sleep" >&2
  echo "Use the watchdog seam. Governing policy: docs/ralph-workflow-policy/gate-script-policy.md § Default requirements." >&2
  exit 1
fi

if grep -RIn --include="*.py" -E "os\.environ\.(get|__getitem__)\(\"RALPH_" ralph/ \
  --exclude-dir=__pycache__ --exclude-dir=.venv --exclude-dir=.pytest_cache --exclude-dir=.mypy_cache \
  | grep -v -E "RALPH_(WORKFLOW_PRO|WORKSPACE|PROMPT_PATH)\b" \
  | grep -v -E "ralph/pro_support/(env|__init__)\.py|ralph/mcp/server/_fallback_standalone_server\.py|ralph/cli/main\.py|ralph/testing/audit_skill_mirror_freshness\.py|tests/(test_pro_support|test_audit_mcp_timeout|test_run_loop_pro_integration|test_audit_lint_bypass|test_audit_test_policy|test_audit_typecheck_bypass|test_audit_di_seam|test_pro_support_contract|test_pro_support_cross_repo_marker|test_no_dead_code|test_opencode_mcp_config_drift)" \
  ; then
  echo "drift: ralph/ uses a RALPH_* env var outside the canonical three" >&2
  echo "Use the canonical environment boundary. Governing policy: docs/ralph-workflow-policy/gate-script-policy.md § Default requirements." >&2
  exit 1
fi

bash "$RALPH_ROOT/scripts/wt028-drift-check.sh"
echo "verify-drift: OK"
}

run_checks &
CHECK_PID="$!"
CHECK_PGID="$(ps -o pgid= -p "$CHECK_PID" 2>/dev/null | tr -d ' ' || true)"
(
  sleep "$CHECK_TIMEOUT_SECONDS"
  : > "$CHECK_DIR/timed_out"
  if [ -n "$CHECK_PGID" ]; then
    kill -KILL -"$CHECK_PGID" 2>/dev/null || true
  fi
  kill -KILL "$CHECK_PID" 2>/dev/null || true
) < /dev/null > /dev/null 2>&1 &
WATCHDOG_PID="$!"
set +e
wait "$CHECK_PID"
CHECK_RC="$?"
set -e
disown "$WATCHDOG_PID" 2>/dev/null || true
kill -KILL -"$WATCHDOG_PID" 2>/dev/null || true
kill -KILL "$WATCHDOG_PID" 2>/dev/null || true
wait "$WATCHDOG_PID" 2>/dev/null || true

if [ -e "$CHECK_DIR/timed_out" ]; then
  echo "FAIL: verify-drift exceeded ${CHECK_TIMEOUT_SECONDS}s and was stopped" >&2
  echo "Fix the slow check; do not raise the timeout. Governing policy: docs/ralph-workflow-policy/gate-script-policy.md § Bounded and § Failure output." >&2
  exit 124
fi
exit "$CHECK_RC"
