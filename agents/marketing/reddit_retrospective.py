#!/usr/bin/env python3
"""ARCHITECTURALLY RETIRED 2026-05-28."""
from __future__ import annotations

import json
import re
import sys as _sys

if __name__ == '__main__':
    print(json.dumps({'status': 'retired', 'reason': 'Reddit pipeline architecturally retired 2026-05-28'}))
    _sys.exit(0)
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path('/home/mistlight/.openclaw/workspace')
LOG_JSONL = ROOT / 'agents/marketing/logs/reddit_posts.jsonl'
OUT_MD = ROOT / 'agents/marketing/logs/reddit_post_analysis.md'
OUT_JSON = ROOT / 'agents/marketing/logs/reddit_post_analysis.json'
OUT_MD_LATEST = ROOT / 'agents/marketing/logs/reddit_post_analysis_latest.md'
OUT_JSON_LATEST = ROOT / 'agents/marketing/logs/reddit_post_analysis_latest.json'

OPENING_STOPWORDS = {'the', 'a', 'an', 'and', 'but', 'for', 'with', 'that', 'this', 'from', 'into', 'your', 'have', 'had', 'when', 'what', 'more', 'just', 'they', 'them', 'then', 'than', 'still', 'really'}
RECENT_POST_WINDOW = 6
RECENT_POST_MAX_AGE_HOURS = 96
CADENCE_RECORD_TYPES = {'structural_body_cadence', 'cadence', 'cadence_check'}


def norm(text: str) -> str:
    return re.sub(r'\s+', ' ', text.strip())


def tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9']+", text.lower()) if len(t) > 3 and t not in OPENING_STOPWORDS]


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _is_structural_row(row: dict) -> bool:
    row_type = str(row.get('type') or '').strip().lower()
    if row_type in CADENCE_RECORD_TYPES:
        return True
    return 'body' not in row and 'metadata' not in row and row_type != 'reddit'


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')


def _write_json_pair(payload: dict) -> None:
    rendered = json.dumps(payload, indent=2, ensure_ascii=False)
    _write_text(OUT_JSON, rendered)
    _write_text(OUT_JSON_LATEST, rendered)


def _write_markdown_pair(content: str) -> None:
    _write_text(OUT_MD, content)
    _write_text(OUT_MD_LATEST, content)


def load_rows() -> tuple[list[dict], int]:
    rows: list[dict] = []
    filtered_structural = 0
    if not LOG_JSONL.exists():
        return rows, filtered_structural
    for line in LOG_JSONL.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if not isinstance(row, dict):
            continue
        if _is_structural_row(row):
            filtered_structural += 1
            continue
        rows.append(row)
    return rows, filtered_structural


