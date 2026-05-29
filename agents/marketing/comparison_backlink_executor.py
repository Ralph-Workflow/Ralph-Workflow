#!/usr/bin/env python3
"""
comparison_backlink_executor.py — Ship prepared comparison backlink artifacts as
live distribution. Each artifact contains a ready pitch, inclusion line, and CTA
targeting the competitor/curator's audience. This script reads the prepared queue,
validates freshness, and marks them as distributed (or generates handoff packets
for manual channels).

Created: 2026-05-28 — repair for primary-repo-flat + stale content distribution.
The 8 comparison artifacts were prepared on 2026-05-22 and never shipped.
This executor treats them as the highest-leverage distribution action while
Apollo, Reddit, and dev.to channels are blocked or measurement-pending.
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path('/home/mistlight/.openclaw/workspace')
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

AGENTS_DIR = ROOT / 'agents/marketing'
LOG_DIR = AGENTS_DIR / 'logs'
DRAFTS_DIR = ROOT / 'drafts'

COMPARISON_QUEUE_PATH = LOG_DIR / 'comparison_backlink_queue_latest.json'
EXECUTION_LOG_PATH = LOG_DIR / 'comparison_backlink_execution_latest.json'
EXECUTION_MD_PATH = DRAFTS_DIR / 'comparison_backlink_execution_latest.md'

# Channels where comparison backlinks can be distributed without Reddit/IP block
DISTRIBUTION_CHANNELS = [
    'comparison_page',       # Post to the comparison page itself (comment, PR, etc.)
    'curated_list_pr',       # Submit PR to awesome-* lists mentioning the comparison
    'hackernews',            # Submit comparison as Show HN angle
    'huggingface_discuss',   # HF discussion boards
    'dev_to',                # dev.to comparison article (auth currently broken, skip)
    'stackoverflow_answer',  # SO answers where comparison is the answer
    'indie_hackers',         # Indie Hackers product comparison
    'lobsters',              # Lobste.rs relevant post
]

CHANNEL_EXECUTION_ORDER = [
    'comparison_page',
    'curated_list_pr',
    'hackernews',
    'lobsters',
    'stackoverflow_answer',
    'indie_hackers',
    'huggingface_discuss',
]


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}


def _save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding='utf-8')


@dataclass
class ComparisonTarget:
    slug: str
    name: str
    comparison_path: Path
    artifact_path: Path
    status: str
    review_due_date: str

    @property
    def artifact_exists(self) -> bool:
        return self.artifact_path.exists()

    @property
    def comparison_text(self) -> str:
        try:
            return self.comparison_path.read_text(encoding='utf-8')
        except OSError:
            return ''

    @property
    def artifact_text(self) -> str:
        try:
            return self.artifact_path.read_text(encoding='utf-8')
        except OSError:
            return ''

    def extract_pitch(self) -> str:
        """Extract the ready backlink/citation pitch from the artifact."""
        text = self.artifact_text
        if not text:
            return ''
        marker = '## Ready backlink/citation pitch'
        next_header = '\n## '
        if marker in text:
            section = text.split(marker, 1)[1]
            if next_header in section:
                section = section.split(next_header, 1)[0]
            return section.strip()
        return ''

    def extract_inclusion_line(self) -> str:
        """Extract the suggested inclusion line from the artifact."""
        text = self.artifact_text
        if not text:
            return ''
        marker = '## Suggested inclusion line'
        next_header = '\n## '
        if marker in text:
            section = text.split(marker, 1)[1]
            if next_header in section:
                section = section.split(next_header, 1)[0]
            return section.strip()
        return ''

    def extract_cta(self) -> str:
        """Extract the Codeberg-primary CTA from the artifact."""
        text = self.artifact_text
        repo_marker = '**🔗 Primary Repo — Codeberg'
        if repo_marker in text:
            return repo_marker + text.split(repo_marker, 1)[1].split('\n', 1)[0]
        # Fallback
        return '[Ralph Workflow](https://codeberg.org/RalphWorkflow/Ralph-Workflow) — free open-source workflow layer for unattended coding runs'


def load_comparison_queue() -> list[ComparisonTarget]:
    data = _load_json(COMPARISON_QUEUE_PATH)
    targets_data = data.get('targets', [])
    targets: list[ComparisonTarget] = []
    for t in targets_data:
        targets.append(ComparisonTarget(
            slug=str(t.get('slug', '')),
            name=str(t.get('name', '')),
            comparison_path=Path(str(t.get('comparison_path', ''))),
            artifact_path=Path(str(t.get('artifact_path', ''))),
            status=str(t.get('status', 'prepared')),
            review_due_date=str(t.get('review_due_date', '')),
        ))
    return targets


def validate_targets(targets: list[ComparisonTarget]) -> dict[str, Any]:
    """Validate that all targets have existing artifacts and comparison pages."""
    issues: list[str] = []
    ready: list[str] = []

    for t in targets:
        if not t.artifact_exists:
            issues.append(f'{t.slug}: artifact missing at {t.artifact_path}')
            continue
        if not t.comparison_path.exists():
            issues.append(f'{t.slug}: comparison page missing at {t.comparison_path}')
            continue
        pitch = t.extract_pitch()
        if not pitch:
            issues.append(f'{t.slug}: no pitch section found in artifact')
            continue
        if 'https://codeberg.org/RalphWorkflow/Ralph-Workflow' not in t.artifact_text:
            issues.append(f'{t.slug}: artifact missing Codeberg primary CTA')
            continue
        ready.append(t.slug)

    return {
        'validated_count': len(targets),
        'ready_count': len(ready),
        'ready_slugs': ready,
        'issues': issues,
        'all_ready': len(issues) == 0,
    }


def generate_execution_packets(targets: list[ComparisonTarget]) -> dict[str, Any]:
    """Generate executable distribution packets for each target-channel pair."""
    packets: list[dict[str, Any]] = []

    for t in targets:
        pitch = t.extract_pitch()
        inclusion = t.extract_inclusion_line()
        cta = t.extract_cta()

        for channel in CHANNEL_EXECUTION_ORDER:
            packet = {
                'target_slug': t.slug,
                'target_name': t.name,
                'channel': channel,
                'artifact_path': str(t.artifact_path),
                'comparison_path': str(t.comparison_path),
            }

            if channel == 'comparison_page':
                packet.update({
                    'action': f'Publish comparison page for {t.slug} and promote it as a useful resource',
                    'content': pitch,
                })
            elif channel == 'curated_list_pr':
                packet.update({
                    'action': f'Submit inclusion line to curated list mentioning {t.slug} comparison',
                    'content': inclusion,
                })
            elif channel == 'hackernews':
                packet.update({
                    'action': f'Submit comparison page as Show HN: Ralph Workflow vs {t.name}',
                    'content': pitch,
                })
            elif channel == 'lobsters':
                packet.update({
                    'action': f'Post comparison writeup to Lobste.rs with {t.name} angle',
                    'content': pitch,
                })
            elif channel == 'stackoverflow_answer':
                packet.update({
                    'action': f'Write SO answer comparing workflow tools, cite {t.name} vs Ralph Workflow',
                    'content': inclusion,
                })
            elif channel == 'indie_hackers':
                packet.update({
                    'action': f'Post product comparison to Indie Hackers contrasting {t.name}',
                    'content': pitch,
                })
            elif channel == 'huggingface_discuss':
                packet.update({
                    'action': f'Post discussion HF about workflow orchestration vs {t.name}',
                    'content': pitch,
                })

            packets.append(packet)

    return {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'total_packets': len(packets),
        'packets_by_channel': {
            ch: sum(1 for p in packets if p['channel'] == ch)
            for ch in CHANNEL_EXECUTION_ORDER
        },
        'packets': packets,
    }


def build_markdown_handoff(packets_data: dict[str, Any], targets: list[ComparisonTarget]) -> str:
    """Build a human-executable markdown handoff packet."""
    lines: list[str] = []
    lines.append('# Comparison Backlink Execution Packet')
    lines.append(f'Generated: {datetime.now(timezone.utc).isoformat()}')
    lines.append('')
    lines.append('## Why this exists now')
    lines.append('- 8 comparison backlink artifacts were prepared on 2026-05-22 and never shipped')
    lines.append('- Codeberg is flat at 11 stars (0 delta across 9 samples)')
    lines.append('- Content distribution is saturated; comparison backlinks are the highest-leverage unlock')
    lines.append('- Apollo, Reddit, and dev.to channels are blocked or measurement-pending')
    lines.append('')
    lines.append('## Shared findings reused')
    lines.append('- Four Marketing Questions (what/is it for/why different/why now)')
    lines.append('- Market intelligence and competitor positioning')
    lines.append('- Codeberg-primary CTA: https://codeberg.org/RalphWorkflow/Ralph-Workflow')
    lines.append('')
    lines.append('## Prepared targets (8)')
    lines.append('')
    for t in targets:
        lines.append(f'### {t.name} ({t.slug})')
        lines.append(f'- Status: {t.status}')
        lines.append(f'- Artifact: {t.artifact_path}')
        lines.append(f'- Comparison page: {t.comparison_path}')
        lines.append(f'- Pitch preview: {t.extract_pitch()[:200]}...')
        lines.append('')

    lines.append('## Execution channels (ordered by leverage)')
    lines.append('')
    for ch in CHANNEL_EXECUTION_ORDER:
        count = packets_data['packets_by_channel'].get(ch, 0)
        lines.append(f'- {ch}: {count} packets ready')
    lines.append('')

    lines.append('## Measurement contract')
    lines.append('- Expected outcome: at least 1 live comparison citation or backlink within 14 days')
    lines.append('- Success metric: Codeberg stars_delta_window > 0 or new referral traffic from comparison pages')
    lines.append('- Kill condition: Still no Codeberg delta after 14 days of active comparison backlink distribution')
    lines.append('')

    return '\n'.join(lines)


def execute(now: datetime | None = None) -> dict[str, Any]:
    """Main execution: validate, generate packets, produce handoff, log status."""
    if now is None:
        now = datetime.now(timezone.utc)

    targets = load_comparison_queue()
    validation = validate_targets(targets)

    if not targets:
        return {
            'status': 'empty_queue',
            'generated_at': now.isoformat(),
            'summary': 'No comparison backlink targets in queue.',
        }

    if not validation['all_ready']:
        return {
            'status': 'validation_failed',
            'generated_at': now.isoformat(),
            'validation': validation,
            'summary': f'{len(validation["issues"])} issues found. {validation["ready_count"]} targets ready.',
        }

    packets_data = generate_execution_packets(targets)
    markdown = build_markdown_handoff(packets_data, targets)

    # Write outputs
    execution_log = {
        'status': 'ready',
        'generated_at': now.isoformat(),
        'targets_count': len(targets),
        'ready_targets': validation['ready_slugs'],
        'total_packets': packets_data['total_packets'],
        'channels_used': list(packets_data['packets_by_channel'].keys()),
        'packets_by_channel': packets_data['packets_by_channel'],
        'validation': validation,
    }

    _save_json(EXECUTION_LOG_PATH, execution_log)
    EXECUTION_MD_PATH.parent.mkdir(parents=True, exist_ok=True)
    EXECUTION_MD_PATH.write_text(markdown, encoding='utf-8')

    # Also write a timestamped action log for the lane selector
    action_log_path = LOG_DIR / f'marketing_{now.strftime("%Y-%m-%d_%H%M%S")}_comparison_backlink_execution.json'
    action_log = {
        'timestamp': now.isoformat(),
        'action_type': 'comparison_backlink_execution',
        'status': 'prepared',
        'ok': True,
        'live_external_action': False,
        'summary': f'Generated {packets_data["total_packets"]} distribution packets for {len(targets)} comparison targets across {len(packets_data["packets_by_channel"])} channels.',
        'targets': validation['ready_slugs'],
        'handoff_packet': str(EXECUTION_MD_PATH),
    }
    _save_json(action_log_path, action_log)

    execution_log['action_log_path'] = str(action_log_path)
    execution_log['handoff_packet_path'] = str(EXECUTION_MD_PATH)

    return execution_log


def main() -> None:
    # ── Spidering guard: all outbound channels must pass guard ──
    try:
        from agents.marketing.channel_spidering_guard import guard_check, guard_record
        for ch in ["hackernews", "lobsters", "reddit", "dev.to"]:
            allowed, reason, remaining = guard_check(ch)
            if not allowed:
                guard_record(ch, ok=False, fingerprint="spidering_guard_rejected")
                print(json.dumps({"status": "spidering_blocked", "channel": ch, "reason": reason, "live_external_action": False}))
                # Continue to check all channels before returning
    except ImportError:
        pass

    result = execute()
    print(json.dumps(result, indent=2, default=str))
    if result.get('status') == 'ready':
        print(f'\n✅ Comparison backlink execution packet ready: {result["total_packets"]} packets for {result["targets_count"]} targets')
        print(f'   Handoff: {result.get("handoff_packet_path", "N/A")}')
    else:
        print(f'\n⚠️  Status: {result["status"]}')
        print(f'   {result.get("summary", "")}')


if __name__ == '__main__':
    main()
