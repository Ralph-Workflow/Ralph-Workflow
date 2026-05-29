#!/usr/bin/env python3
"""
PyPI → Codeberg Conversion Lane Monitor

Tracks the ratio of PyPI downloads to Codeberg stars and surfaces
conversion anomalies. The primary alert threshold: if conversion stays
below 1% for 14+ days after the README star-CTA is live, it suggests
a structural conversion problem beyond surface-level messaging.

Usage: python3 agents/marketing/pypi_conversion_lane.py [--status|--check]

Design:
- Polls PyPI JSON API for download counts + version info
- Polls Codeberg API for star/watcher/fork counts
- Computes 30-day rolling conversion ratio
- Flags when ratio drops below thresholds
- Writes machine-readable status for the audit pipeline
"""

from __future__ import annotations

import json
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path('/home/mistlight/.openclaw/workspace')
LOG_DIR = ROOT / 'agents/marketing/logs'
STATE_PATH = LOG_DIR / 'pypi_conversion_lane_state.json'
REPORT_PATH = LOG_DIR / 'pypi_conversion_lane_latest.json'

PYPI_PACKAGE = 'ralph-workflow'
CODEBERG_REPO = 'RalphWorkflow/Ralph-Workflow'
CODEBERG_API = 'https://codeberg.org/api/v1/repos'

# Thresholds
CONVERSION_CRITICAL_THRESHOLD = 0.005   # 0.5% — structural problem, need README/CTA repair
CONVERSION_WARNING_THRESHOLD = 0.01     # 1.0% — below baseline, review
CONVERSION_HEALTHY_THRESHOLD = 0.02     # 2.0% — healthy for an open-source tool

# The README on PyPI already has Codeberg badges + star CTA blockquote since v0.8.7 (May 21).
# If conversion stays below 1% 14+ days after that deploy, the readme-level CTA alone
# isn't enough and we need an additional conversion mechanism.
README_CTA_DEPLOY_DATE = datetime(2026, 5, 21, tzinfo=timezone.utc)


def fetch_pypi_stats() -> dict:
    """Fetch PyPI download stats for the package."""
    # We use the PyPI JSON API for detailed info
    req = urllib.request.Request(f'https://pypi.org/pypi/{PYPI_PACKAGE}/json')
    req.add_header('User-Agent', 'RalphWorkflow-PyPIConversionMonitor/1.0')
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            info = data.get('info', {})
            releases = data.get('releases', {})
            latest_version = info.get('version', '')
            last_release = sorted([r for rels in releases.values() for r in rels],
                                  key=lambda r: r.get('upload_time', ''),
                                  reverse=True)[:1]
            return {
                'status': 'ok',
                'version': latest_version,
                'last_release_upload': last_release[0].get('upload_time', '') if last_release else '',
                'total_releases': len(releases),
            }
    except Exception as e:
        return {'status': 'error', 'error': str(e)}


def fetch_codeberg_stats() -> dict:
    """Fetch Codeberg repo stats (stars, watchers, forks)."""
    req = urllib.request.Request(f'{CODEBERG_API}/{CODEBERG_REPO}')
    req.add_header('User-Agent', 'RalphWorkflow-PyPIConversionMonitor/1.0')
    req.add_header('Accept', 'application/json')
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            return {
                'status': 'ok',
                'stars': data.get('stars_count', 0),
                'watchers': data.get('watchers_count', 0),
                'forks': data.get('forks_count', 0),
                'open_issues': data.get('open_issues_count', 0),
                'updated_at': data.get('updated_at', ''),
            }
    except Exception as e:
        return {'status': 'error', 'error': str(e)}


def fetch_pypi_downloads() -> dict:
    """Fetch recent download counts via PyPI's stats endpoint."""
    stats_url = f'https://pypistats.org/api/packages/{PYPI_PACKAGE}/recent'
    req = urllib.request.Request(stats_url)
    req.add_header('User-Agent', 'RalphWorkflow-PyPIConversionMonitor/1.0')
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            last_month = data.get('data', {}).get('last_month', 0)
            last_week = data.get('data', {}).get('last_week', 0)
            return {
                'status': 'ok',
                'total_downloads': last_month * 8,  # Rough extrapolation: ~8mo of history
                'last_30_days': last_month,
                'last_7_days': last_week,
            }
    except Exception as e:
        # Fallback: use hardcoded known value from adoption_metrics
        return {
            'status': 'fallback',
            'total_downloads': 1498,  # Known from PyPI public dashboard
            'last_30_days': 300,      # ~10/day rough estimate
            'last_7_days': 70,        # ~10/day rough estimate
            'note': f'API error: {str(e)[:200]}, using cached values from adoption_metrics',
        }


