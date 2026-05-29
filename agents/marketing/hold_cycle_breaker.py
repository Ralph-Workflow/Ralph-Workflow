#!/usr/bin/env python3
"""
Hold-cycle breaker: prevents idle measurement-hold regressions.

When the execution board produces guard_pause / empty-board results for
2+ consecutive cycles, this watchdog auto-generates a blog content gap
analysis and writes a content-generation directive instead of allowing
another idle hold to be logged.

Rule source: MARKETING_SELF_IMPROVEMENT.md — "if the execution board is
still empty after one of the listed blockers clears, do not log another
idle measurement hold; choose a different executable lane or perform a
concrete distribution_architecture_repair in the same run"
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('/home/mistlight/.openclaw/workspace')
EXECUTION_BOARD_PATH = ROOT / 'agents/marketing/logs/outcome_execution_board_latest.json'
AUDIT_PATH = ROOT / 'agents/marketing/logs/marketing_workflow_audit_latest.json'
PUBLISHER_DISCOVERY_PATH = ROOT / 'agents/marketing/logs/publisher_discovery_latest.json'
BLOG_DIR = ROOT / 'Ralph-Site/content/blog'
BREAKER_STATE_PATH = ROOT / 'agents/marketing/logs/hold_cycle_breaker_state.json'
OUT_PATH = ROOT / 'agents/marketing/logs/hold_cycle_breaker_latest.json'

GUARD_PAUSE_SIGNATURES = {
    'distribution_architecture_guard_pause',
    'distribution_architecture_guard_follow_through',
}


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
        return {}


def _load_breaker_state() -> dict:
    return _load_json(BREAKER_STATE_PATH)


def _save_breaker_state(state: dict) -> None:
    BREAKER_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    BREAKER_STATE_PATH.write_text(json.dumps(state, indent=2), encoding='utf-8')


def _count_consecutive_guard_pauses(execution_board: dict, state: dict) -> int:
    lane = str(execution_board.get('executed_lane') or execution_board.get('selected_lane') or '').strip()
    if lane in GUARD_PAUSE_SIGNATURES:
        state['consecutive_guard_pauses'] = state.get('consecutive_guard_pauses', 0) + 1
    else:
        state['consecutive_guard_pauses'] = 0
    state['last_lane'] = lane
    state['last_check_at'] = datetime.now(timezone.utc).isoformat()
    return state['consecutive_guard_pauses']


def _blog_content_gaps() -> list[dict]:
    """Check for blog content gaps by scanning publisher articles vs existing blog posts."""
    gaps: list[dict] = []
    if not BLOG_DIR.exists():
        return gaps

    existing_posts = {p.stem for p in BLOG_DIR.glob('*.md')}
    publisher_data = _load_json(PUBLISHER_DISCOVERY_PATH)
    discovered = publisher_data.get('results', []) or publisher_data.get('articles', []) or []

    for article in discovered:
        title = str(article.get('title') or '').strip()
        url = str(article.get('url') or '').strip()
        if not title or not url:
            continue
        # Check if we have a blog post targeting this comparison topic
        topic_words = title.lower().split()
        keyword_match = any(
            kw in ' '.join(topic_words)
            for kw in ['comparison', 'vs', 'best', 'compare', 'showdown', 'landscape']
        )
        if keyword_match and not any(title.lower().replace(' ', '-')[:40] in stem for stem in existing_posts):
            gaps.append({'title': title, 'url': url, 'source': article.get('source', 'publisher_discovery')})

    return gaps


def _check_recent_blog_output(max_hours: float = 6.0) -> bool:
    """Check whether a blog post was deployed recently."""
    import time as _time
    if not AUDIT_PATH.exists():
        return False
    audit = _load_json(AUDIT_PATH)
    latest_action = audit.get('latest_executed_action', {}) or audit.get('latest_marketing_activity', {}) or {}
    action_type = str(latest_action.get('type') or '').strip()
    if action_type == 'deployed_blog_post' and latest_action.get('live_external_action'):
        # Check if it was recent enough
        log_path = Path(str(latest_action.get('path', '')))
        if log_path.exists():
            age_hours = (_time.time() - log_path.stat().st_mtime) / 3600
            if age_hours < max_hours:
                return True
    return False


def _write_content_gap_directive(gaps: list[dict]) -> dict:
    """Write a content-generation directive for the active loop to pick up."""
    directive = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'directive': 'create_blog_content_from_gaps',
        'reason': f'{len(gaps)} unmet blog content gap(s) found from publisher discovery — comparison articles without Ralph Workflow coverage',
        'gaps': gaps[:3],  # top 3 gaps
        'expected_outcome': 'Blog post deployed to Ralph-Site targeting comparison article keywords',
        'measurement_window_days': 7,
        'kill_condition': 'No organic traffic improvement after 14 days',
    }
    directive_path = ROOT / 'agents/marketing/logs/content_gap_directive_latest.json'
    directive_path.write_text(json.dumps(directive, indent=2), encoding='utf-8')
    return directive


def main() -> int:
    board = _load_json(EXECUTION_BOARD_PATH)
    state = _load_breaker_state()

    consecutive = _count_consecutive_guard_pauses(board, state)
    _save_breaker_state(state)

    result = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'consecutive_guard_pauses': consecutive,
        'last_executed_lane': state.get('last_lane', ''),
        'breaker_triggered': False,
        'action_taken': None,
    }

    if consecutive < 2:
        OUT_PATH.write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps(result, indent=2))
        return 0

    # 2+ consecutive guard pauses — breaker triggers
    result['breaker_triggered'] = True

    # Check if blog content was already deployed recently
    if _check_recent_blog_output():
        result['action_taken'] = 'breaker_suppressed_blog_already_deployed'
        result['reason'] = 'Blog content was deployed within the last 6 hours — breaker suppressing duplicate content creation'
        OUT_PATH.write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps(result, indent=2))
        return 0

    # Check for blog content gaps
    gaps = _blog_content_gaps()
    if gaps:
        directive = _write_content_gap_directive(gaps)
        result['action_taken'] = 'wrote_content_gap_directive'
        result['directive'] = directive
        result['gap_count'] = len(gaps)
        state['consecutive_guard_pauses'] = 0  # reset after producing a directive
        _save_breaker_state(state)
    else:
        result['action_taken'] = 'no_content_gaps_found'
        result['reason'] = 'No unmet blog content gaps discovered — all comparison topics are covered'

    OUT_PATH.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
