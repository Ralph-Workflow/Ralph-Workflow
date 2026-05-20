#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path('/home/mistlight/.openclaw/workspace')
LOG_JSONL = ROOT / 'agents/marketing/logs/reddit_posts.jsonl'
OUT_MD = ROOT / 'agents/marketing/logs/reddit_post_analysis.md'
OUT_JSON = ROOT / 'agents/marketing/logs/reddit_post_analysis.json'

OPENING_STOPWORDS = {'the','a','an','and','but','for','with','that','this','from','into','your','have','had','when','what','more','just','they','them','then','than','still','really'}


def norm(text: str) -> str:
    return re.sub(r'\s+', ' ', text.strip())


def tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9']+", text.lower()) if len(t) > 3 and t not in OPENING_STOPWORDS]


def load_rows() -> list[dict]:
    rows = []
    if not LOG_JSONL.exists():
        return rows
    for line in LOG_JSONL.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


RECENT_POST_WINDOW = 6


def main() -> int:
    rows = load_rows()
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        payload = {'generated_at': datetime.now().isoformat(), 'count': 0, 'status': 'no_posts'}
        OUT_JSON.write_text(json.dumps(payload, indent=2), encoding='utf-8')
        OUT_MD.write_text('# Reddit Post Analysis\n\nNo logged Reddit posts yet.\n', encoding='utf-8')
        return 0

    recent_rows = rows[-RECENT_POST_WINDOW:]

    by_account = Counter()
    by_community = Counter()
    title_words = Counter()
    opening_lines = []
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
    if phrase_hits['reviewable'] and phrase_hits['reviewable'] == len(recent_rows):
        recommendations.append('Keep the reviewability angle, but rotate supporting language so posts do not sound templated.')
    if by_community.get('r/ClaudeCode', 0) or by_community.get('ClaudeCode', 0):
        recommendations.append('ClaudeCode is the strongest current venue, but avoid stacking too many similar comments there without fresh thread-specific advice.')
    if len(by_account) > 1:
        recommendations.append('Treat pre-switch Clear-Past7954 posts as historical only; future retros and posting rules should optimize for the currently allowed account only.')
    recommendations.append('Before each autopost, compare the candidate body against the last 3 logged bodies and rewrite if the opening, structure, or closing feel too similar.')
    recommendations.append('Score opportunities not just on topic fit but on whether they create a distinctly new reply angle versus existing logged comments.')

    payload = {
        'generated_at': datetime.now().isoformat(),
        'count': len(rows),
        'recent_window_count': len(recent_rows),
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
    OUT_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')

    md = [
        '# Reddit Post Analysis',
        '',
        f'- Generated: {payload["generated_at"]}',
        f'- Logged posts analyzed: {len(rows)} total / {len(recent_rows)} recent-window posts',
        f'- Accounts seen: {", ".join(f"{k} ({v})" for k, v in by_account.items())}',
        f'- Communities seen: {", ".join(f"{k} ({v})" for k, v in by_community.items())}',
        '',
        '## What the past posts actually say',
        f'- Most repeated themes: {", ".join(k for k, _ in phrase_hits.most_common(8) if _ > 0)}',
        f'- Frequent body tokens: {", ".join(top_tokens[:12])}',
        '',
        '## Repetition risks',
    ]
    if repeated_openings:
        md.extend([f'- Reused opening line: "{line}"' for line in repeated_openings])
    else:
        md.append('- No exact repeated opening line detected.')
    md.extend(['', '## Recommendations'])
    md.extend([f'- {r}' for r in recommendations])
    md.extend(['', '## Recent post log rollup'])
    for row in post_summaries[-6:]:
        md.append(f"- {row['timestamp']} — u/{row['account']} — {row['community']} — {row['title']} — {row['comment_url']}")
    OUT_MD.write_text('\n'.join(md) + '\n', encoding='utf-8')
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
