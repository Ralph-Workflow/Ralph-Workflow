import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from agents.marketing import primary_repo_flat_contact_discovery as discovery


class PrimaryRepoFlatContactDiscoveryTests(unittest.TestCase):
    def test_extract_channels_keeps_same_site_and_telegram_but_drops_noise(self):
        html = ' '.join([
            '<a href="/contact#main-content">Contact</a>',
            '<a href="/advertise">Advertise</a>',
            '<a href="https://schema.org">Schema</a>',
            '<a href="https://t.me/example">Telegram</a>',
            '<a href="mailto:test@ctxt.dev">Email</a>',
            '<a href="mailto:you@example.com">Placeholder</a>',
            '<a href="https://other.example/contact">Offsite</a>',
        ])

        channels = discovery.extract_channels('https://ctxt.dev/about', html)
        values = {(row['type'], row['value']) for row in channels}

        self.assertIn(('email', 'test@ctxt.dev'), values)
        self.assertNotIn(('email', 'you@example.com'), values)
        self.assertIn(('telegram', 'https://t.me/example'), values)
        self.assertIn(('website', 'https://ctxt.dev/contact'), values)
        self.assertIn(('website', 'https://ctxt.dev/advertise'), values)
        self.assertNotIn(('website', 'https://schema.org'), values)
        self.assertNotIn(('website', 'https://other.example/contact'), values)

    def test_enrich_target_prefers_explicit_work_with_me_telegram_path(self):
        target = discovery.Target(
            name='ctxt.dev / Signum',
            article_url='https://ctxt.dev/posts/en/tasks-are-not-goals',
            root_url='https://ctxt.dev/',
            hook='Tasks Are Not Goals (2026-05-22)',
            reason='Fit',
            outreach_subject='Subject',
        )

        def fake_get(url: str, timeout: int = 20) -> str:
            if url == target.article_url:
                return '<a href="/work-with-me">Work with me</a>'
            if url.rstrip('/') == 'https://ctxt.dev':
                return '<a href="https://t.me/ctxtdev">Telegram</a>'
            if 'work-with-me' in url:
                return '<p>' + ('Helpful context. ' * 20) + 'Send a short message in Telegram with your use case.</p><a href="https://t.me/ctxtdev">Message on Telegram</a>'
            return ''

        original = discovery.http_get
        discovery.http_get = fake_get
        try:
            enriched = discovery.enrich_target(target)
        finally:
            discovery.http_get = original

        self.assertEqual(enriched['recommended_next_step'], 'Telegram consulting contact path is explicitly confirmed')
        self.assertEqual(enriched['channels'][0]['label'], 'work with me page')
        self.assertIn(
            {'type': 'telegram', 'value': 'https://t.me/ctxtdev', 'label': 'Telegram'},
            enriched['channels'],
        )

    def test_enrich_target_finds_real_email_and_drops_placeholder_on_publisher_site(self):
        target = discovery.Target(
            name='ToolChase',
            article_url='https://toolchase.com/blog/best-ai-coding-tools-2026/',
            root_url='https://toolchase.com/',
            hook='Hook',
            reason='Fit',
            outreach_subject='Subject',
        )

        def fake_get(url: str, timeout: int = 20) -> str:
            normalized = url.rstrip('/')
            if normalized == 'https://toolchase.com/blog/best-ai-coding-tools-2026':
                return '<a href="/contact/">Contact</a><a href="mailto:hello@toolchase.com">Email</a>'
            if normalized == 'https://toolchase.com':
                return '<a href="/advertise/">Advertise</a><a href="mailto:you@example.com">Placeholder</a>'
            if normalized == 'https://toolchase.com/contact':
                return '<a href="mailto:hello@toolchase.com">Email</a>'
            if normalized == 'https://toolchase.com/advertise':
                return '<p>Partnerships</p><a href="mailto:hello@toolchase.com">hello@toolchase.com</a>'
            return ''

        original = discovery.http_get
        discovery.http_get = fake_get
        try:
            enriched = discovery.enrich_target(target)
        finally:
            discovery.http_get = original

        self.assertEqual(enriched['recommended_next_step'], 'email/contact send path is now identified')
        self.assertIn(
            {'type': 'email', 'value': 'hello@toolchase.com', 'label': 'email'},
            enriched['channels'],
        )
        self.assertNotIn(
            {'type': 'email', 'value': 'you@example.com', 'label': 'email'},
            enriched['channels'],
        )

    def test_recent_contact_targets_omits_recent_live_publisher_outreach(self):
        with TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            (log_dir / 'marketing_recent.json').write_text(
                '{\n'
                '  "timestamp": "2026-05-24T08:09:00+02:00",\n'
                '  "target": "Bollwerk / Werkstatt",\n'
                '  "type": "publisher_email_outreach",\n'
                '  "status": "sent",\n'
                '  "ok": true\n'
                '}\n',
                encoding='utf-8',
            )
            (log_dir / 'marketing_old.json').write_text(
                '{\n'
                '  "timestamp": "2026-05-10T08:09:00+02:00",\n'
                '  "target": "AXME Code",\n'
                '  "type": "publisher_email_outreach",\n'
                '  "status": "sent",\n'
                '  "ok": true\n'
                '}\n',
                encoding='utf-8',
            )

            recent = discovery._recent_contact_targets(
                datetime(2026, 5, 24, 22, 0, 0),
                log_dir=log_dir,
            )

        self.assertIn('Bollwerk / Werkstatt', recent)
        self.assertNotIn('AXME Code', recent)


if __name__ == '__main__':
    unittest.main()
