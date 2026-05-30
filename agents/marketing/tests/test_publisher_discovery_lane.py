#!/usr/bin/env python3
"""Unit tests for publisher_discovery_lane.py --from-json injection path."""

import sys
sys.path.insert(0, "/home/mistlight/.openclaw/workspace/agents/marketing")
from publisher_discovery_lane import inject_results, rank


def test(name, condition):
    if condition:
        print(f"  PASS {name}")
        return True
    else:
        print(f"  FAIL {name}")
        return False


def run_tests():
    passed, failed = 0, 0

    # Test 1: inject_results accepts valid input
    results = [{"title": "Test Article", "url": "https://example.com/blog/test", "source": "example.com"}]
    ranked, count = inject_results(results)
    for name, cond in [
        ("inject_results returns correct count", count == 1),
        ("inject_results preserves title", ranked[0]["title"] == "Test Article"),
        ("inject_results assigns query tag", ranked[0]["query"] == "agent-injected"),
    ]:
        if test(name, cond): passed += 1
        else: failed += 1

    # Test 2: inject_results skips items without URL
    results2 = [
        {"title": "No URL", "source": "x.com"},
        {"title": "Has URL", "url": "https://example.com/test", "source": "example.com"},
    ]
    ranked2, count2 = inject_results(results2)
    for name, cond in [
        ("inject_results skips items without url", count2 == 1 and ranked2[0]["title"] == "Has URL"),
    ]:
        if test(name, cond): passed += 1
        else: failed += 1

    # Test 3: inject_results derives source from URL if missing
    results3 = [{"title": "No Source Field", "url": "https://someblog.com/article"}]
    ranked3, count3 = inject_results(results3)
    for name, cond in [
        ("inject_results derives source from URL when missing", count3 == 1 and ranked3[0]["source"] == "someblog.com"),
    ]:
        if test(name, cond): passed += 1
        else: failed += 1

    # Test 4: rank scores comparison/orchestration titles higher
    items = [
        {"title": "Best AI Tools Comparison 2026", "url": "https://a.com", "source": "a.com", "query": "test"},
        {"title": "Some generic post about AI", "url": "https://b.com", "source": "b.com", "query": "test"},
        {"title": "Agent Workflow Automation with Orchestration", "url": "https://c.com", "source": "c.com", "query": "test"},
    ]
    scored = rank(items)
    for name, cond in [
        ("rank gives comparison title higher score", scored[0]["title"] == "Best AI Tools Comparison 2026"),
        ("rank gives publisher content mid-tier score", scored[1]["title"] == "Agent Workflow Automation with Orchestration"),
        ("rank gives generic post lowest score", scored[2]["title"] == "Some generic post about AI"),
    ]:
        if test(name, cond): passed += 1
        else: failed += 1

    # Test 5: empty input returns empty
    ranked5, count5 = inject_results([])
    if test("inject_results empty list returns empty", count5 == 0): passed += 1
    else: failed += 1

    print(f"\n{passed} passed, {failed} failed")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if run_tests() else 1)
