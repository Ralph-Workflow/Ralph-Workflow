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

    def test_extract_channels_drops_social_share_junk_and_normalizes_backslashes(self):
        html = ' '.join([
            '<a href="https://x.com/TIMEWELL_PR\\\\\\">X</a>',
            '<a href="https://x.com/intent/tweet?url=&text=hello">Share</a>',
            '<a href="https://www.linkedin.com/company/timewell-corp/\\\\\\">LinkedIn</a>',
            '<a href="https://www.linkedin.com/sharing/share-offsite/?url=">LinkedIn share</a>',
            '<a href="https://www.linkedin.com/sharing/share-offsite/?url=https%3A%2F%2Fexample.com">LinkedIn share with url</a>',
        ])

        channels = discovery.extract_channels('https://timewell.jp/en/columns/example', html)
        values = {(row['type'], row['value']) for row in channels}

        self.assertIn(('x', 'https://x.com/TIMEWELL_PR'), values)
        self.assertIn(('linkedin', 'https://www.linkedin.com/company/timewell-corp'), values)
        self.assertNotIn(('x', 'https://x.com/TIMEWELL_PR\\\\\\'), values)
        self.assertNotIn(('x', 'https://x.com/intent/tweet?url=&text=hello'), values)
        self.assertNotIn(('linkedin', 'https://www.linkedin.com/company/timewell-corp/\\\\\\'), values)
        self.assertNotIn(('linkedin', 'https://www.linkedin.com/sharing/share-offsite/?url='), values)
        self.assertNotIn(('linkedin', 'https://www.linkedin.com/sharing/share-offsite/?url=https%3A%2F%2Fexample.com'), values)

    def test_extract_channels_promotes_github_repo_link_to_issue_path(self):
        html = ' '.join([
            '<a href="https://github.com/shenli/tldl">GitHub</a>',
            '<a href="https://github.com/topics/agents">Topics</a>',
        ])

        channels = discovery.extract_channels('https://www.tldl.io/resources/tldl-faq', html)
        values = {(row['type'], row['value']) for row in channels}

        self.assertIn(('github_issue', 'https://github.com/shenli/tldl/issues/new'), values)
        self.assertNotIn(('github_issue', 'https://github.com/topics/agents/issues/new'), values)

    def test_extract_channels_does_not_misclassify_same_site_urls_as_x(self):
        html = ' '.join([
            '<a href="https://codivox.com/contact">Contact</a>',
            '<a href="https://codivox.com/hire">Hire</a>',
            '<a href="https://x.com/codivoxai">X</a>',
        ])

        channels = discovery.extract_channels('https://codivox.com/comparisons/ai-coding-tools', html)
        values = {(row['type'], row['value']) for row in channels}

        self.assertIn(('website', 'https://codivox.com/contact'), values)
        self.assertIn(('website', 'https://codivox.com/hire'), values)
        self.assertIn(('x', 'https://x.com/codivoxai'), values)
        self.assertNotIn(('x', 'https://codivox.com/contact'), values)
        self.assertNotIn(('x', 'https://codivox.com/hire'), values)

    def test_extract_channels_detects_embedded_feedback_form_as_manual_path(self):
        html = ' '.join([
            '<button title="Submit Feedback">💬</button>',
            '<form id="fb-form" onsubmit="submitFeedback(event)">',
            '<textarea placeholder="Tell us what you think..."></textarea>',
            '<input type="email" name="contact" placeholder="Email (optional)">',
            '</form>',
            "<script>fetch('/api/feedback', { method: 'POST' })</script>",
        ])

        channels = discovery.extract_channels('https://aisaying.net/', html)

        self.assertIn(
            {'type': 'website', 'value': 'https://aisaying.net', 'label': 'feedback form'},
            channels,
        )

    def test_enrich_target_labels_feedback_form_route_explicitly(self):
        target = discovery.Target(
            name='AI Saying',
            article_url='https://aisaying.net/knowledge/article/ai-coding-tools-comparison-matrix',
            root_url='https://aisaying.net/',
            hook='Hook',
            reason='Fit',
            outreach_subject='Subject',
        )

        feedback_html = ' '.join([
            '<button>Submit Feedback</button>',
            '<form id="fb-form" onsubmit="submitFeedback(event)">',
            '<textarea placeholder="Tell us what you think..."></textarea>',
            '<input type="email" name="contact" placeholder="Email (optional)">',
            '</form>',
            "<script>fetch('/api/feedback', { method: 'POST' })</script>",
        ])

        def fake_get(url: str, timeout: int = 20) -> str:
            normalized = url.rstrip('/')
            if normalized == 'https://aisaying.net/knowledge/article/ai-coding-tools-comparison-matrix':
                return feedback_html
            if normalized == 'https://aisaying.net':
                return feedback_html
            return ''

        original = discovery.http_get
        discovery.http_get = fake_get
        try:
            enriched = discovery.enrich_target(target)
        finally:
            discovery.http_get = original

        self.assertEqual(enriched['recommended_next_step'], 'public feedback-form contact path is now identified')
        self.assertIn(
            {'type': 'website', 'value': 'https://aisaying.net', 'label': 'feedback form'},
            enriched['channels'],
        )

    def test_placeholder_email_detection_filters_known_junk_samples(self):
        self.assertTrue(discovery._looks_placeholder_email('you@example.com'))
        self.assertTrue(discovery._looks_placeholder_email('jane@acme.com'))
        self.assertTrue(discovery._looks_placeholder_email('you@work.com'))
        self.assertTrue(discovery._looks_placeholder_email('you@company.com'))
        self.assertFalse(discovery._looks_placeholder_email('info@digitalapplied.com'))

    def test_extract_follow_on_contact_urls_finds_same_site_faq_like_pages(self):
        html = ' '.join([
            '<a href="/resources/tldl-faq">FAQ</a>',
            '<a href="/docs/troubleshooting">Troubleshooting</a>',
            '<a href="https://other.example/help">Offsite help</a>',
            '<a href="/blog/product-update">Blog</a>',
        ])

        urls = discovery.extract_follow_on_contact_urls('https://www.tldl.io/resources/ai-coding-tools-2026', html)

        self.assertEqual(
            urls,
            [
                'https://www.tldl.io/docs/troubleshooting',
                'https://www.tldl.io/resources/tldl-faq',
            ],
        )

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

    def test_enrich_target_prefers_contact_page_over_weak_role_emails(self):
        target = discovery.Target(
            name='NxCode',
            article_url='https://www.nxcode.io/resources/news/codex-vs-cursor-vs-claude-code-2026',
            root_url='https://www.nxcode.io/',
            hook='Hook',
            reason='Fit',
            outreach_subject='Subject',
            contact_urls=(
                'https://www.nxcode.io/ar/contact',
                'https://www.nxcode.io/docs/troubleshooting',
            ),
        )

        def fake_get(url: str, timeout: int = 20) -> str:
            normalized = url.rstrip('/')
            if normalized == 'https://www.nxcode.io/resources/news/codex-vs-cursor-vs-claude-code-2026':
                return '<a href="/ar/contact">Contact</a>'
            if normalized == 'https://www.nxcode.io':
                return '<a href="mailto:legal@nxcode.io">Legal</a>'
            if normalized == 'https://www.nxcode.io/ar/contact':
                return '<form></form><a href="/company/about">About</a>'
            if normalized == 'https://www.nxcode.io/docs/troubleshooting':
                return '<p>Urgent issue? <a href="mailto:support@nxcode.io">support@nxcode.io</a></p>'
            if normalized == 'https://www.nxcode.io/company/about':
                return '<p>About page</p>'
            return ''

        original = discovery.http_get
        discovery.http_get = fake_get
        try:
            enriched = discovery.enrich_target(target)
        finally:
            discovery.http_get = original

        self.assertEqual(enriched['recommended_next_step'], 'email/contact send path is now identified')
        self.assertEqual(enriched['channels'][0], {'type': 'website', 'value': 'https://www.nxcode.io/ar/contact', 'label': 'contact page'})
        self.assertIn({'type': 'email', 'value': 'legal@nxcode.io', 'label': 'email'}, enriched['channels'])
        self.assertIn({'type': 'email', 'value': 'support@nxcode.io', 'label': 'email'}, enriched['channels'])

    def test_enrich_target_preserves_explicit_real_email(self):
        target = discovery.Target(
            name='TIMEWELL',
            article_url='https://timewell.jp/en/columns/ai-coding-tools-complete-benchmark-2026',
            root_url='https://timewell.jp/en/',
            hook='Hook',
            reason='Fit',
            outreach_subject='Subject',
            explicit_emails=('timewell@timewell.jp',),
        )

        def fake_get(url: str, timeout: int = 20) -> str:
            normalized = url.rstrip('/')
            if normalized == 'https://timewell.jp/en/columns/ai-coding-tools-complete-benchmark-2026':
                return '<a href="/en/contact">Contact</a>'
            if normalized == 'https://timewell.jp/en':
                return '<a href="/en/company">Company</a>'
            if normalized == 'https://timewell.jp/en/contact':
                return '<form></form>'
            return ''

        original = discovery.http_get
        discovery.http_get = fake_get
        try:
            enriched = discovery.enrich_target(target)
        finally:
            discovery.http_get = original

        self.assertEqual(enriched['recommended_next_step'], 'email/contact send path is now identified')
        self.assertEqual(enriched['channels'][0], {'type': 'email', 'value': 'timewell@timewell.jp', 'label': 'email'})

    def test_enrich_target_finds_editorial_email_on_toolradar_policy_page(self):
        target = discovery.Target(
            name='Toolradar',
            article_url='https://toolradar.com/guides/best-ai-coding-tools',
            root_url='https://toolradar.com/',
            hook='Hook',
            reason='Fit',
            outreach_subject='Subject',
            contact_urls=(
                'https://toolradar.com/contact',
                'https://toolradar.com/editorial-policy',
            ),
        )

        def fake_get(url: str, timeout: int = 20) -> str:
            normalized = url.rstrip('/')
            if normalized == 'https://toolradar.com/guides/best-ai-coding-tools':
                return '<a href="/contact">Contact</a>'
            if normalized == 'https://toolradar.com':
                return '<a href="/about">About</a>'
            if normalized == 'https://toolradar.com/contact':
                return '<p>Tell us what you\'re building.</p>'
            if normalized == 'https://toolradar.com/editorial-policy':
                return '<p>Email editorial@toolradar.com with corrections.</p>'
            return ''

        original = discovery.http_get
        discovery.http_get = fake_get
        try:
            enriched = discovery.enrich_target(target)
        finally:
            discovery.http_get = original

        self.assertEqual(enriched['recommended_next_step'], 'email/contact send path is now identified')
        self.assertIn(
            {'type': 'email', 'value': 'editorial@toolradar.com', 'label': 'email'},
            enriched['channels'],
        )

    def test_enrich_target_uses_github_issue_path_when_hub_page_reveals_faq(self):
        target = discovery.Target(
            name='TLDL',
            article_url='https://www.tldl.io/resources/ai-coding-tools-2026',
            root_url='https://www.tldl.io/',
            hook='Hook',
            reason='Fit',
            outreach_subject='Subject',
        )

        def fake_get(url: str, timeout: int = 20) -> str:
            normalized = url.rstrip('/')
            if normalized == 'https://www.tldl.io/resources/ai-coding-tools-2026':
                return '<a href="/about">About</a>'
            if normalized == 'https://www.tldl.io':
                return '<a href="https://x.com/shenli3514">X</a><a href="mailto:you@company.com">Placeholder</a>'
            if normalized == 'https://www.tldl.io/resources':
                return '<a href="/resources/tldl-faq">FAQ</a>'
            if normalized == 'https://www.tldl.io/resources/tldl-faq':
                return '<p>For questions or feedback, reach out through our GitHub.</p><a href="https://github.com/shenli/tldl">GitHub</a>'
            if normalized == 'https://www.tldl.io/about':
                return '<p>About</p>'
            return ''

        original = discovery.http_get
        discovery.http_get = fake_get
        try:
            enriched = discovery.enrich_target(target)
        finally:
            discovery.http_get = original

        self.assertEqual(enriched['recommended_next_step'], 'GitHub issue/PR path is now identified')
        self.assertIn(
            {'type': 'github_issue', 'value': 'https://github.com/shenli/tldl/issues/new', 'label': 'GitHub issue'},
            enriched['channels'],
        )
        self.assertIn(
            {'type': 'website', 'value': 'https://www.tldl.io/resources/tldl-faq', 'label': 'faq page'},
            enriched['channels'],
        )
        self.assertNotIn(
            {'type': 'email', 'value': 'you@company.com', 'label': 'email'},
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
