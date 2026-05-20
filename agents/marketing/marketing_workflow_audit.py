#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

ROOT = Path('/home/mistlight/.openclaw/workspace')
OUT_DIR = ROOT / 'agents/marketing/logs'
AUDIT_MD = OUT_DIR / 'marketing_workflow_audit_latest.md'
AUDIT_JSON = OUT_DIR / 'marketing_workflow_audit_latest.json'
OUTREACH = ROOT / 'outreach-log.md'
ADOPTION = OUT_DIR / 'adoption_metrics_latest.json'
RETRO = OUT_DIR / 'reddit_post_analysis.json'
PRINCIPLES = ROOT / 'agents/marketing/MARKETING_WORKFLOW_PRINCIPLES.md'
FOUR_QUESTIONS = ROOT / 'agents/marketing/FOUR_MARKETING_QUESTIONS.md'


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    adoption = load_json(ADOPTION)
    retro = load_json(RETRO)
    outreach_text = OUTREACH.read_text(encoding='utf-8') if OUTREACH.exists() else ''

    articles = outreach_text.count('**write.as article**')
    reddit_posts = len(retro.get('recent_posts', []))
    repeated_openings = retro.get('repeated_openings', [])
    metrics = adoption.get('metrics', [])
    recent_window = adoption.get('recent_window', {})
    adoption_eval = adoption.get('evaluation', {})

    codeberg_window = recent_window.get('Codeberg', {})
    github_window = recent_window.get('GitHub', {})
    codeberg_flat = codeberg_window.get('samples', 0) >= 3 and all(codeberg_window.get(k, 0) == 0 for k in ('stars_delta_window', 'watchers_delta_window', 'forks_delta_window'))
    github_flat = github_window.get('samples', 0) >= 3 and all(github_window.get(k, 0) == 0 for k in ('stars_delta_window', 'watchers_delta_window', 'forks_delta_window'))
    repetitive_reddit = bool(repeated_openings)

    if codeberg_flat:
        bottleneck = 'distribution_and_message_to_primary_repo_conversion'
    else:
        bottleneck = 'conversion_to_free_use'

    reasons = [
        'Owned content and outreach exist, but repo/public adoption signals are still low.',
        'Codeberg is the primary repo, so primary-repo movement matters more than mirror vanity metrics.',
    ]
    if codeberg_flat:
        reasons.append('Codeberg adoption is flat across the recent measurement window, so the active tactics are not earning real adoption movement yet.')
    if github_flat:
        reasons.append('GitHub mirror adoption is also flat, which reinforces that activity is not converting anywhere meaningful yet.')
    if repetitive_reddit:
        reasons.append('Reddit body repetition risk is visible, which weakens authenticity and makes the loop less likely to learn from fresh audience response.')

    next_moves = [
        'Kill or rewrite any tactic that stays flat across the recent adoption window instead of rewarding it for mere activity.',
        'Treat Codeberg movement as the primary outcome metric; GitHub is secondary mirror evidence only.',
        'Reduce repetitive outreach patterns and keep messaging tied to real workflow pain in a native-sounding voice.',
        'Require each new marketing action to name its expected outcome, measurement window, and replacement condition if it fails.',
    ]

    repair_actions: list[dict] = []
    if codeberg_flat:
        repair_actions.append({
            'target_tactic': 'content_distribution',
            'failure_type': 'primary_repo_flat',
            'action': 'REPLACE stale content distribution repair. write.as is permanently blocked; Telegraph is primary. Real gap is (a) homepage title/description SEO tuning, (b) Telegraph posts targeting keyword gaps (unattended coding agent, AI agent orchestration CLI), (c) backlink building via directory submissions and competitor citations.',
            'kill_condition': 'Still no Codeberg delta after 7 days of new approach',
            'success_metric': 'Codeberg stars_delta_window > 0 or watchers_delta_window > 0 within 14 days',
            'priority': 1,
        })
    if github_flat:
        repair_actions.append({
            'target_tactic': 'github_mirror_outreach',
            'failure_type': 'mirror_repo_flat',
            'action': 'Ensure all public-facing content links Codeberg as primary and GitHub as mirror. If GitHub mirror remains flat, it is secondary evidence — do not allocate dedicated effort unless Codeberg is moving.',
            'kill_condition': 'N/A (mirror, not primary)',
            'success_metric': 'GitHub mirror shows any adoption delta',
            'priority': 3,
        })
    if repetitive_reddit:
        repair_actions.append({
            'target_tactic': 'reddit_post_style',
            'failure_type': 'repetitive_outreach',
            'action': 'REWRITE Reddit outreach template. Current opening has been used repeatedly. Draft 2-3 fresh openings tied to specific subreddit pain points. Do not reuse any opening across different subreddits.',
            'kill_condition': 'Same opening detected again in next audit',
            'success_metric': 'No repeated openings in next audit window',
            'priority': 2,
        })

    message_checks = {
        'what_is_it': 'free and open-source tool that orchestrates existing agents on your machine',
        'who_is_it_for': 'developers/teams with engineering work too big to babysit and too risky to trust blindly',
        'why_different': 'repo-native unattended orchestration that aims to leave substantial, reviewable output instead of just a transcript',
        'why_now': 'free to use now and useful for overnight project-scale work while you sleep',
    }

    failing_tactic_names = [
        name for name, failed in {
            'reddit_style_repetition': repetitive_reddit,
            'primary_repo_flat_window': codeberg_flat,
            'mirror_repo_flat_window': github_flat,
        }.items() if failed
    ]

    payload = {
        'generated_at': datetime.now().isoformat(),
        'current_bottleneck': bottleneck,
        'articles_logged': articles,
        'reddit_posts_analyzed': reddit_posts,
        'repeated_openings': repeated_openings,
        'adoption_metrics': metrics,
        'recent_window': recent_window,
        'adoption_evaluation': adoption_eval,
        'failing_tactics': failing_tactic_names,
        'repair_actions': repair_actions,
        'has_failing_tactics': bool(failing_tactic_names),
        'reasons': reasons,
        'next_moves': next_moves,
        'four_marketing_questions': message_checks,
    }
    AUDIT_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')

    lines = [
        '# Marketing Workflow Audit',
        '',
        f'- Generated: {payload["generated_at"]}',
        f'- Current bottleneck: **{bottleneck}**',
        f'- Owned articles logged: **{articles}**',
        f'- Reddit posts analyzed: **{reddit_posts}**',
        '',
        '## Why this is the bottleneck',
    ]
    lines += [f'- {r}' for r in reasons]
    lines += ['', '## Observed risks']
    if repeated_openings:
        lines += [f'- Repetition risk in outreach opening: "{x}"' for x in repeated_openings]
    else:
        lines.append('- No exact repeated outreach opening detected in the latest audit inputs.')
    if payload['failing_tactics']:
        lines += [f'- Failing tactic detected: {name}' for name in payload['failing_tactics']]
    lines += ['', '## Outcome evaluation']
    for platform, summary in recent_window.items():
        lines.append(
            f"- {platform}: samples={summary.get('samples', 0)}, stars {summary.get('stars_delta_window', 0):+d}, watchers {summary.get('watchers_delta_window', 0):+d}, forks {summary.get('forks_delta_window', 0):+d}"
        )
    for finding in adoption_eval.get('findings', []):
        lines.append(f'- {finding}')
    lines += ['', '## Repair actions (execute in this run)']
    for ra in repair_actions:
        lines += [
            f'- **{ra["failure_type"]}** → {ra["action"]}',
            f'  - Kill condition: {ra["kill_condition"]}',
            f'  - Success metric: {ra["success_metric"]}',
        ]
    if not repair_actions:
        lines.append('- No repair actions needed.')
    lines += ['', '## Next highest-leverage moves']
    lines += [f'- {m}' for m in next_moves]
    lines += ['', '## Four marketing questions that messaging must answer']
    lines += [f'- {k}: {v}' for k, v in message_checks.items()]
    lines += ['', '## Principle reference', f'- See `{PRINCIPLES}`', f'- See `{FOUR_QUESTIONS}`']
    AUDIT_MD.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
