#!/usr/bin/env python3
"""Analyze runner.py to find key sections that need modification."""
import re

path = '/Users/mistlight/Projects/RalphWithReviewer/wt-82-fail-recovery/ralph-workflow/ralph/pipeline/runner.py'
with open(path, 'r') as f:
    content = f.read()
    lines = content.split('\n')

print(f"Total lines: {len(lines)}")
print(f"Total chars: {len(content)}")

# Find function definitions
print("\n=== FUNCTION DEFINITIONS ===")
for i, line in enumerate(lines, 1):
    if line.strip().startswith('def ') or line.strip().startswith('async def '):
        print(f"{i}: {line.strip()}")

# Find class definitions
print("\n=== CLASS DEFINITIONS ===")
for i, line in enumerate(lines, 1):
    if line.strip().startswith('class '):
        print(f"{i}: {line.strip()}")

# Find RecoveryController usages
print("\n=== RecoveryController USAGES ===")
for i, line in enumerate(lines, 1):
    if 'RecoveryController' in line:
        print(f"{i}: {line.strip()}")

# Find connectivity_monitor usages
print("\n=== connectivity_monitor USAGES ===")
for i, line in enumerate(lines, 1):
    if 'connectivity_monitor' in line.lower():
        print(f"{i}: {line.strip()}")

# Find seed_budget_registry usages
print("\n=== seed_budget_registry USAGES ===")
for i, line in enumerate(lines, 1):
    if 'seed_budget' in line:
        print(f"{i}: {line.strip()}")

# Find _connectivity_stop usages
print("\n=== _connectivity_stop USAGES ===")
for i, line in enumerate(lines, 1):
    if '_connectivity_stop' in line:
        print(f"{i}: {line.strip()}")

# Find snapshot usages
print("\n=== snapshot USAGES ===")
for i, line in enumerate(lines, 1):
    if 'snapshot' in line.lower():
        print(f"{i}: {line.strip()}")

# Find SignalBridge usages
print("\n=== SignalBridge USAGES ===")
for i, line in enumerate(lines, 1):
    if 'SignalBridge' in line:
        print(f"{i}: {line.strip()}")

# Find _reduce_runtime_recovery usages
print("\n=== _reduce_runtime_recovery USAGES ===")
for i, line in enumerate(lines, 1):
    if '_reduce_runtime_recovery' in line:
        print(f"{i}: {line.strip()}")

# Find last_connectivity_state usages
print("\n=== last_connectivity_state USAGES ===")
for i, line in enumerate(lines, 1):
    if 'last_connectivity_state' in line:
        print(f"{i}: {line.strip()}")

# Find fallover_history usages
print("\n=== fallover_history USAGES ===")
for i, line in enumerate(lines, 1):
    if 'fallover_history' in line:
        print(f"{i}: {line.strip()}")

# Find recovery_cycle_count usages
print("\n=== recovery_cycle_count USAGES ===")
for i, line in enumerate(lines, 1):
    if 'recovery_cycle_count' in line:
        print(f"{i}: {line.strip()}")
