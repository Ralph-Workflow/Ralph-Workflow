import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from agents.marketing import primary_repo_flat_contact_discovery as discovery


class PrimaryRepoFlatContactDiscoveryTests(unittest.TestCase):
    def test_extract_channels_keeps_same_site_and_telegram_but_drops_noise(self):
        html = ' '.join([
            '<a href="/contact#main-content">Contact</a>',
            '<a href="https://schema.org">Schema</a>',
            '<a href="https://t.me/example">Telegram</a>',
            '<a href="mailto:test@example.com">Email</a>',
            '<a href="https://other.example/contact">Offsite</a>',
        ])

        channels = discovery.extract_channels('https://ctxt.dev/about', html)
        values = {(row['type'], row['value']) for row in channels}

        self.assertIn(('email', 'test@example.com'), values)
        self.assertIn(('telegram', 'https://t.me/example'), values)
        self.assertIn(('website', 'https://ctxt.dev/contact'), values)
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
