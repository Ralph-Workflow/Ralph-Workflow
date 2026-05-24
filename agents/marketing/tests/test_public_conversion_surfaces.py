from pathlib import Path
import unittest

ROOT = Path('/home/mistlight/.openclaw/workspace')
PUBLIC_SURFACES = [
    ROOT / 'README.md',
    ROOT / 'START_HERE.md',
    ROOT / 'docs' / 'README.md',
    ROOT / 'docs' / 'first-task-guide.md',
    ROOT / 'content' / 'posts' / '2026-05-10_devto-ai-cli-orchestration-problem.md',
    ROOT / 'content' / 'posts' / '2026-05-11_devto-spec-driven-development-unattended-claude.md',
    ROOT / 'drafts' / '2026-05-20_ai-engineering-pipeline_telegraph.md',
]
DEPRECATED_SNIPPETS = [
    'npm install -g ralphworkflow',
    'brew install ralphworkflow',
    'ralph run --spec',
]


class PublicConversionSurfaceTests(unittest.TestCase):
    def test_public_surfaces_do_not_contain_deprecated_install_or_run_snippets(self):
        offenders = []
        for path in PUBLIC_SURFACES:
            text = path.read_text(encoding='utf-8')
            for snippet in DEPRECATED_SNIPPETS:
                if snippet in text:
                    offenders.append(f'{path.relative_to(ROOT)} :: {snippet}')
        self.assertEqual(offenders, [], f'Deprecated public conversion snippets found: {offenders}')

    def test_any_surface_with_github_mirror_also_mentions_codeberg_primary(self):
        offenders = []
        for path in PUBLIC_SURFACES:
            text = path.read_text(encoding='utf-8')
            if 'github.com/Ralph-Workflow/Ralph-Workflow' in text and 'codeberg.org/RalphWorkflow/Ralph-Workflow' not in text:
                offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [], f'GitHub mirror mentioned without Codeberg primary: {offenders}')


if __name__ == '__main__':
    unittest.main()
