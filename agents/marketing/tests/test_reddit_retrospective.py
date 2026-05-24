import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agents.marketing import reddit_retrospective


class RedditRetrospectiveTests(unittest.TestCase):
    def test_main_writes_latest_aliases_for_json_and_markdown(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            log_dir = root / 'agents/marketing/logs'
            log_dir.mkdir(parents=True)
            (log_dir / 'reddit_posts.jsonl').write_text(
                '\n'.join([
                    json.dumps({
                        'platform': 'reddit',
                        'account': 'Informal-Salt827',
                        'body': 'Fresh opening line\n\nBody text here',
                        'comment_url': 'https://reddit.test/comment',
                        'timestamp': '2026-05-24T23:00:00+02:00',
                        'metadata': {'community': 'r/ClaudeCode', 'title': 'Example title'},
                    }),
                    json.dumps({'type': 'structural_body_cadence', 'opening': 'ignore me'}),
                ]),
                encoding='utf-8',
            )

            with patch.object(reddit_retrospective, 'ROOT', root), \
                 patch.object(reddit_retrospective, 'LOG_JSONL', log_dir / 'reddit_posts.jsonl'), \
                 patch.object(reddit_retrospective, 'OUT_MD', log_dir / 'reddit_post_analysis.md'), \
                 patch.object(reddit_retrospective, 'OUT_JSON', log_dir / 'reddit_post_analysis.json'), \
                 patch.object(reddit_retrospective, 'OUT_MD_LATEST', log_dir / 'reddit_post_analysis_latest.md'), \
                 patch.object(reddit_retrospective, 'OUT_JSON_LATEST', log_dir / 'reddit_post_analysis_latest.json'):
                rc = reddit_retrospective.main()

            self.assertEqual(rc, 0)
            canonical_json = json.loads((log_dir / 'reddit_post_analysis.json').read_text(encoding='utf-8'))
            latest_json = json.loads((log_dir / 'reddit_post_analysis_latest.json').read_text(encoding='utf-8'))
            self.assertEqual(canonical_json, latest_json)
            canonical_md = (log_dir / 'reddit_post_analysis.md').read_text(encoding='utf-8')
            latest_md = (log_dir / 'reddit_post_analysis_latest.md').read_text(encoding='utf-8')
            self.assertEqual(canonical_md, latest_md)
            self.assertIn('Filtered 1 cadence/structural records', canonical_md)


if __name__ == '__main__':
    unittest.main()
