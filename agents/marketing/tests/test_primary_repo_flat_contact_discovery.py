import unittest

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


if __name__ == '__main__':
    unittest.main()
