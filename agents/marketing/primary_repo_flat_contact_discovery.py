#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html import unescape
from pathlib import Path
from typing import Any

ROOT = Path('/home/mistlight/.openclaw/workspace')
LOG_DIR = ROOT / 'agents/marketing/logs'
DRAFTS_DIR = ROOT / 'drafts'
LATEST_JSON = LOG_DIR / 'primary_repo_flat_contact_discovery_latest.json'
LATEST_MD = DRAFTS_DIR / 'primary_repo_flat_contact_discovery_latest.md'

USER_AGENT = 'Mozilla/5.0 (compatible; RalphWorkflow marketing loop)'
ACTIONABLE_HINTS = ('contact', 'about', 'work-with', 'consult', 'hire', 'book', 'team', 'company', 'impressum', 'advertise', 'sponsor', 'partner', 'media')
FOLLOW_ON_PAGE_HINTS = ('faq', 'help', 'support', 'feedback', 'docs', 'troubleshooting')
WEAK_ROLE_EMAIL_PREFIXES = ('legal@', 'privacy@', 'support@', 'security@', 'compliance@', 'terms@', 'info@noreply', 'no-reply@', 'noreply@')
KNOWN_PLACEHOLDER_EMAILS = {
    'you@example.com',
    'jane@acme.com',
    'you@work.com',
    'you@company.com',
}
KNOWN_PLACEHOLDER_EMAIL_DOMAINS = {
    'acme.com',
    'example.com',
    'company.com',
    'domain.com',
}
KNOWN_PLACEHOLDER_EMAIL_LOCALPARTS = {
    'example',
    'demo',
    'sample',
    'yourname',
    'email',
}
COMMON_PATHS = (
    '/contact',
    '/contact/',
    '/contact-us',
    '/contact-us/',
    '/about',
    '/about/',
    '/impressum',
    '/impressum/',
    '/work-with-me',
    '/work-with-me/',
    '/advertise',
    '/advertise/',
)
HUB_PATHS = (
    '/resources',
    '/resources/',
    '/docs',
    '/docs/',
    '/help',
    '/help/',
    '/support',
    '/support/',
)
NOISY_SUFFIXES = ('.js', '.css', '.svg', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.ico', '.pdf', '.woff', '.woff2', '.ttf', '.otf')
NOISY_HOSTS = {'schema.org', 'fonts.googleapis.com', 'fonts.gstatic.com'}
LIVE_EXTERNAL_STATUSES = {'executed', 'sent', 'submitted', 'published', 'launched'}
PUBLISHER_CONTACT_ACTION_TYPES = {
    'publisher_email_outreach',
    'publisher_contact_form_submission',
    'publisher_feedback_form_submission',
}


@dataclass(frozen=True)
class Target:
    name: str
    article_url: str
    root_url: str
    hook: str
    reason: str
    outreach_subject: str
    contact_urls: tuple[str, ...] = ()
    explicit_emails: tuple[str, ...] = ()


TARGETS = [
    Target(
        name='ctxt.dev / Signum',
        article_url='https://ctxt.dev/posts/en/tasks-are-not-goals',
        root_url='https://ctxt.dev/',
        hook='Tasks Are Not Goals (2026-05-22)',
        reason='Contract-first, evidence-first agent workflow audience overlaps strongly with Ralph Workflow.',
        outreach_subject='RalphWorkflow for your next contract-first agent workflow roundup',
    ),
    Target(
        name='AXME Code',
        article_url='https://code.axme.ai/',
        root_url='https://code.axme.ai/',
        hook='Agent-native durability and Claude Code memory/safety positioning',
        reason='Adjacent reliability problem from the workflow side; likely receptive to comparative mention.',
        outreach_subject='RalphWorkflow as a complementary workflow layer to durable agent runs',
    ),
    Target(
        name='WyeWorks',
        article_url='https://www.wyeworks.com/blog',
        root_url='https://www.wyeworks.com/',
        hook='Workflow-engineering positioning and AI delivery consulting audience',
        reason='Publishes on AI workflow engineering and can plausibly cite open-source workflow systems.',
        outreach_subject='RalphWorkflow might fit your workflow-engineering follow-up',
    ),
    Target(
        name='Bollwerk / Werkstatt',
        article_url='https://bollwerk.ai/blog/werkstatt-open-source/',
        root_url='https://bollwerk.ai/',
        hook='Werkstatt open-source workflow/skill discipline positioning',
        reason='Adjacent coding-agent workflow audience; good fit for an adjacent-system mention or comparison.',
        outreach_subject='RalphWorkflow as an adjacent workflow-system reference for Werkstatt readers',
    ),
    Target(
        name='ToolChase',
        article_url='https://toolchase.com/blog/best-ai-coding-tools-2026/',
        root_url='https://toolchase.com/',
        hook='AI coding tools comparison page already covering Claude Code, Codex, Cursor, and Aider',
        reason='Direct comparison/discovery audience already evaluating AI coding tools and adjacent workflow choices.',
        outreach_subject='Ralph Workflow for your next AI coding tools comparison refresh',
    ),
    Target(
        name='Beam',
        article_url='https://getbeam.dev/blog/ai-coding-agents-comparison-2026.html',
        root_url='https://getbeam.dev/',
        hook='Claude Code vs Cursor vs Codex comparison for terminal-first builders',
        reason='Highly adjacent audience already thinking about agent autonomy, workflow fit, and terminal-native execution.',
        outreach_subject='Ralph Workflow as a workflow-system reference for your coding agents comparison',
    ),
    Target(
        name='NxCode',
        article_url='https://www.nxcode.io/resources/news/codex-vs-cursor-vs-claude-code-2026',
        root_url='https://www.nxcode.io/',
        hook='Codex vs Cursor vs Claude Code comparison with workflow-specific tradeoffs',
        reason='Direct AI-coding comparison audience already evaluating when autonomous background agents beat interactive editors.',
        outreach_subject='Ralph Workflow as a workflow-system addition to your AI coding tools comparison',
        contact_urls=(
            'https://www.nxcode.io/ar/contact',
            'https://www.nxcode.io/company/about',
            'https://www.nxcode.io/company/careers',
            'https://www.nxcode.io/docs/troubleshooting',
        ),
    ),
    Target(
        name='TIMEWELL',
        article_url='https://timewell.jp/en/columns/ai-coding-tools-complete-benchmark-2026',
        root_url='https://timewell.jp/en/',
        hook='AI coding tools benchmark focused on agent-style development and review discipline',
        reason='Strong adjacent audience for process-layer framing because the article already centers on briefing, review, and multi-tool operating models.',
        outreach_subject='Ralph Workflow for your next AI coding tools benchmark update',
        contact_urls=(
            'https://timewell.jp/en/contact',
            'https://timewell.jp/en/company',
            'https://timewell.jp/en/team',
        ),
        explicit_emails=(
            'timewell@timewell.jp',
        ),
    ),
    Target(
        name='AI Saying',
        article_url='https://aisaying.net/knowledge/article/ai-coding-tools-comparison-matrix',
        root_url='https://aisaying.net/',
        hook='AI Coding Tools Compared: The Complete 2026 Matrix (14 Tools Ranked)',
        reason='Fresh AI coding tools comparison audience already evaluating terminal agents, editors, and workflow tradeoffs.',
        outreach_subject='Ralph Workflow for your AI coding tools comparison update',
    ),
    Target(
        name='Toolradar',
        article_url='https://toolradar.com/guides/best-ai-coding-tools',
        root_url='https://toolradar.com/',
        hook='Best AI Coding Tools in 2026',
        reason='B2B buyer guide audience already comparing coding-agent tradeoffs and adjacent workflow layers.',
        outreach_subject='Ralph Workflow as a workflow-system addition to your AI coding tools guide',
        contact_urls=(
            'https://toolradar.com/contact',
            'https://toolradar.com/advertise',
            'https://toolradar.com/editorial-policy',
        ),
    ),
    Target(
        name='TLDL',
        article_url='https://www.tldl.io/resources/ai-coding-tools-2026',
        root_url='https://www.tldl.io/',
        hook='AI Coding Tools Compared (2026): Cursor vs Claude Code vs Copilot — Benchmarks & Pricing',
        reason='Comparison audience overlaps strongly with evaluators looking for autonomous coding workflows, not just single tools.',
        outreach_subject='Ralph Workflow for your next AI coding tools comparison refresh',
    ),
    Target(
        name='Requesty',
        article_url='https://www.requesty.ai/blog/agentic-coding-tools-compared-2026-claude-code-cursor-codex-aider',
        root_url='https://www.requesty.ai/',
        hook='Agentic Coding Tools Compared (2026): Claude Code, Cursor, Codex, Aider, and the Gateway That Connects Them',
        reason='High-intent agentic-coding comparison audience already evaluating tool stacks, gateways, and workflow control layers.',
        outreach_subject='Ralph Workflow for your next agentic coding tools comparison refresh',
        explicit_emails=(
            'sales@requesty.ai',
        ),
    ),
    Target(
        name='ComputingForGeeks',
        article_url='https://computingforgeeks.com/opencode-vs-claude-code-vs-cursor/',
        root_url='https://computingforgeeks.com/',
        hook='OpenCode vs Claude Code vs Cursor: AI Coding Agents Compared (2026)',
        reason='Large Linux/DevOps engineering audience already reading practical AI coding workflow comparisons.',
        outreach_subject='Ralph Workflow for your next AI coding agents comparison update',
        contact_urls=(
            'https://computingforgeeks.com/contact-us/',
            'https://computingforgeeks.com/about-us/',
        ),
    ),
    Target(
        name='SOTAAZ',
        article_url='https://www.sotaaz.com/post/cursor-vs-claude-code-vs-codex-2026-en',
        root_url='https://www.sotaaz.com/',
        hook='2026 AI Coding Tool War: Cursor vs Claude Code vs Codex — Hands-On Comparison',
        reason='AI-engineer readers already consuming hands-on coding-agent comparisons and adjacent orchestration pieces.',
        outreach_subject='Ralph Workflow for your next AI coding tools comparison update',
        explicit_emails=(
            'support@oncreative.ai',
        ),
    ),
    Target(
        name='SitePoint',
        article_url='https://www.sitepoint.com/claude-code-vs-codex-2026/',
        root_url='https://www.sitepoint.com/',
        hook='Claude Code vs Codex: A Developer\'s 2026 Workflow Comparison',
        reason='Large developer publication already comparing AI coding workflows, with clear editorial contact paths and a strong fit for workflow-layer follow-up.',
        outreach_subject='Ralph Workflow for your next AI coding workflow comparison refresh',
        contact_urls=(
            'https://www.sitepoint.com/contact-us/',
            'https://www.sitepoint.com/about-us/',
        ),
        explicit_emails=(
            'support@sitepoint.com',
        ),
    ),
    Target(
        name='Dupple',
        article_url='https://dupple.com/learn/claude-code-vs-cursor',
        root_url='https://dupple.com/',
        hook='Claude Code vs Cursor in 2026: Which AI Coding Tool Should You Use?',
        reason='High-reach developer and AI-tools audience already evaluating coding workflows, with a strong newsletter-plus-directory surface that can route qualified evaluators to Codeberg-first workflow comparisons.',
        outreach_subject='Ralph Workflow for your next AI coding workflow comparison refresh',
        contact_urls=(
            'https://dupple.com/about',
            'https://dupple.com/top-tools',
            'https://dupple.com/dupple-x',
        ),
        explicit_emails=(
            'louis@dupple.com',
            'techpresso@dupple.com',
        ),
    ),
    Target(
        name='Codersera',
        article_url='https://codersera.com/blog/ai-coding-agents-complete-guide-2026/',
        root_url='https://codersera.com/',
        hook='AI Coding Agents in 2026: The Complete Guide',
        reason='Fresh AI coding agent guide with strong evaluator intent and a public support path that can carry a Codeberg-first workflow addition.',
        outreach_subject='Ralph Workflow for your next AI coding agents guide refresh',
        contact_urls=(
            'https://codersera.com/about-us',
            'https://codersera.com/blog',
        ),
        explicit_emails=(
            'support@codersera.com',
        ),
    ),
    Target(
        name='Codivox',
        article_url='https://codivox.com/comparisons/ai-coding-tools/',
        root_url='https://codivox.com/',
        hook='AI Coding Tools Comparison 2026',
        reason='Direct AI coding comparison page with a public email path and a fit for workflow-layer positioning.',
        outreach_subject='Ralph Workflow for your AI coding tools comparison page',
    ),
    Target(
        name='Morph',
        article_url='https://www.morphllm.com/best-ai-coding-agents-2026',
        root_url='https://www.morphllm.com/',
        hook='14 Best AI Coding Agents (2026): Full Rankings',
        reason='Fresh AI coding agent rankings page already comparing terminal agents and multi-agent workflows; good fit for a workflow-system addition that points evaluators to Codeberg first.',
        outreach_subject='Ralph Workflow for your AI coding agents rankings page',
        contact_urls=(
            'https://www.morphllm.com/contact',
            'https://www.morphllm.com/docs',
        ),
        explicit_emails=(
            'info@morphllm.com',
        ),
    ),
]


def http_get(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(
        url,
        headers={
            'User-Agent': USER_AGENT,
            'Accept': 'text/html,application/json;q=0.9,*/*;q=0.8',
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode('utf-8', errors='ignore')
    except Exception:
        return ''


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
        return {}


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone().replace(tzinfo=None)
    return dt


def _chosen_action_type(payload: dict[str, Any]) -> str:
    chosen_action = payload.get('chosen_action')
    if not isinstance(chosen_action, dict):
        chosen_action = {}
    return str(
        chosen_action.get('type')
        or payload.get('type')
        or payload.get('action_type')
        or payload.get('action')
        or ''
    ).strip()


def _recent_contact_targets(now: datetime, *, days: int = 7, log_dir: Path = LOG_DIR) -> set[str]:
    cutoff = now - timedelta(days=days)
    targets: set[str] = set()
    for path in log_dir.glob('marketing_*.json'):
        if any(token in path.name for token in ('latest', 'workflow_audit', 'loop_runner', 'loop_verifier', 'independent_verification', 'momentum_watchdog', 'positioning_audit')):
            continue
        payload = _load_json(path)
        dt = _parse_dt(payload.get('timestamp') or payload.get('timestamp_utc'))
        if dt is None:
            dt = datetime.fromtimestamp(path.stat().st_mtime)
        if dt < cutoff:
            continue

        result = payload.get('result') or {}
        status = str(payload.get('status') or result.get('status') or '').lower()
        ok = bool(payload.get('ok') or result.get('ok') or status in LIVE_EXTERNAL_STATUSES)
        if not ok:
            continue

        if _chosen_action_type(payload) not in PUBLISHER_CONTACT_ACTION_TYPES:
            continue

        target_name = str(payload.get('target') or (payload.get('chosen_action') or {}).get('target') or '').strip()
        if target_name:
            targets.add(target_name)
    return targets


def normalize_url(value: str) -> str:
    cleaned = unescape((value or '').strip())
    if not cleaned:
        return ''
    cleaned = cleaned.replace('\\', '')
    cleaned = cleaned.rstrip(').,:;]')
    if cleaned.startswith('//'):
        cleaned = f'https:{cleaned}'
    parsed = urllib.parse.urlparse(cleaned)
    if parsed.scheme in {'http', 'https'}:
        cleaned = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path, '', parsed.query, ''))
    return cleaned.rstrip(' /')


def _is_social_share_url(parsed: urllib.parse.ParseResult) -> bool:
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if host in {'x.com', 'www.x.com', 'twitter.com', 'www.twitter.com'}:
        return path.startswith('/intent/')
    if host in {'linkedin.com', 'www.linkedin.com'}:
        if path.startswith('/sharing/') or path.startswith('/sharearticle'):
            return True
    return False


def _github_issue_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    if host not in {'github.com', 'www.github.com'}:
        return ''
    parts = [part for part in parsed.path.split('/') if part]
    if len(parts) < 2:
        return ''
    owner, repo = parts[0], parts[1]
    if any(token in {owner.lower(), repo.lower()} for token in {'orgs', 'organizations', 'topics', 'features', 'marketplace', 'sponsors', 'settings'}):
        return ''
    return f'https://github.com/{owner}/{repo}/issues/new'


def resolve_url(base: str, raw: str) -> str:
    raw = normalize_url(raw)
    if not raw:
        return ''
    if raw.startswith('mailto:'):
        return raw
    return normalize_url(urllib.parse.urljoin(base, raw))


def _same_site(host_a: str, host_b: str) -> bool:
    def canon(host: str) -> str:
        host = host.lower()
        return host[4:] if host.startswith('www.') else host
    return canon(host_a) == canon(host_b)


def _looks_placeholder_email(value: str) -> bool:
    lowered = (value or '').strip().lower()
    if not lowered:
        return True
    if lowered in KNOWN_PLACEHOLDER_EMAILS:
        return True
    if '@' not in lowered:
        return True
    local_part, domain = lowered.rsplit('@', 1)
    if local_part in KNOWN_PLACEHOLDER_EMAIL_LOCALPARTS:
        return True
    return lowered.endswith('@example.com') or domain in KNOWN_PLACEHOLDER_EMAIL_DOMAINS


def _is_weak_role_email(value: str) -> bool:
    lowered = (value or '').strip().lower()
    if not lowered:
        return False
    return lowered.startswith(WEAK_ROLE_EMAIL_PREFIXES)


def _extract_candidate_urls(base_url: str, text: str) -> list[str]:
    hrefs = set(re.findall(r'href=["\']([^"\']+)["\']', text, re.I))
    hrefs.update(re.findall(r'https://[^\s"\'<>]+', text))
    urls: list[str] = []
    seen: set[str] = set()
    for raw in sorted(hrefs):
        url = resolve_url(base_url, raw)
        if not url or url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def extract_follow_on_contact_urls(base_url: str, text: str) -> list[str]:
    base_host = urllib.parse.urlparse(base_url).netloc.lower()
    urls: list[str] = []
    seen: set[str] = set()
    for url in _extract_candidate_urls(base_url, text):
        lowered = url.lower()
        parsed = urllib.parse.urlparse(url)
        host = parsed.netloc.lower()
        if parsed.scheme not in {'http', 'https'} or not host:
            continue
        if not _same_site(base_host, host):
            continue
        path = parsed.path.lower().strip('/')
        if not path:
            continue
        if not any(hint in path for hint in FOLLOW_ON_PAGE_HINTS):
            continue
        normalized = normalize_url(url)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        urls.append(normalized)
    return urls


def extract_channels(base_url: str, text: str) -> list[dict[str, str]]:
    channels: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(channel_type: str, value: str, label: str) -> None:
        value = (value or '').strip()
        if not value:
            return
        key = (channel_type, value)
        if key in seen:
            return
        seen.add(key)
        channels.append({'type': channel_type, 'value': value, 'label': label})

    for email in sorted(set(re.findall(r'mailto:([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})', text, re.I))):
        if not _looks_placeholder_email(email):
            add('email', email, 'email')
    for email in sorted(set(re.findall(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', text))):
        if not _looks_placeholder_email(email):
            add('email', email, 'email')

    base_host = urllib.parse.urlparse(base_url).netloc.lower()
    for url in _extract_candidate_urls(base_url, text):
        lowered = url.lower()
        lowered = url.lower()
        if not url or lowered.startswith('mailto:'):
            continue
        parsed = urllib.parse.urlparse(url)
        host = parsed.netloc.lower()
        if parsed.scheme not in {'http', 'https'} or not host:
            continue
        if host in NOISY_HOSTS or any(parsed.path.lower().endswith(suffix) for suffix in NOISY_SUFFIXES):
            continue
        if _is_social_share_url(parsed):
            continue
        if host in {'linkedin.com', 'www.linkedin.com'}:
            add('linkedin', url, 'LinkedIn')
            continue
        if host in {'twitter.com', 'www.twitter.com', 'x.com', 'www.x.com'}:
            add('x', url, 'X/Twitter')
            continue
        github_issue_url = _github_issue_url(url)
        if github_issue_url:
            add('github_issue', github_issue_url, 'GitHub issue')
            continue
        if host in {'t.me', 'telegram.me', 'www.telegram.me'}:
            add('telegram', url, 'Telegram')
            continue
        if not _same_site(base_host, host):
            continue
        path = parsed.path.lower().strip('/')
        if any(hint in path for hint in ACTIONABLE_HINTS) or path in {'', 'index.html', 'index.htm'}:
            if 'work-with' in path or 'consult' in path:
                label = 'work with me page'
            elif 'advertise' in path or 'sponsor' in path or 'partner' in path:
                label = 'advertise page'
            elif 'about' in path:
                label = 'about page'
            elif 'contact' in path or 'hire' in path or 'book' in path or 'impressum' in path:
                label = 'contact page'
            else:
                label = 'website'
            add('website', url, label)

    lowered_text = text.lower()
    feedback_markers = (
        'send feedback',
        'submit feedback',
        'feedback form',
        'tell us what you think',
    )
    has_feedback_widget = (
        any(marker in lowered_text for marker in feedback_markers)
        and ('submitfeedback(' in lowered_text or '/api/feedback' in lowered_text or 'id="fb-form"' in lowered_text)
    )
    has_contact_form = bool(re.search(r'<form[^>]*>', text, re.I)) and any(
        token in lowered_text for token in ('contact us', 'send feedback', 'submit feedback', 'message us', 'tell us what you think')
    )
    if has_feedback_widget:
        add('website', normalize_url(base_url), 'feedback form')
    elif has_contact_form:
        add('website', normalize_url(base_url), 'contact form')
    return channels


def prioritize(channels: list[dict[str, str]]) -> list[dict[str, str]]:
    def key(channel: dict[str, str]) -> tuple[int, int, int, int, str]:
        channel_type = channel.get('type', '')
        label = channel.get('label', '').lower()
        value = channel.get('value', '').lower()
        if channel_type == 'email':
            type_rank = 2 if _is_weak_role_email(value) else 0
        else:
            type_rank = {'website': 1, 'telegram': 2, 'github_issue': 3, 'x': 4, 'linkedin': 5}.get(channel_type, 9)
        if 'work with me' in label or 'consult' in label:
            hint_rank = 0
        elif 'contact' in label or any(h in value for h in ACTIONABLE_HINTS):
            hint_rank = 1
        else:
            hint_rank = 2
        telegram_rank = 0 if channel_type == 'telegram' else 1
        weak_email_rank = 1 if channel_type == 'email' and _is_weak_role_email(value) else 0
        return (type_rank, hint_rank, telegram_rank, weak_email_rank, value)

    ordered = sorted(channels, key=key)
    pruned: list[dict[str, str]] = []
    seen = set()
    website_count = 0
    for channel in ordered:
        value = channel['value']
        if value in seen:
            continue
        seen.add(value)
        if channel['type'] == 'website':
            website_count += 1
            if website_count > 3:
                continue
        pruned.append(channel)
    return pruned


def enrich_target(target: Target) -> dict[str, Any]:
    article_html = http_get(target.article_url)
    root_html = http_get(target.root_url)
    combined = []
    channels: list[dict[str, str]] = []

    if article_html:
        combined.append(article_html)
        channels.extend(extract_channels(target.article_url, article_html))
    if root_html:
        combined.append(root_html)
        channels.extend(extract_channels(target.root_url, root_html))

    discovered_follow_on_urls: list[str] = []
    for base_url, html in ((target.article_url, article_html), (target.root_url, root_html)):
        if html:
            discovered_follow_on_urls.extend(extract_follow_on_contact_urls(base_url, html))

    for path in HUB_PATHS:
        hub_url = urllib.parse.urljoin(target.root_url, path)
        hub_html = http_get(hub_url, timeout=12)
        if not hub_html:
            continue
        combined.append(hub_html)
        discovered_follow_on_urls.extend(extract_follow_on_contact_urls(hub_url, hub_html))

    explicit_contact_urls = tuple(dict.fromkeys(url.rstrip('/') for url in target.contact_urls if url))
    follow_on_contact_urls = tuple(
        url for url in dict.fromkeys(discovered_follow_on_urls)
        if url and url.rstrip('/') not in explicit_contact_urls
    )
    explicit_contact_email_seen = False
    for email in dict.fromkeys(email.strip() for email in target.explicit_emails if email and not _looks_placeholder_email(email)):
        channels.append({'type': 'email', 'value': email, 'label': 'email'})
        explicit_contact_email_seen = True

    for url in (*explicit_contact_urls, *follow_on_contact_urls[:4]):
        html = http_get(url, timeout=12)
        if not html:
            continue
        combined.append(html)
        parsed_path = urllib.parse.urlparse(url).path.lower()
        label = 'contact page'
        if 'faq' in parsed_path:
            label = 'faq page'
        elif any(token in parsed_path for token in ('support', 'help', 'troubleshooting')):
            label = 'support page'
        elif 'feedback' in parsed_path:
            label = 'feedback page'
        elif 'about' in parsed_path:
            label = 'about page'
        elif 'advertise' in parsed_path or 'sponsor' in parsed_path or 'partner' in parsed_path:
            label = 'advertise page'
        channels.append({'type': 'website', 'value': url, 'label': label})
        extracted = extract_channels(url, html)
        if any(row.get('type') == 'email' for row in extracted):
            explicit_contact_email_seen = True
        channels.extend(extracted)

    work_with_me_seen = False
    consulting_telegram_seen = False
    for path in COMMON_PATHS:
        url = urllib.parse.urljoin(target.root_url, path)
        html = http_get(url, timeout=12)
        if not html:
            continue
        if len(html) < 200:
            continue
        normalized_url = url.rstrip('/')
        path_label = 'common contact/about path'
        lowered_url = normalized_url.lower()
        lowered_html = html.lower()
        if '/work-with-me' in lowered_url or '/consult' in lowered_url:
            path_label = 'work with me page'
            work_with_me_seen = True
        elif '/contact' in lowered_url:
            path_label = 'contact page'
        elif '/advertise' in lowered_url or '/sponsor' in lowered_url or '/partner' in lowered_url:
            path_label = 'advertise page'
        elif '/about' in lowered_url:
            path_label = 'about page'
        channels.append({'type': 'website', 'value': normalized_url, 'label': path_label})
        extracted = extract_channels(url, html)
        if work_with_me_seen and 'telegram' in lowered_html and 'send a short message in telegram' in lowered_html:
            consulting_telegram_seen = True
        channels.extend(extracted)
        if path_label == 'work with me page' and any(row.get('type') == 'telegram' for row in extracted):
            consulting_telegram_seen = True

    channels = prioritize(channels)
    actionable_website_labels = {'contact page', 'contact form', 'feedback form', 'work with me page', 'advertise page'}
    recommended = 'manual research still needed'
    if any(c['type'] == 'email' and not _is_weak_role_email(c['value']) for c in channels):
        recommended = 'email/contact send path is now identified'
    elif explicit_contact_email_seen:
        recommended = 'email/contact send path is now identified'
    elif consulting_telegram_seen:
        recommended = 'Telegram consulting contact path is explicitly confirmed'
    elif any((c['type'] == 'website' and 'feedback form' in c.get('label', '').lower()) for c in channels):
        recommended = 'public feedback-form contact path is now identified'
    elif any(c['type'] == 'website' and c.get('label', '') in actionable_website_labels for c in channels):
        recommended = 'public website contact path is now identified'
    elif any(c['type'] == 'github_issue' for c in channels):
        recommended = 'GitHub issue/PR path is now identified'
    elif any(c['type'] == 'website' for c in channels):
        recommended = 'public website contact path is now identified'
    elif any(c['type'] == 'email' for c in channels):
        recommended = 'fallback role-email contact path is identified, but website contact is preferable'
    elif any(c['type'] in {'x', 'linkedin'} for c in channels):
        recommended = 'social contact path is now identified'

    return {
        'target': target.name,
        'article_url': target.article_url,
        'root_url': target.root_url,
        'hook': target.hook,
        'reason': target.reason,
        'outreach_subject': target.outreach_subject,
        'channels': channels,
        'recommended_next_step': recommended,
    }


def write_outputs(
    now: datetime,
    findings: list[dict[str, Any]],
    *,
    omitted_recent_targets: list[str] | None = None,
) -> tuple[Path, Path]:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    dated_md = DRAFTS_DIR / f'{now.strftime("%Y-%m-%d")}_primary_repo_flat_contact_discovery.md'
    payload = {
        'generated_at': now.isoformat(),
        'targets': findings,
    }
    if omitted_recent_targets:
        payload['omitted_recent_targets'] = omitted_recent_targets
    LATEST_JSON.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')

    lines = [
        '# Ralph Workflow primary-repo-flat contact discovery',
        f'Generated: {now.isoformat(timespec="seconds")}',
        '',
        '## Why this ran',
        '- Codeberg adoption is still flat in the active window.',
        '- Same-family directory and curator bursts are already inside measurement windows.',
        '- The strongest remaining unblock was to turn the active primary-repo-flat repair into actionable public contact routes.',
        '',
        '## Reused findings',
        '- marketing_workflow_audit_latest.json → primary bottleneck is still distribution + conversion to the primary repo',
        '- adoption_metrics_latest.json → Codeberg is still the primary success gate',
        '- market_intelligence_latest.json → comparison/positioning truth reused for outreach framing',
        '',
        '## Contact-ready targets',
    ]
    if omitted_recent_targets:
        lines.extend([
            '- Omitted because live publisher outreach already shipped in the active 7-day review window: ' + ', '.join(omitted_recent_targets),
            '',
        ])
    for idx, finding in enumerate(findings, start=1):
        lines.extend([
            f'### {idx}. {finding["target"]}',
            f'- Hook: {finding["hook"]}',
            f'- Article/root: {finding["article_url"]} / {finding["root_url"]}',
            f'- Recommended next step: {finding["recommended_next_step"]}',
            f'- Suggested subject: {finding["outreach_subject"]}',
        ])
        if finding['channels']:
            lines.append('- Discovered channels:')
            for channel in finding['channels']:
                lines.append(f"  - {channel['label']}: {channel['value']}")
        else:
            lines.append('- Discovered channels: none')
        lines.append('')

    text = '\n'.join(lines).rstrip() + '\n'
    dated_md.write_text(text, encoding='utf-8')
    LATEST_MD.write_text(text, encoding='utf-8')
    return dated_md, LATEST_JSON


def main() -> int:
    now = datetime.now(UTC)
    known_target_names = {target.name for target in TARGETS}
    recent_targets = {
        name for name in _recent_contact_targets(now.replace(tzinfo=None))
        if name in known_target_names
    }
    remaining_targets = [target for target in TARGETS if target.name not in recent_targets]
    findings = [enrich_target(target) for target in remaining_targets]
    write_outputs(now, findings, omitted_recent_targets=sorted(recent_targets))
    print(json.dumps({
        'generated_at': now.isoformat(),
        'targets': findings,
        'omitted_recent_targets': sorted(recent_targets),
    }, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
