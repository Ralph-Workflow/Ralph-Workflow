"""Run the failing test variant in isolation to capture the error."""
import os
import sys
import tempfile

# Simulate the autouse fixture
tmpdir = tempfile.mkdtemp(prefix="debug-xdg-")
os.environ["HOME"] = tmpdir
os.environ["XDG_CONFIG_HOME"] = tmpdir + "/.config"

# Now import and exercise
from ralph.telemetry import _sentry
from ralph.agent_runtime.transport import AgentTransport
from ralph.agent_runtime.config import AgentConfig

# Simulate _capture_contexts: patch _INITIALIZED
_sentry._INITIALIZED = True

# Now call set_agent_config_context with "x"*200 model
agent = AgentConfig.model_validate({"cmd": "claude", "transport": "claude", "model": "x" * 200})
print("Agent:", agent)

# Check _telemetry_is_inactive
print("_telemetry_is_inactive():", _sentry._telemetry_is_inactive())

# Try build_agent_config_payload
try:
    from ralph.telemetry._agent_config_payload import build_agent_config_payload
    payload = build_agent_config_payload({"a": agent})
    print("Payload:", payload)
except Exception as e:
    print("Payload failed:", type(e).__name__, e)

# Check set_context
captured = []
import sentry_sdk
sentry_sdk.set_context = lambda name, data: captured.append((name, data))
sentry_sdk.set_tag = lambda k, v: None

try:
    _sentry.set_agent_config_context({"a": agent})
    print("Captured:", captured)
except Exception as e:
    print("set_agent_config_context failed:", type(e).__name__, e)
