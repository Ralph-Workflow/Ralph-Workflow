"""Test script to debug the import issue."""
import sys
print(f"Python version: {sys.version}")

# Try importing the module step by step
print("Step 1: Importing ralph package")
import ralph
print(f"  ralph.__file__ = {ralph.__file__}")

print("Step 2: Importing ralph.recovery")
import ralph.recovery
print(f"  ralph.recovery.__file__ = {ralph.recovery.__file__}")

print("Step 3: Importing ralph.recovery.controller")
import ralph.recovery.controller as ctrl
print(f"  ralph.recovery.controller.__file__ = {ctrl.__file__}")

print("\nClasses in ralph.recovery.controller:")
for name in dir(ctrl):
    if 'Recovery' in name or 'Failure' in name or 'Options' in name:
        print(f"  {name}")

print("\nTrying to import RecoveryControllerOptions:")
try:
    from ralph.recovery.controller import RecoveryControllerOptions
    print(f"  Success: {RecoveryControllerOptions}")
except ImportError as e:
    print(f"  Error: {e}")