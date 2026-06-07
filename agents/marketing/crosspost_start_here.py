#!/usr/bin/env python3
"""Cross-post start-here guide to Telegraph. Handles backtick-stripping in pre blocks."""
import sys, json, time, re, subprocess, urllib.parse
from pathlib import Path

ROOT = Path("/home/mistlight/.openclaw/workspace")
AGENTS = ROOT / "agents" / "marketing"
sys.path.insert(0, str(AGENTS))

from run_posting import build_telegraph_nodes, CTA_FOOTER

BLOG_PATH = ROOT / "Ralph-Site/content/blog/your-first-overnight-task-start-here-guide.md"
TELEGRAPH_POSTED = AGENTS / "logs" / "telegraph_posted.json"

def main():
    raw = BLOG_PATH.read_text(encoding='utf-8')
    
    m = re.search(r'^title:\s*"([^"]+)"', raw, re.MULTILINE)
    title = m.group(1).strip() if m else "Your First Overnight Task with Ralph Workflow"
    
    fm_end = raw.find("\n------\n")
    body = raw[fm_end + len("\n------\n"):].strip() if fm_end > 0 else raw
    
    for marker in ["## Related Posts", "## Start here, now"]:
        idx = body.rfind(marker)
        if idx > 0:
            body = body[:idx].strip()
    
    # Remove markdown tables
    lines = body.split('\n')
    out = []
    in_table = False
    for line in lines:
        is_sep_row = bool(re.match(r'^\|[\s\-:|]+\|$', line.strip()))
        if is_sep_row:
            in_table = True
            out.append('')
            continue
        if in_table and line.strip().startswith('|'):
            cells = [c.strip() for c in line.strip().split('|') if c.strip()]
            if cells:
                out.append('- ' + ' — '.join(cells))
            continue
        in_table = False
        out.append(line)
    
    body = '\n'.join(out) + CTA_FOOTER
    nodes = build_telegraph_nodes(body)
    
    # Strip backticks from pre blocks (Telegraph rejects them inside <pre>)
    for node in nodes:
        if node.get('tag') == 'pre':
            for j, child in enumerate(node.get('children', [])):
                if isinstance(child, str):
                    node['children'][j] = child.replace('\u0060', '')  # backtick
    
    print(f"Posting: {title}")
    print(f"Nodes: {len(nodes)}, JSON: {len(json.dumps(nodes))} bytes")
    
    r1 = subprocess.run(
        ['curl', '-s', 'https://api.telegra.ph/createAccount?short_name=rwbot6&author_name=RW+Marketing'],
        capture_output=True, text=True, timeout=10
    )
    token = json.loads(r1.stdout)['result']['access_token']
    
    params = urllib.parse.urlencode({
        'access_token': token,
        'title': title,
        'author_name': 'RW Marketing',
        'content': json.dumps(nodes),
        'return_content': 'false',
    })
    
    r2 = subprocess.run(
        ['curl', '-s', '-X', 'POST', 'https://api.telegra.ph/createPage',
         '-H', 'Content-Type: application/x-www-form-urlencoded',
         '-d', params],
        capture_output=True, text=True, timeout=20
    )
    result = json.loads(r2.stdout)
    
    if result.get('ok'):
        url = result['result']['url']
        print(f"SUCCESS: {url}")
        
        posted = {}
        if TELEGRAPH_POSTED.exists():
            posted = json.loads(TELEGRAPH_POSTED.read_text(encoding='utf-8'))
        
        today = "2026-06-05"
        entry = {
            "url": url,
            "title": title,
            "source": str(BLOG_PATH),
            "posted_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
            "type": "blog_crosspost",
            "cta": "Codeberg primary + GitHub mirror + pipx install",
            "seo_clusters": [
                "first overnight task AI coding",
                "start here guide autonomous coding",
                "how to start with AI coding agent",
            ]
        }
        posted.setdefault(today, []).append(entry)
        TELEGRAPH_POSTED.write_text(json.dumps(posted, indent=2), encoding='utf-8')
        print("Recorded in telegraph_posted.json")
        return 0
    else:
        print(f"FAILED: {result.get('error', r2.stdout[:200])}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
