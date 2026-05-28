#!/bin/bash
# Runs the full marketing certification bundle when the measurement hold expires.
# Scheduled for 2026-05-28 09:12:15 local time.

RESULTS_FILE="/home/mistlight/.openclaw/workspace/agents/marketing/logs/scheduled_certify_results.json"
LOG_FILE="/home/mistlight/.openclaw/workspace/agents/marketing/logs/scheduled_certify.log"

echo "=== Scheduled marketing certifier started at $(date -Iseconds) ===" >> "$LOG_FILE"

cd /home/mistlight/.openclaw/workspace

python3 agents/marketing/marketing_momentum_watchdog.py >> "$LOG_FILE" 2>&1
echo "watchdog done: $?" >> "$LOG_FILE"

python3 agents/marketing/marketing_loop_runner.py >> "$LOG_FILE" 2>&1
echo "runner done: $?" >> "$LOG_FILE"

python3 agents/marketing/outcome_execution_board_runner.py >> "$LOG_FILE" 2>&1
echo "execution_board done: $?" >> "$LOG_FILE"

python3 agents/marketing/marketing_loop_independent_verify.py >> "$LOG_FILE" 2>&1
VERIFY_EXIT=$?

VERDICT=$(python3 -c "
import json
with open('/home/mistlight/.openclaw/workspace/agents/marketing/logs/marketing_loop_independent_verification.json') as f:
    d = json.load(f)
print(d.get('verdict','unknown'))
" 2>>"$LOG_FILE")

echo "=== Completed at $(date -Iseconds) | verdict=$VERDICT exit=$VERIFY_EXIT ===" >> "$LOG_FILE"

# Write summary
python3 -c "
import json, datetime
with open('/home/mistlight/.openclaw/workspace/agents/marketing/logs/marketing_loop_independent_verification.json') as f:
    data = json.load(f)
summary = {
    'scheduled_run_at': '2026-05-28T09:12:15+02:00',
    'completed_at': datetime.datetime.now().astimezone().isoformat(),
    'verdict': data.get('verdict'),
    'summary': data.get('summary'),
    'blockers': data.get('blockers', []),
    'watchpoints': data.get('watchpoints', []),
    'verify_exit_code': $VERIFY_EXIT
}
with open('$RESULTS_FILE','w') as f:
    json.dump(summary, f, indent=2)
print('Results written to $RESULTS_FILE')
print('Verdict:', data.get('verdict'))
print('Blockers:', data.get('blockers', []))
" >> "$LOG_FILE" 2>&1
