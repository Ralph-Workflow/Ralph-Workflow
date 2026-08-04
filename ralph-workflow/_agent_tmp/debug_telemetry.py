"""Quick diagnostic to check telemetry-disabled state."""
import os
import tempfile
from pathlib import Path

# Simulate the autouse fixture
tmpdir = tempfile.mkdtemp(prefix="debug-xdg-")
os.environ["HOME"] = tmpdir
os.environ["XDG_CONFIG_HOME"] = tmpdir + "/.config"

from ralph.workspace.scope import resolve_workspace_scope
from ralph.telemetry import _sentry

print("XDG_CONFIG_HOME:", os.environ.get("XDG_CONFIG_HOME"))
print("HOME:", os.environ.get("HOME"))
print("CWD:", os.getcwd())
print("RALPH_DISABLE_TELEMETRY:", os.environ.get("RALPH_DISABLE_TELEMETRY"))
print()

# Mimic _capture_contexts: set _INITIALIZED via setattr
setattr(_sentry, "_INITIALIZED", True)
print("After setting _INITIALIZED=True:")
print("  _telemetry_is_inactive():", _sentry._telemetry_is_inactive())
print("  is_telemetry_disabled():", _sentry.is_telemetry_disabled())
print("  is_telemetry_disabled_by_config():", _sentry.is_telemetry_disabled_by_config())

# Check what nearest local config is
print("  _nearest_local_config_path():", _sentry._nearest_local_config_path())
print("  CWD-relative .agent/ralph-workflow.toml exists:", (Path(".agent") / "ralph-workflow.toml").exists())
