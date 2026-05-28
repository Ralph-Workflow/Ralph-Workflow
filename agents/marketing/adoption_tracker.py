#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path('/home/mistlight/.openclaw/workspace')
OUT_DIR = ROOT / 'agents/marketing/logs'
OUT_JSONL = OUT_DIR / 'adoption_metrics.jsonl'
OUT_LATEST = OUT_DIR / 'adoption_metrics_latest.json'
OUT_MD = OUT_DIR / 'adoption_metrics_latest.md'

TARGETS = [
    {
        'name': 'Codeberg',
        'url': 'https://codeberg.org/api/v1/repos/RalphWorkflow/Ralph-Workflow',
        'kind': 'codeberg',
        'role': 'primary',
    },
    {
        'name': 'PyPI',
        'url': 'https://pypi.org/pypi/ralph-workflow/json',
        'kind': 'pypi',
        'role': 'package',
    },
    {
        'name': 'GitHub',
        'url': 'https://api.github.com/repos/Ralph-Workflow/Ralph-Workflow',
        'kind': 'github',
        'role': 'mirror',
    },
]

def fetch_json(url: str) -> dict:
    req = Request(url, headers={'User-Agent': 'RalphWorkflow-Marketing-Agent/1.0'})
    with urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode('utf-8'))


def fetch_pypi_stats(payload: dict, package: str = 'ralph-workflow') -> dict:
    """Fetch PyPI download statistics from pypistats.org."""
    stats: dict = {
        'recent_downloads_month': None,
        'recent_downloads_week': None,
        'recent_downloads_day': None,
        'total_downloads': None,
        'version': None,
        'package_info': {},
    }
    # Extract package info from the payload (already fetched)
    info = payload.get('info', {}) if isinstance(payload, dict) else {}
    if info:
        stats['version'] = info.get('version')
        stats['package_info'] = {
            'summary': (info.get('summary') or '')[:200],
            'keywords': info.get('keywords', ''),
            'classifiers': info.get('classifiers', [])[:5],
            'requires_python': info.get('requires_python', ''),
            'home_page': info.get('home_page', ''),
            'project_url_homepage': (info.get('project_urls') or {}).get('Homepage', ''),
        }
    # Fetch recent downloads from pypistats (cached for short windows to avoid rate limits)
    import time as _time
    cache_path = OUT_DIR / 'pypi_stats_cache.json'
    cached = None
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text())
            cache_age = _time.time() - cached.get('_fetched_at', 0)
            if cache_age < 3600:
                stats.update(cached.get('stats', {}))
                return stats
        except Exception:
            pass
    try:
        req = Request(
            f'https://pypistats.org/api/packages/{package}/recent',
            headers={'User-Agent': 'RalphWorkflow-Marketing-Agent/1.0'}
        )
        with urlopen(req, timeout=20) as r:
            raw = json.loads(r.read().decode('utf-8'))
        data = raw.get('data', {})
        stats['recent_downloads_month'] = data.get('last_month')
        stats['recent_downloads_week'] = data.get('last_week')
        stats['recent_downloads_day'] = data.get('last_day')
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps({'_fetched_at': _time.time(), 'stats': dict(stats)}))
    except Exception:
        if cached:
            stats.update(cached.get('stats', {}))  # fallback to stale cache
        else:
            pass
    return stats


def normalize(target: dict, payload: dict) -> dict:
    kind = target['kind']
    if kind == 'pypi':
        stats = fetch_pypi_stats(payload)
        return {
            'platform': target['name'],
            'downloads_month': stats.get('recent_downloads_month'),
            'downloads_week': stats.get('recent_downloads_week'),
            'downloads_day': stats.get('recent_downloads_day'),
            'total_downloads': stats.get('total_downloads'),
            'version': stats.get('version'),
            'html_url': 'https://pypi.org/project/ralph-workflow/',
        }
    if kind == 'github':
        return {
            'platform': target['name'],
            'stars': payload.get('stargazers_count'),
            'watchers': payload.get('subscribers_count', payload.get('watchers_count')),
            'forks': payload.get('forks_count'),
            'open_issues': payload.get('open_issues_count'),
            'html_url': payload.get('html_url'),
        }
    return {
        'platform': target['name'],
        'stars': payload.get('stars_count') or payload.get('stars'),
        'watchers': payload.get('watchers_count') or payload.get('watchers'),
        'forks': payload.get('forks_count') or payload.get('forks'),
        'open_issues': payload.get('open_issues_count') or payload.get('open_issues'),
        'html_url': payload.get('html_url') or payload.get('website'),
    }


def history_entries(limit: int | None = None) -> list[dict]:
    if not OUT_JSONL.exists():
        return []
    rows: list[dict] = []
    for line in OUT_JSONL.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    if limit is not None:
        rows = rows[-limit:]
    return rows


def previous_entry() -> dict | None:
    rows = history_entries(limit=1)
    return rows[-1] if rows else None


def summarize_recent_window(history: list[dict], metrics: list[dict]) -> dict:
    by_platform: dict[str, list[dict]] = {}
    for row in history:
        for metric in row.get('metrics', []):
            by_platform.setdefault(metric.get('platform', 'unknown'), []).append(metric)

    summary: dict[str, dict] = {}
    for metric in metrics:
        platform = metric['platform']
        rows = by_platform.get(platform, [])
        oldest = rows[0] if rows else metric
        latest = rows[-1] if rows else metric
        summary[platform] = {
            'samples': len(rows),
            'stars_delta_window': (latest.get('stars') or 0) - (oldest.get('stars') or 0),
            'watchers_delta_window': (latest.get('watchers') or 0) - (oldest.get('watchers') or 0),
            'forks_delta_window': (latest.get('forks') or 0) - (oldest.get('forks') or 0),
        }
    return summary


