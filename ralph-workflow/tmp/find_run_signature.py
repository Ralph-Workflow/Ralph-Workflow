#!/usr/bin/env python3
"""Extract run function signature and key sections from runner.py."""
import re

path = '/Users/mistlight/Projects/RalphWithReviewer/wt-82-fail-recovery/ralph-workflow/ralph/pipeline/runner.py'
with open(path, 'r') as f:
    lines = f.readlines()

# Find "def run" or "async def run"
print("=== RUN FUNCTION SIGNATURE ===")
in_run = False
indent_level = 0
for i, line in enumerate(lines, 1):
    stripped = line.strip()
    if 'def run(' in stripped or 'async def run(' in stripped:
        in_run = True
        indent_level = len(line) - len(line.lstrip())
        print(f"{i}: {line.rstrip()}")
        continue
    if in_run:
        if line.strip() and not line.startswith(' ' * (indent_level + 1)) and not line.strip().startswith('#'):
            break
        print(f"{i}: {line.rstrip()}")
        if stripped and not stripped.startswith('#') and ':' in stripped:
            # Check if we hit a new definition
            if stripped.startswith('def ') or stripped.startswith('async def '):
                break

print("\n=== RECOVERY CONTROLLER CONSTRUCTION ===")
for i, line in enumerate(lines, 1):
    if 'RecoveryController' in line and ('=' in line or 'Controller(' in line):
        # Print context
        start = max(0, i - 3)
        end = min(len(lines), i + 10)
        for j in range(start, end):
            marker = '>>> ' if j + 1 == i else '    '
            print(f'{marker}{j+1}: {lines[j].rstrip()}')
        print()

print("\n=== SEED_BUDGET_REGISTRY USAGE ===")
for i, line in enumerate(lines, 1):
    if 'seed_budget' in line.lower():
        start = max(0, i - 2)
        end = min(len(lines), i + 5)
        for j in range(start, end):
            marker = '>>> ' if j + 1 == i else '    '
            print(f'{marker}{j+1}: {lines[j].rstrip()}')
        print()

print("\n=== CONNECTIVITY_MONITOR PARAMETER ===")
for i, line in enumerate(lines, 1):
    if 'connectivity_monitor' in line.lower():
        start = max(0, i - 2)
        end = min(len(lines), i + 5)
        for j in range(start, end):
            marker = '>>> ' if j + 1 == i else '    '
            print(f'{marker}{j+1}: {lines[j].rstrip()}')
        print()

print("\n=== SIGNALBRIDGE INSTALLATION ===")
for i, line in enumerate(lines, 1):
    if 'SignalBridge' in line or 'signal_bridge' in line.lower() or 'install_signal' in line.lower():
        start = max(0, i - 3)
        end = min(len(lines), i + 10)
        for j in range(start, end):
            marker = '>>> ' if j + 1 == i else '    '
            print(f'{marker}{j+1}: {lines[j].rstrip()}')
        print()

print("\n=== _CONNECTIVITY_STOP ===")
for i, line in enumerate(lines, 1):
    if '_connectivity_stop' in line:
        start = max(0, i - 3)
        end = min(len(lines), i + 10)
        for j in range(start, end):
            marker = '>>> ' if j + 1 == i else '    '
            print(f'{marker}{j+1}: {lines[j].rstrip()}')
        print()

print("\n=== SNAPSHOT USAGE ===")
for i, line in enumerate(lines, 1):
    if 'snapshot' in line.lower():
        start = max(0, i - 2)
        end = min(len(lines), i + 5)
        for j in range(start, end):
            marker = '>>> ' if j + 1 == i else '    '
            print(f'{marker}{j+1}: {lines[j].rstrip()}')
        print()
