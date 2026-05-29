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
    ROOT / 'Ralph-Site' / 'content' / 'blog' / 'hello-ralph-workflow.md',
    ROOT / 'Ralph-Site' / 'content' / 'blog' / 'how-to-run-claude-code-unattended.md',
    ROOT / 'Ralph-Site' / 'content' / 'blog' / 'spec-driven-ai-agents-why-workflow-is-the-unit-of-work.md',
    ROOT / 'Ralph-Site' / 'docs' / 'sphinx_overrides' / 'getting-started.md',
    ROOT / 'Ralph-Site' / 'docs' / 'sphinx_overrides' / 'what-a-good-ai-coding-finish-receipt-looks-like.md',
    ROOT / 'Ralph-Site' / 'public' / 'docs' / 'getting-started.html',
    ROOT / 'Ralph-Site' / 'public' / 'docs' / 'what-a-good-ai-coding-finish-receipt-looks-like.html',
]

HIGH_INTENT_BLOG_SURFACES = [
    ROOT / 'Ralph-Site' / 'content' / 'blog' / 'hello-ralph-workflow.md',
    ROOT / 'Ralph-Site' / 'content' / 'blog' / 'how-to-run-claude-code-unattended.md',
    ROOT / 'Ralph-Site' / 'content' / 'blog' / 'spec-driven-ai-agents-why-workflow-is-the-unit-of-work.md',
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

    def test_ralph_site_high_intent_surfaces_keep_codeberg_ahead_of_github_mirror(self):
        offenders = []
        for relative in [
            Path('Ralph-Site/docs/sphinx_overrides/getting-started.md'),
            Path('Ralph-Site/docs/sphinx_overrides/what-a-good-ai-coding-finish-receipt-looks-like.md'),
            Path('Ralph-Site/public/docs/getting-started.html'),
            Path('Ralph-Site/public/docs/what-a-good-ai-coding-finish-receipt-looks-like.html'),
            Path('Ralph-Site/content/blog/hello-ralph-workflow.md'),
            Path('Ralph-Site/content/blog/how-to-run-claude-code-unattended.md'),
            Path('Ralph-Site/content/blog/spec-driven-ai-agents-why-workflow-is-the-unit-of-work.md'),
        ]:
            text = (ROOT / relative).read_text(encoding='utf-8')
            codeberg_index = text.find('codeberg.org/RalphWorkflow/Ralph-Workflow')
            github_index = text.find('github.com/Ralph-Workflow/Ralph-Workflow')
            if github_index != -1 and (codeberg_index == -1 or codeberg_index > github_index):
                offenders.append(str(relative))
        self.assertEqual(offenders, [], f'High-intent surfaces must point to Codeberg before GitHub mirror: {offenders}')

    def test_high_intent_blog_surfaces_include_codeberg_first_cta(self):
        """High-intent blog posts must have Codeberg primary link and ordering.
        
        Checks the essential conversion requirement: Codeberg link present,
        ordered before GitHub mirror. Does not require specific CTA variants —
        those are content decisions, not enforcement targets.
        """
        offenders: list[str] = []
        for path in HIGH_INTENT_BLOG_SURFACES:
            text = path.read_text(encoding='utf-8')
            codeberg_idx = text.find('codeberg.org/RalphWorkflow/Ralph-Workflow')
            github_idx = text.find('github.com/Ralph-Workflow/Ralph-Workflow')
            if codeberg_idx == -1:
                offenders.append(f"{path.relative_to(ROOT)} :: missing Codeberg primary link")
            elif github_idx != -1 and codeberg_idx > github_idx:
                offenders.append(f"{path.relative_to(ROOT)} :: GitHub appears before Codeberg")
        self.assertEqual(offenders, [], f'High-intent blog CTA regression(s): {offenders}')


if __name__ == '__main__':
    unittest.main()
