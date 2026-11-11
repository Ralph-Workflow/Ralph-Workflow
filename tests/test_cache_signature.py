
from ralph.language_detector import get_project_stack
from ralph.project_policy import cache as policy_cache
from ralph.project_policy import evidence as policy_evidence
from ralph.project_policy.models import ReadinessStatus
from ralph.workspace.memory import MemoryWorkspace


def test_cache_signature_round_trip() -> None:
    ws = MemoryWorkspace()
    stack = get_project_stack(ws)
    print(f"\n=== stack: {stack!r} ===")

    policy_cache.write_cache(ws, stack, ReadinessStatus.READY)
    cache_content = ws.read(".agent/tmp/policy_readiness_cache.json")
    print(f"=== cache file: {cache_content!r} ===")

    cur_sig = policy_evidence.evidence_signature(ws, stack)
    print(f"=== current sig: {cur_sig} ===")

    result = policy_cache.read_cached_ready(ws, stack)
    print(f"=== read_cached_ready: {result} ===")
    assert result, "Cache should be fresh; got sig mismatch"
