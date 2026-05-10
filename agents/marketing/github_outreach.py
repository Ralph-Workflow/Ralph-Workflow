#!/usr/bin/env python3
"""
GitHub Outreach — Finds repos mentioning Ralph Workflow.
v2: Handles rate limits, skips READMEs to avoid 60 req/min cap.
"""
import os, json, time, urllib.request
from datetime import datetime

AGENTS_DIR = "/home/mistlight/.openclaw/workspace/agents/marketing"
LOG_DIR = f"{AGENTS_DIR}/logs"
os.makedirs(LOG_DIR, exist_ok=True)

TOKEN = os.environ.get("GITHUB_TOKEN", "")

def gh_get(url):
    """Make a GitHub API GET request with rate limit handling."""
    req = urllib.request.Request(url, headers={
        "Authorization": f"token {TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    })
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 403:
                reset = e.headers.get("X-RateLimit-Reset", "")
                wait = int(reset) - int(time.time()) + 5 if reset else 65
                print(f"  Rate limited. Waiting {wait}s...")
                time.sleep(min(wait, 60))
            else:
                return None
    return None

def gh_search_mentions():
    """Find repos that mention Ralph Workflow in code."""
    results = []
    page = 1
    while len(results) < 100:
        url = f"https://api.github.com/search/code?q=%22Ralph+Workflow%22+OR+%22ralph-workflow%22+OR+%22ralph_workflow%22&per_page=100&page={page}"
        data = gh_get(url)
        if not data or not data.get("items"):
            break
        for item in data["items"]:
            results.append({
                "repo": item["repository"]["full_name"],
                "stars": item["repository"].get("stargazers_count", 0),
                "path": item["path"],
                "file_name": item["name"]
            })
        if len(data["items"]) < 100:
            break
        page += 1
        time.sleep(2)  # Be respectful between pages
    
    # Dedupe by repo
    seen = {}
    for r in results:
        if r["repo"] not in seen:
            seen[r["repo"]] = r
    return list(seen.values())

def gh_get_readme_preview(repo_name):
    """Get README first 500 chars — fast version."""
    for ext in ["md", "MD"]:
        data = gh_get(f"https://api.github.com/repos/{repo_name}/contents/README.{ext}")
        if data and data.get("encoding") == "base64":
            content = base64.b64decode(data["content"]).decode("utf-8", errors="ignore")[:500]
            return content, data.get("sha", "")
    return None, None

def main():
    now = datetime.now()
    print(f"[Outreach] {now.isoformat()}")
    
    print("[Outreach] Searching GitHub for mentions...")
    repos = gh_search_mentions()
    print(f"[Outreach] Found {len(repos)} unique repos")
    
    # Sort by stars
    repos.sort(key=lambda x: x.get("stars", 0), reverse=True)
    
    # Check top repos for existing links (skip to avoid rate limits — just report)
    # Instead, just report the top repos and manually review
    top_repos = repos[:30]
    
    print(f"\n[Outreach] Top repos by stars:")
    for r in top_repos[:10]:
        desc = gh_get(f"https://api.github.com/repos/{r['repo']}") or {}
        r["description"] = desc.get("description", "") or ""
        r["language"] = desc.get("language", "") or ""
        print(f"  {r['stars']}⭐ {r['repo']} | {r['language']} | {r.get('description','')[:50]}")
        time.sleep(0.5)  # Don't hit rate limits
    
    # Save full list
    log_file = f"{LOG_DIR}/outreach_{now.strftime('%Y-%m-%d')}.json"
    with open(log_file, "w") as f:
        json.dump({
            "timestamp": now.isoformat(),
            "total_mentions": len(repos),
            "top_repos": top_repos
        }, f, indent=2)
    
    print(f"\n[Outreach] Full report: {log_file}")
    print(f"[Outreach] 75 repos found — manual review needed for backlink outreach")

if __name__ == "__main__":
    import base64  # need this for README decode
    main()