def main() -> int:
    # ── Spidering guard: Reddit is permanently blocked (IP at Hetzner) ──
    try:
        from agents.marketing.channel_spidering_guard import guard_check, guard_record
        allowed, reason, remaining = guard_check("reddit")
        if not allowed:
            guard_record("reddit", ok=False, fingerprint="spidering_guard_rejected")
            payload = {"ok": False, "status": "spidering_blocked", "reason": reason, "live_external_action": False}
            print(json.dumps(payload))
            return 1
    except ImportError:
        pass

    rows, filtered_structural = load_rows()
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now().isoformat()

    if not rows:
        payload = {
            'generated_at': generated_at,
            'count': 0,
            'recent_window_count': 0,
            'status': 'no_posts',
            'filtered_structural_records': filtered_structural,
            'recent_window_max_age_hours': RECENT_POST_MAX_AGE_HOURS,
        }
        _write_json_pair(payload)
        _write_markdown_pair(
            '# Reddit Post Analysis\n\n'
            f'- Generated: {generated_at}\n'
            f'- Filtered {filtered_structural} cadence/structural records\n\n'
            'No logged Reddit posts yet.\n'
        )
        return 0

    now = datetime.now(UTC)
    recent_cutoff = now - timedelta(hours=RECENT_POST_MAX_AGE_HOURS)
    recent_candidates = []
    for row in rows:
        parsed_ts = _parse_timestamp(str(row.get('timestamp') or ''))
        if parsed_ts is not None and parsed_ts >= recent_cutoff:
            recent_candidates.append(row)
    recent_rows = recent_candidates[-RECENT_POST_WINDOW:]

    by_account = Counter()
    by_community = Counter()
    title_words = Counter()
    opening_lines: list[str] = []
    body_tokens = Counter()
    phrase_hits = Counter()
    post_summaries = []
    account_switches = []

    last_account = None
    for row in recent_rows:
        account = row.get('account') or 'unknown'
        meta = row.get('metadata') or {}
        community = (meta.get('community') or '').strip('`') or 'unknown'
        title = meta.get('title') or ''
        body = row.get('body') or ''
        first_line = norm(body.splitlines()[0] if body.splitlines() else '')
        by_account[account] += 1
        by_community[community] += 1
        title_words.update(tokens(title))
        body_tokens.update(tokens(body))
        if first_line:
            opening_lines.append(first_line)
        for phrase in [
            'reviewable', 'worktree', 'done criteria', 'final check', 'diff', 'trust',
            'overnight', 'scope', 'verify', 'handoff', 'review bundle', 'small scoped'
        ]:
            if phrase in body.lower() or phrase in title.lower() or phrase in (meta.get('angle') or '').lower():
                phrase_hits[phrase] += 1
        if last_account and account != last_account:
            account_switches.append({'from': last_account, 'to': account, 'timestamp': row.get('timestamp')})
        last_account = account
        post_summaries.append({
            'timestamp': row.get('timestamp'),
            'account': account,
            'community': community,
            'title': title,
            'comment_url': row.get('comment_url'),
            'opening_line': first_line,
        })

    repeated_openings = [line for line, count in Counter(opening_lines).items() if count > 1]
    top_tokens = [w for w, _ in body_tokens.most_common(20)]
    recommendations = []
    if repeated_openings:
        recommendations.append('Avoid reusing the same opening line; vary the first paragraph deliberately.')
    if recent_rows and phrase_hits['reviewable'] and phrase_hits['reviewable'] == len(recent_rows):
        recommendations.append('Keep the reviewability angle, but rotate supporting language so posts do not sound templated.')
    if by_community.get('r/ClaudeCode', 0) or by_community.get('ClaudeCode', 0):
        recommendations.append('ClaudeCode is the strongest current venue, but avoid stacking too many similar comments there without fresh thread-specific advice.')
    if len(by_account) > 1:
        recommendations.append('Treat pre-switch Clear-Past7954 posts as historical only; future retros and posting rules should optimize for the currently allowed account only.')
    recommendations.append('Before each autopost, compare the candidate body against the last 3 logged bodies and rewrite if the opening, structure, or closing feel too similar.')
    recommendations.append('Score opportunities not just on topic fit but on whether they create a distinctly new reply angle versus existing logged comments.')

    payload = {
        'generated_at': generated_at,
        'count': len(rows),
        'recent_window_count': len(recent_rows),
        'recent_window_max_age_hours': RECENT_POST_MAX_AGE_HOURS,
        'filtered_structural_records': filtered_structural,
        'by_account': dict(by_account),
        'by_community': dict(by_community),
        'top_title_words': [w for w, _ in title_words.most_common(15)],
        'top_body_tokens': top_tokens,
        'phrase_hits': dict(phrase_hits),
        'repeated_openings': repeated_openings,
        'account_switches': account_switches,
        'recent_posts': post_summaries[-10:],
        'recommendations': recommendations,
    }
    _write_json_pair(payload)

    md = [
        '# Reddit Post Analysis',
        '',
        f'- Generated: {payload["generated_at"]}',
        f'- Logged posts analyzed: {len(rows)} total / {len(recent_rows)} recent-window posts',
        f'- Recent window max age: {RECENT_POST_MAX_AGE_HOURS}h',
        f'- Filtered {filtered_structural} cadence/structural records',
        f'- Accounts seen: {", ".join(f"{k} ({v})" for k, v in by_account.items()) if by_account else "none in recent window"}',
        f'- Communities seen: {", ".join(f"{k} ({v})" for k, v in by_community.items()) if by_community else "none in recent window"}',
        '',
        '## What the past posts actually say',
        f'- Most repeated themes: {", ".join(k for k, v in phrase_hits.most_common(8) if v > 0) or "none in recent window"}',
        f'- Frequent body tokens: {", ".join(top_tokens[:12]) or "none in recent window"}',
        '',
        '## Repetition risks',
    ]
    if repeated_openings:
        md.extend([f'- Reused opening line: "{line}"' for line in repeated_openings])
    else:
        md.append('- No exact repeated opening line detected in the recent window.')
    md.extend(['', '## Recommendations'])
    md.extend([f'- {r}' for r in recommendations])
    md.extend(['', '## Recent post log rollup'])
    if post_summaries:
        for row in post_summaries[-6:]:
            md.append(f"- {row['timestamp']} — u/{row['account']} — {row['community']} — {row['title']} — {row['comment_url']}")
    else:
        md.append(f'- No recent-window Reddit posts in the last {RECENT_POST_MAX_AGE_HOURS} hours.')
    _write_markdown_pair('\n'.join(md) + '\n')

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
