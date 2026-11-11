import os
import sys
import tempfile

tmpdir = tempfile.mkdtemp(prefix="debug-xdg-")
os.environ["HOME"] = tmpdir
os.environ["XDG_CONFIG_HOME"] = tmpdir + "/.config"

from ralph.telemetry import _sentry
from ralph.agent_runtime.transport import AgentTransport
from ralph.agent_runtime.config import AgentConfig

_sentry._INITIALIZED = True

agent = AgentConfig.model_validate({"cmd": "claude", "transport": "claude", "model": "x" * 200})
print("Agent:", agent)

print("_telemetry_is_inactive():", _sentry._telemetry_is_inactive())

try:
    from ralph.telemetry._agent_config_payload import build_agent_config_payload
    payload = build_agent_config_payload({"a": agent})
    print("Payload:", payload)
except Exception as e:
    print("Payload failed:", type(e).__name__, e)

captured = []
import sentry_sdk
sentry_sdk.set_context = lambda name, data: captured.append((name, data))
sentry_sdk.set_tag = lambda k, v: None

try:
    _sentry.set_agent_config_context({"a": agent})
    print("Captured:", captured)
except Exception as e:
    print("set_agent_config_context failed:", type(e).__name__, e)