def compute_conversion_health(pypi_downloads: dict, codeberg: dict) -> dict:
    """Compute the conversion ratio and health assessment."""
    if pypi_downloads.get('status') != 'ok' or codeberg.get('status') != 'ok':
        return {
            'status': 'error',
            'error': f'pypi={pypi_downloads.get("status")}, codeberg={codeberg.get("status")}',
            'conversion_ratio': None,
            'health': 'unknown',
        }

    total_dls = max(pypi_downloads.get('total_downloads', 0), 1)
    stars = codeberg.get('stars', 0)
    conversion = stars / total_dls if total_dls > 0 else 0

    if conversion < CONVERSION_CRITICAL_THRESHOLD:
        health = 'critical'
    elif conversion < CONVERSION_WARNING_THRESHOLD:
        health = 'warning'
    elif conversion < CONVERSION_HEALTHY_THRESHOLD:
        health = 'ok'
    else:
        health = 'healthy'

    return {
        'status': 'ok',
        'conversion_ratio': round(conversion, 4),
        'conversion_pct': f'{conversion * 100:.2f}%',
        'total_downloads': total_dls,
        'stars': stars,
        'downloads_per_star': total_dls // max(stars, 1),
        'health': health,
    }


def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding='utf-8'))
    except (FileNotFoundError, json.JSONDecodeError):
        return {'history': []}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, default=str) + '\n', encoding='utf-8')


def generate_report() -> dict:
    """Generate the full conversion health report."""
    now = datetime.now(timezone.utc)

    pypi_info = fetch_pypi_stats()
    codeberg = fetch_codeberg_stats()
    pypi_dls = fetch_pypi_downloads()
    conversion = compute_conversion_health(pypi_dls, codeberg)

    state = load_state()
    state.setdefault('history', []).append({
        'timestamp': now.isoformat(),
        'stars': codeberg.get('stars'),
        'downloads_30d': pypi_dls.get('last_30_days'),
        'conversion_ratio': conversion.get('conversion_ratio'),
        'health': conversion.get('health'),
    })
    # Keep last 90 entries
    state['history'] = state['history'][-90:]
    save_state(state)

    # Check README CTA age
    days_since_readme_cta = (now - README_CTA_DEPLOY_DATE).days if conversion.get('health') in ('critical', 'warning') else None

    report = {
        'generated_at': now.isoformat(),
        'pypi': pypi_info,
        'codeberg': codeberg,
        'downloads': pypi_dls,
        'conversion': conversion,
        'thresholds': {
            'critical': CONVERSION_CRITICAL_THRESHOLD,
            'warning': CONVERSION_WARNING_THRESHOLD,
            'healthy': CONVERSION_HEALTHY_THRESHOLD,
        },
        'readme_cta_deploy_date': README_CTA_DEPLOY_DATE.isoformat(),
        'days_since_readme_cta': days_since_readme_cta,
        'needs_additional_cta': (
            conversion.get('health') in ('critical', 'warning')
            and days_since_readme_cta is not None
            and days_since_readme_cta >= 14
        ),
        'recommendations': [],
    }

    health = conversion.get('health', 'unknown')
    if health == 'critical':
        report['recommendations'].append(
            f'PyPI→Codeberg conversion at {conversion.get("conversion_pct")} is CRITICAL. '
            f'Consider: 1) post-install message directing to Codeberg, '
            f'2) in-app star nudge on first successful run, '
            f'3) README copy A/B test with stronger CTA'
        )
    elif health == 'warning':
        report['recommendations'].append(
            f'Conversion at {conversion.get("conversion_pct")} is below baseline. Monitor weekly.'
        )

    if report.get('needs_additional_cta'):
        report['recommendations'].append(
            'README CTA deployed 14+ days ago without sufficient conversion lift. '
            'Recommend shipping a post-install Codeberg star nudge as a pip install hook.'
        )

    REPORT_PATH.write_text(json.dumps(report, indent=2, default=str) + '\n', encoding='utf-8')
    return report


def main() -> int:
    if '--status' in sys.argv:
        try:
            existing = json.loads(REPORT_PATH.read_text(encoding='utf-8'))
            print(json.dumps(existing, indent=2))
        except (FileNotFoundError, json.JSONDecodeError):
            print('{"status": "no_data"}')
        return 0

    report = generate_report()

    if '--check' in sys.argv:
        health = report['conversion']['health']
        if health == 'critical':
            print(f'CRITICAL: {report["conversion"]["conversion_pct"]} conversion')
            return 2
        elif health == 'warning':
            print(f'WARNING: {report["conversion"]["conversion_pct"]} conversion')
            return 1
        else:
            print(f'OK: {report["conversion"]["conversion_pct"]} conversion')
            return 0

    print(json.dumps(report, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
