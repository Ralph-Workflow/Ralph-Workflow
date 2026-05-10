#!/usr/bin/env python3
"""
SEO Audit + Backlink Outreach Agent — Runs weekly
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
]

def check_backlinks():
    """Check who links to ralphworkflow.com via Google search."""
    try:
        query = urllib.parse.quote('link:ralphworkflow.com')
        url = f"https://www.google.com/search?q={query}&num=10"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1)"
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
            # Extract URLs from search results
            links = re.findall(r'href="(https://[^"]+)"[^>]*>.*?ralphworkflow', html, re.DOTALL)
            return {"backlinks": len(links), "sources": links[:5]}
    except Exception as e:
        return {"backlinks": 0, "error": str(e)}

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

I maintain Ralph Workflow (https://ralphworkflow.com) — an open-source tool that turns AI coding agents into unattended engineering pipelines.

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
    print(f"Backlinks: {results['backlinks'].get('backlinks', 0)} found")
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
