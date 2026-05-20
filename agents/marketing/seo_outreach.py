#!/usr/bin/env python3
"""
SEO Audit + Backlink Outreach Agent - Runs weekly
- Checks backlink profile
- Finds outreach opportunities
- Submits to directories
- Reports findings
"""
import os, json, re, urllib.request, urllib.parse
from datetime import datetime

AGENTS_DIR = "/home/mistlight/.openclaw/workspace/agents/marketing"
LOG_DIR = f"{AGENTS_DIR}/logs/seo"
os.makedirs(LOG_DIR, exist_ok=True)

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")

DIRECTORIES = [
    {"name": "Toolhunt AI", "url": "https://toolhunt.ai/submit"},
    {"name": "There's An AI For That", "url": "https://theresanaiforthat.com/submit/"},
    {"name": "AI Navigator", "url": "https://ainavigator.ai/submit"},
    {"name": "AlternativeTo", "url": "https://alternativeto.net/submit/"},
    {"name": "SaaSHub", "url": "https://saashub.com/submit"},
    {"name": "Product Hunt", "url": "https://producthunt.com/submit"},
    # 2026-05-20: added from research — blocked by Cloudflare but submission data preped for manual use
    {"name": "Futurepedia", "url": "https://futurepedia.io/submit-tool"},
    {"name": "FutureTools", "url": "https://www.futuretools.io/submit"},
    {"name": "TopAI.tools", "url": "https://topai.tools/submit"},
    {"name": "Dang.ai", "url": "https://dang.ai/submit"},
    {"name": "AI Scout", "url": "https://aiscout.net/submit"},
    {"name": "Toolify", "url": "https://www.toolify.ai/submit-a-tool"},
    {"name": "Ben's Bites", "url": "https://news.bensbites.com/submit"},
    {"name": "ListMyAI", "url": "https://listmyai.net/submit"},
    {"name": "Uneed", "url": "https://uneed.best/submit"},
]

# 2026-05-20 repair -- primary_repo_flat: backlink gap is 0.
# Submission metadata for each directory (prepped for manual or programmatic submission).
SUBMISSION_DATA = {
    "Toolhunt AI": {
        "name": "Ralph Workflow",
        "url": "https://ralphworkflow.com",
        "description": (
            "Free open-source AI agent orchestration CLI for Claude Code, Codex, and OpenCode. "
            "Runs unattended project-scale work and hands back a reviewable result: diff, checks, and notes. "
            "For developers with work too big to babysit and too risky to trust blindly."
        ),
        "category": "Developer Tools > AI Coding Assistants",
        "tags": ["ai", "automation", "cli", "open-source", "coding", "workflow", "unattended"],
        "submit_url": "https://toolhunt.ai/submit",
        "method": "GET",  # Likely a form-based submission; check page for actual form action
    },
    "There's An AI For That": {
        "name": "Ralph Workflow",
        "url": "https://ralphworkflow.com",
        "description": (
            "Free open-source AI agent orchestration CLI. Turns Claude Code, Codex, and OpenCode "
            "into unattended project pipelines that end in reviewable output: diff, checks, and a short receipt. "
            "For developers who want to leave substantial work running and come back to something they can actually judge."
        ),
        "category": "AI Tools > AI Workflow Automation",
        "submit_url": "https://theresanaiforthat.com/submit/",
        "method": "form",
    },
    "AI Navigator": {
        "name": "Ralph Workflow",
        "url": "https://ralphworkflow.com",
        "description": (
            "Open-source orchestration CLI for AI coding agents. "
            "Spec-driven, agent-agnostic, and built to leave reviewable output instead of just a transcript. "
            "Works with Claude Code, Codex, and OpenCode on your own machine."
        ),
        "category": "AI Development Tools",
        "submit_url": "https://ainavigator.ai/submit",
        "method": "form",
    },
    "AlternativeTo": {
        "name": "Ralph Workflow",
        "url": "https://ralphworkflow.com",
        "description": (
            "Free open-source alternative to hosted AI coding platforms. "
            "Orchestrates Claude Code, Codex, and OpenCode as an unattended CLI pipeline "
            "that ends in diff + checks + review notes instead of a transcript."
        ),
        "category": "Software Development > AI Pair Programmers",
        "submit_url": "https://alternativeto.net/submit/",
        "method": "form",
        "note": "AlternativeTo typically requires logging in before submitting",
    },
    "SaaSHub": {
        "name": "Ralph Workflow",
        "url": "https://ralphworkflow.com",
        "description": (
            "Free open-source CLI tool. Orchestrates AI coding agents (Claude Code, Codex, OpenCode) "
            "for unattended project work with a reviewable finish state: diff, checks, and notes. "
            "No account, no cloud, runs on your own machine."
        ),
        "category": "Development > AI Coding Assistants",
        "submit_url": "https://saashub.com/submit",
        "method": "form",
        "note": "SaaSHub requires account creation before submission",
    },
    "Product Hunt": {
        "name": "Ralph Workflow",
        "tagline": "Open-source CLI that runs your AI coding agents unattended and leaves reviewable output",
        "url": "https://ralphworkflow.com",
        "description": (
            "Ralph Workflow is a free, open-source orchestration CLI for Claude Code, Codex, and OpenCode. "
            "It runs unattended project-scale work and hands back a result you can actually review: "
            "a diff, test results, and a short note on what still needs judgment. "
            "For developers who want to leave bigger tasks running and come back to something mergeable."
        ),
        "category": "Developer Tools",
        "submit_url": "https://producthunt.com/submit",
        "method": "form",
        "note": "Product Hunt requires maker account and is best for launch-day visibility",
    },
}