def evaluate_adoption_state(metrics: list[dict], recent_window: dict[str, dict]) -> dict:
    by_platform = {m['platform']: m for m in metrics}
    codeberg = by_platform.get('Codeberg', {})
    github = by_platform.get('GitHub', {})
    pypi = by_platform.get('PyPI', {})
    codeberg_window = recent_window.get('Codeberg', {})
    github_window = recent_window.get('GitHub', {})

    findings: list[str] = []
    failing_signals: list[str] = []
    next_focus: list[str] = []

    if codeberg_window.get('samples', 0) >= 3 and all(codeberg_window.get(k, 0) == 0 for k in ('stars_delta_window', 'watchers_delta_window', 'forks_delta_window')):
        failing_signals.append('primary_repo_flat')
        findings.append('Codeberg, the primary repo, has shown no star/watch/fork movement across the recent measurement window.')
        next_focus.append('Treat tactics that did not move Codeberg adoption as failing until they produce a measurable delta.')

    if github_window.get('samples', 0) >= 3 and all(github_window.get(k, 0) == 0 for k in ('stars_delta_window', 'watchers_delta_window', 'forks_delta_window')):
        findings.append('GitHub mirror adoption is also flat across the recent measurement window.')

    if (codeberg.get('stars') or 0) >= (github.get('stars') or 0):
        findings.append('Codeberg remains the stronger adoption surface and should stay the primary evaluation target.')

    pypi_monthly = pypi.get('downloads_month', 0) or 0
    pypi_daily = pypi.get('downloads_day', 0) or 0
    if pypi_monthly > 0:
        findings.append(f'PyPI has {pypi_monthly} downloads/month ({pypi_daily}/day) — real usage signal that repo metrics don\'t capture.')

    if not next_focus:
        next_focus.append('Keep measuring adoption deltas and replace any tactic that stays flat across the window.')

    return {
        'findings': findings,
        'failing_signals': failing_signals,
        'next_focus': next_focus,
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now().isoformat()
    metrics = []
    for target in TARGETS:
        payload = fetch_json(target['url'])
        metrics.append(normalize(target, payload))

    prev = previous_entry()
    deltas = {}
    if prev:
        prev_by_platform = {m['platform']: m for m in prev.get('metrics', [])}
        for m in metrics:
            old = prev_by_platform.get(m['platform'], {})
            deltas[m['platform']] = {
                'stars_delta': (m.get('stars') or 0) - (old.get('stars') or 0),
                'watchers_delta': (m.get('watchers') or 0) - (old.get('watchers') or 0),
                'forks_delta': (m.get('forks') or 0) - (old.get('forks') or 0),
            }
    recent_history = history_entries(limit=8)
    recent_window = summarize_recent_window(recent_history + [{'metrics': metrics}], metrics)
    evaluation = evaluate_adoption_state(metrics, recent_window)
    entry = {
        'timestamp': now,
        'metrics': metrics,
        'deltas': deltas,
        'recent_window': recent_window,
        'evaluation': evaluation,
    }
    with OUT_JSONL.open('a', encoding='utf-8') as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + '\n')
    OUT_LATEST.write_text(json.dumps(entry, indent=2, ensure_ascii=False), encoding='utf-8')

    md = ['# Adoption Metrics', '', f'- Timestamp: {now}', '']
    for m in metrics:
        d = deltas.get(m['platform'], {})
        role = next((t.get('role') for t in TARGETS if t['name'] == m['platform']), None)
        recent = recent_window.get(m['platform'], {})
        if m['platform'] == 'PyPI':
            md.extend([
                f"## {m['platform']}" + (f" ({role})" if role else ''),
                f"- Version: {m.get('version')}",
                f"- Downloads (last month): {m.get('downloads_month')}",
                f"- Downloads (last week): {m.get('downloads_week')}",
                f"- Downloads (last day): {m.get('downloads_day')}",
                f"- Total downloads: {m.get('total_downloads')}",
                f"- URL: {m.get('html_url')}",
                '',
            ])
        else:
            md.extend([
                f"## {m['platform']}" + (f" ({role})" if role else ''),
                f"- Stars: {m.get('stars')}" + (f" ({d.get('stars_delta', 0):+d})" if d else ''),
                f"- Watchers: {m.get('watchers')}" + (f" ({d.get('watchers_delta', 0):+d})" if d else ''),
                f"- Forks: {m.get('forks')}" + (f" ({d.get('forks_delta', 0):+d})" if d else ''),
                f"- Open issues: {m.get('open_issues')}",
                f"- Recent window samples: {recent.get('samples', 0)}",
                f"- Window deltas: stars {recent.get('stars_delta_window', 0):+d}, watchers {recent.get('watchers_delta_window', 0):+d}, forks {recent.get('forks_delta_window', 0):+d}",
                f"- URL: {m.get('html_url')}",
                '',
            ])
    md.extend(['## Evaluation'])
    md.extend([f"- {line}" for line in evaluation.get('findings', [])])
    if evaluation.get('failing_signals'):
        md.extend([f"- Failing signal: {signal}" for signal in evaluation['failing_signals']])
    md.extend(['', '## Next focus'])
    md.extend([f"- {line}" for line in evaluation.get('next_focus', [])])
    OUT_MD.write_text('\n'.join(md), encoding='utf-8')
    print(json.dumps(entry, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
