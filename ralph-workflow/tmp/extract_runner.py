#!/usr/bin/env python3
"""Extract key sections from runner.py for analysis."""
import sys

path = '/Users/mistlight/Projects/RalphWithReviewer/wt-82-fail-recovery/ralph-workflow/ralph/pipeline/runner.py'
with open(path, 'r') as f:
    content = f.read()
    lines = content.split('\n')

# Find lines containing key patterns
keywords = [
    'RecoveryController', 'seed_budget_registry', 'connectivity_monitor',
    '_connectivity_stop', 'ConnectivityMonitor', 'SignalBridge',
    '_reduce_runtime_recovery', 'snapshot', 'fallover_history',
    'recovery_cycle_count', 'last_failure_category', 'last_connectivity_state',
    'AgentBudgetRegistry', 'FailureEventBus', '_log_recovery'
]

for keyword in keywords:
    print(f'\n=== Found "{keyword}" at these lines ===')
    for i, line in enumerate(lines, 1):
        if keyword in line:
            start = max(0, i - 2)
            end = min(len(lines), i + 3)
            for j in range(start, end):
                marker = '>>> ' if j + 1 == i else '    '
                print(f'{marker}{j+1}: {lines[j]}')
            print()