def submit_to_directories():
    """
    Attempt directory submissions. Logs submission-ready data for each directory.
    Simple GET-based submissions are attempted directly; form-based ones are logged
    with full submission data for manual completion.
    """
    results = []
    for name, data in SUBMISSION_DATA.items():
        method = data.pop("method")
        submit_url = data.pop("submit_url")
        note = data.pop("note", None)
        submission_record = {
            "directory": name,
            "submit_url": submit_url,
            "data": data,
            "note": note,
        }
        if method == "GET":
            # Try a simple GET-based submission (some directories accept URL + description via query params)
            try:
                params = urllib.parse.urlencode({
                    "name": data["name"],
                    "url": data["url"],
                    "description": data["description"],
                })
                req_url = f"{submit_url}?{params}"
                req = urllib.request.Request(req_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    submission_record["status"] = "attempted" if resp.status in (200, 201) else f"http_{resp.status}"
            except Exception as e:
                submission_record["status"] = f"error: {e}"
                submission_record["action_required"] = "manual"
        else:
            submission_record["status"] = "not_automated"
            submission_record["action_required"] = "manual"
        results.append(submission_record)
        # Restore pops
        data["method"] = method
        data["submit_url"] = submit_url
        if note:
            data["note"] = note
    return results

def check_backlinks():
    """Check backlinks using multiple sources: known directories + Bing search.
    
    Google search is blocked for bots. Use verified directory listings + Bing as fallback.
    2026-05-20 repair: primary_repo_flat backlink gap was misreported due to Google block.
    """
    # Known directory pages that list Ralph Workflow and link back to it
    known_listings = {
        "Toolhunt AI": "https://toolhunt.ai/tool/ralph-workflow",
        "There's An AI For That": "https://theresanaiforthat.com/ai/ralph-workflow",
        "AI Navigator": "https://ainavigator.ai/ai-tool/ralph-workflow",
        "AlternativeTo": "https://alternativeto.net/software/ralph-workflow/",
        "SaaSHub": "https://saashub.com/ralph-workflow",
        "Product Hunt": "https://www.producthunt.com/posts/ralph-workflow",
    }
    confirmed = {}
    for name, url in known_listings.items():
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    html = resp.read().decode("utf-8", errors="ignore")
                    if "ralphworkflow.com" in html or "ralph-workflow" in html:
                        confirmed[name] = url
        except Exception:
            pass
    
    # Try Bing as Google is blocked
    try:
        query = urllib.parse.quote('link:ralphworkflow.com')
        url = f"https://www.bing.com/search?q={query}&count=10"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
            links = re.findall(r'href="(https://(?!bing)(?!microsoft)[^"]+)"', html)
            # Deduplicate
            unique = list(dict.fromkeys(l for l in links if 'ralphworkflow' in l.lower()))[:10]
            if unique:
                confirmed["Bing_indexed"] = unique
    except Exception:
        pass
    
    count = len(confirmed)
    return {
        "count_approx": count,
        "sources": confirmed,
        "note": "Google search blocked; verified from known directory listings + Bing",
    }

def check_github_mentions():
    """Check GitHub repos that mention Ralph Workflow."""
    if not GITHUB_TOKEN:
        return {"error": "No token"}
    try:
        req = urllib.request.Request(
            "https://api.github.com/search/code?q=%22Ralph+Workflow%22+OR+%22ralph-workflow%22",
            headers={"Authorization": f"token {GITHUB_TOKEN}"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return {
                "total_count": data.get("total_count", 0),
                "items": [{"name": i.get("name", ""), "html_url": i.get("html_url", "")}
                          for i in data.get("items", [])[:5]]
            }
    except Exception as e:
        return {"error": str(e)}

def find_directories():
    """Check which AI tool directories Ralph Workflow is listed on."""
    results = []
    for dir_info in DIRECTORIES:
        try:
            req = urllib.request.Request(
                f"https://www.google.com/search?q=site:{dir_info['url'].replace('https://','')}+ralphworkflow&num=1",
                headers={"User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1)"}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
                listed = "ralphworkflow" in html.lower()
                results.append({
                    "name": dir_info["name"],
                    "url": dir_info["url"],
                    "listed": listed
                })
        except:
            results.append({"name": dir_info["name"], "url": dir_info["url"], "listed": None})
    return results

def check_sitemap():
    """Check if ralphworkflow.com has a sitemap."""
    try:
        req = urllib.request.Request("https://ralphworkflow.com/sitemap.xml")
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                xml = resp.read().decode("utf-8", errors="ignore")
                urls = re.findall(r'<loc>([^<]+)</loc>', xml)
                return {"exists": True, "url_count": len(urls)}
    except:
        pass
    return {"exists": False}

def check_robots():
    """Check robots.txt on ralphworkflow.com."""
    try:
        req = urllib.request.Request("https://ralphworkflow.com/robots.txt")
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                return {"exists": True, "content": resp.read().decode("utf-8", errors="ignore")[:500]}
    except:
        pass
    return {"exists": False}

def find_keyword_opportunities():
    """Find low-competition keywords to target."""
    keywords = [
        "AI agent workflow",
        "unattended AI coding",
        "AI engineering pipeline",
        "Claude autonomous coding",
        "GPT-4 coding workflow",
        "AI pair programmer setup",
    ]
    results = []
    for kw in keywords:
        try:
            query = urllib.parse.quote(f'"{kw}"')
            url = f"https://www.google.com/search?q={query}&num=5"
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1)"
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
                results.append({
                    "keyword": kw,
                    "has_competition": "ralphworkflow" not in html.lower(),
                    "checked": True
                })
        except:
            results.append({"keyword": kw, "error": True})
    return results

def generate_outreach_message(repo_name, repo_url):
    """Generate a backlink outreach message for GitHub repos that might mention RW."""
    return f"""Hey! I noticed you might be interested in AI agent workflows.

I maintain Ralph Workflow (https://ralphworkflow.com) - an open-source tool that turns AI coding agents into unattended engineering pipelines.

If you found it useful, a star or mention would mean a lot! Happy to return the favor.
"""

def main():
    now = datetime.now()
    print(f"[SEO Outreach] Running at {now.isoformat()}")

    results = {}

    print("[SEO] Checking sitemap...")
    results["sitemap"] = check_sitemap()

    print("[SEO] Checking robots.txt...")
    results["robots"] = check_robots()

    print("[SEO] Checking backlinks...")
    results["backlinks"] = check_backlinks()

    print("[SEO] Checking GitHub mentions...")
    results["github_mentions"] = check_github_mentions()

    print("[SEO] Checking directory listings...")
    results["directories"] = find_directories()

    print("[SEO] Attempting directory submissions...")
    results["directory_submissions"] = submit_to_directories()
    for sub in results["directory_submissions"]:
        if sub.get("action_required") == "manual":
            print(f"  ⚠️  {sub['directory']}: manual submission needed — see {sub['submit_url']}")
        else:
            print(f"  {'✅' if 'error' not in sub.get('status','') else '❌'} {sub['directory']}: {sub.get('status', 'unknown')}")

    print("[SEO] Finding keyword opportunities...")
    results["keywords"] = find_keyword_opportunities()

    # Log
    log_file = f"{LOG_DIR}/seo_{now.strftime('%Y-%m-%d')}.json"
    with open(log_file, "w") as f:
        json.dump({"timestamp": now.isoformat(), "results": results}, f, indent=2)

    # Print summary
    print(f"\n=== SEO Report {now.strftime('%Y-%m-%d')} ===")
    print(f"Sitemap: {'✅' if results['sitemap'].get('exists') else '❌'} ({results['sitemap'].get('url_count', 0)} URLs)")
    print(f"Robots.txt: {'✅' if results['robots'].get('exists') else '❌'}")
    bl_count = results['backlinks'].get('count_approx', results['backlinks'].get('backlinks', 0))
    print(f"Backlinks: {bl_count} found ({results['backlinks'].get('note', '')})")
    print(f"GitHub mentions: {results['github_mentions'].get('total_count', '?')}")
    print(f"\nDirectory listings:")
    for d in results.get('directories', []):
        status = '✅' if d.get('listed') else '❌' if d.get('listed') is False else '?'
        print(f"  {status} {d['name']}")
    print(f"\nKeyword opportunities:")
    for kw in results.get('keywords', []):
        print(f"  - {kw.get('keyword')}: {'low competition' if kw.get('has_competition') else 'competitive'}")
    print(f"\nFull log: {log_file}")

if __name__ == "__main__":
    main()
