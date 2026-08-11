#!/usr/bin/env bash
# Fail-closed Pro-contract drift checks extracted from the Makefile.
# Policy: docs/ralph-workflow-policy/gate-script-policy.md
set -euo pipefail

CHECK_TIMEOUT_SECONDS="${VERIFY_DRIFT_TIMEOUT_SECONDS:-10}"
CHECK_DIR="$(mktemp -d -t verify_drift.XXXXXX)"
chmod 700 "$CHECK_DIR"
cleanup() {
  rm -rf "$CHECK_DIR"
}
trap cleanup EXIT

run_checks() {
if grep -RIn --include="*.py" \
  -e "PROMPT.md" -e "Path(\"PROMPT.md\")" ralph/ \
  --exclude-dir=__pycache__ --exclude-dir=.venv \
  | grep -v "ralph/pro_support/prompt.py" \
  | grep -v "ralph/pro_support/env.py" \
  | grep -v "ralph/pro_support/__init__.py" \
  | grep -v "ralph/prompts/" \
  | grep -v "tests/" \
  | grep -v "\.agent/" \
  | grep -v "cli/main.py" \
  | grep -v "cli/commands/init.py" \
  | grep -v "cli/commands/diagnose.py" \
  | grep -v "cli/commands/run.py" \
  | grep -v "cli/commands/smoke.py" \
  | grep -v "phases/_agent_internal_paths.py" \
  | grep -v "config/welcome.py" \
  | grep -v "onboarding.py" \
  | grep -v "files/operations.py" \
  | grep -v "agents/invoke/__init__.py" \
  | grep -v "agents/invoke/_commands.py" \
  | grep -v "agents/invoke/_command_builders/" \
  | grep -v "agents/invoke/_runtime_resolvers/" \
  | grep -v "mcp/artifacts/product_spec.py" \
  | grep -v "mcp/server/_in_memory_transport.py" \
  | grep -v "mcp/server/_fallback_http_handler.py" \
  | grep -v "phases/integrity.py" \
  | grep -v "policy/validation/_api.py" \
  | grep -v "policy/defaults/" \
  | grep -v "display/prompt_reader.py" \
  | grep -v "pipeline/prompt_prep.py" \
  | grep -v "pipeline/orchestrator.py" \
  | grep -v "prompts/master_prompt.py" \
  | grep -v "prompts/materialize.py" \
  | grep -v "prompts/developer/" \
  | grep -v "parallel/worker_runtime.py" \
  | grep -v "_retry_progress_guard.py" \
  | grep -v "policy/loader.py" \
  | grep -v "phases/required_artifacts.py" \
  | grep -v "testing/audit_di_seam.py" \
  | grep -v "workspace/context.py" \
  ; then
  echo "drift: hardcoded source-prompt construction outside the resolver" >&2
  echo "Fix the resolver boundary. Governing policy: docs/ralph-workflow-policy/gate-script-policy.md § Default requirements." >&2
  exit 1
fi

if grep -RIn --include="*.py" -e ".ralph/run.json" ralph/ \
  | grep -v "ralph/pro_support/marker.py" \
  | grep -v "ralph/pro_support/watcher.py" \
  | grep -v "ralph/pro_support/__init__.py" \
  | grep -v "ralph/pro_support/env.py" \
  | grep -v "pro_support_inventory.md" \
  ; then
  echo "drift: .ralph/run.json referenced outside ralph/pro_support/marker.py" >&2
  echo "Fix the marker boundary. Governing policy: docs/ralph-workflow-policy/gate-script-policy.md § Default requirements." >&2
  exit 1
fi

if grep -RIn --include="*.py" -e "time\.sleep" ralph/pro_support/ \
  | grep -v "ralph/pro_support/watcher.py" \
  | grep -v "ralph/pro_support/state_query.py" \
  ; then
  echo "drift: ralph/pro_support must not use time.sleep" >&2
  echo "Use the watchdog seam. Governing policy: docs/ralph-workflow-policy/gate-script-policy.md § Default requirements." >&2
  exit 1
fi

if grep -RIn --include="*.py" -E "os\.environ\.(get|__getitem__)\(\"RALPH_" ralph/ \
  | grep -v -E "RALPH_(WORKFLOW_PRO|WORKSPACE|PROMPT_PATH)\b" \
  | grep -v "ralph/pro_support/env.py" \
  | grep -v "ralph/pro_support/__init__.py" \
  | grep -v "ralph/mcp/server/_fallback_standalone_server.py" \
  | grep -v "ralph/cli/main.py" \
  | grep -v "tests/test_pro_support" \
  | grep -v "tests/test_audit_mcp_timeout" \
  | grep -v "tests/test_run_loop_pro_integration" \
  | grep -v "tests/test_audit_lint_bypass" \
  | grep -v "tests/test_audit_test_policy" \
  | grep -v "tests/test_audit_typecheck_bypass" \
  | grep -v "tests/test_audit_di_seam" \
  | grep -v "tests/test_pro_support_contract" \
  | grep -v "tests/test_pro_support_cross_repo_marker" \
  | grep -v "tests/test_no_dead_code" \
  | grep -v "tests/test_opencode_mcp_config_drift" \
  ; then
  echo "drift: ralph/ uses a RALPH_* env var outside the canonical three" >&2
  echo "Use the canonical environment boundary. Governing policy: docs/ralph-workflow-policy/gate-script-policy.md § Default requirements." >&2
  exit 1
fi

bash "$(dirname "$0")/wt028-drift-check.sh"
echo "verify-drift: OK"
}

run_checks &
CHECK_PID="$!"
(
  sleep "$CHECK_TIMEOUT_SECONDS"
  : > "$CHECK_DIR/timed_out"
  kill -KILL "$CHECK_PID" 2>/dev/null || true
) &
WATCHDOG_PID="$!"
set +e
wait "$CHECK_PID"
CHECK_RC="$?"
set -e
kill -KILL "$WATCHDOG_PID" 2>/dev/null || true
wait "$WATCHDOG_PID" 2>/dev/null || true

if [ -e "$CHECK_DIR/timed_out" ]; then
  echo "FAIL: verify-drift exceeded ${CHECK_TIMEOUT_SECONDS}s and was stopped" >&2
  echo "Fix the slow check; do not raise the timeout. Governing policy: docs/ralph-workflow-policy/gate-script-policy.md § Bounded and § Failure output." >&2
  exit 124
fi
exit "$CHECK_RC"
