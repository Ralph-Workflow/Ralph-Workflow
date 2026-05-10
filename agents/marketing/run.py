#!/usr/bin/env python3
"""
Marketing Agent — Runs daily + weekly strategy review
Evaluates traffic, SEO, content performance, and adapts strategy.
"""
import os, json, subprocess, re, urllib.request, urllib.parse
from datetime import datetime, timedelta

AGENTS_DIR = "/home/mistlight/.openclaw/workspace/agents/marketing"
STRATEGY_FILE = f"{AGENTS_DIR}/STRATEGY.md"
LOG_DIR = f"{AGENTS_DIR}/logs"
os.makedirs(LOG_DIR, exist_ok=True)

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
PUBLISHED_URLS_FILE = "/home/mistlight/.openclaw/workspace/agents/content/published_urls.json"

# ─── DATA COLLECTION ────────────────────────────────────────────────────────

def check_site_health():
    """Check if ralphworkflow.com is responding and returning 200."""
    try:
        req = urllib.request.Request("https://ralphworkflow.com", method="HEAD")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return {"ok": True, "status": resp.status}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def check_seo_rankings():
    """Check if ralphworkflow.com ranks for target keywords."""
    keywords = [
        "Ralph Workflow AI",
        "AI agent workflow composer",
        "unattended AI coding pipeline",
        "AI engineering workflow",
    ]
    results = []
    for kw in keywords:
        try:
            query = urllib.parse.quote(f"site:ralphworkflow.com {kw}")
            url = f"https://www.google.com/search?q={query}&num=5"
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1)"
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
                in_results = "ralphworkflow.com" in html
                results.append({"keyword": kw, "ranked": in_results})
        except Exception as e:
            results.append({"keyword": kw, "error": str(e)})
    return results

def check_github_stars():
    """Check GitHub stars on Ralph-Workflow repo."""
    if not GITHUB_TOKEN:
        return {"error": "No GitHub token"}
    try:
        req = urllib.request.Request(
            "https://api.github.com/repos/mistlight/Ralph-Workflow",
            headers={"Authorization": f"token {GITHUB_TOKEN}"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return {
                "stars": data.get("stargazers_count", 0),
                "forks": data.get("forks_count", 0),
                "subscribers": data.get("subscribers_count", 0),
                "open_issues": data.get("open_issues_count", 0),
                "description": data.get("description", ""),
            }
    except Exception as e:
        return {"error": str(e)}

def check_writeas_views():
    """Check view counts on write.as posts."""
    published = {"articles": []}
    if os.path.exists(PUBLISHED_URLS_FILE):
        try:
            with open(PUBLISHED_URLS_FILE) as f:
                published = json.load(f)
        except:
            pass
    
    results = []
    for article in published.get("articles", []):
        if article.get("platform") == "writeas" and "url" in article:
            url = article["url"].replace(".md", "")
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    html = resp.read().decode("utf-8", errors="ignore")
                    views_match = re.search(r'(\d+)\s+views?', html)
                    if views_match:
                        results.append({
                            "title": article.get("title", ""),
                            "url": url,
                            "views": int(views_match.group(1))
                        })
            except:
                results.append({"title": article.get("title", ""), "url": url, "views": 0})
    return results

def check_linkedin_mentions():
    """Check if ralphworkflow is mentioned on LinkedIn (rough check via search)."""
    try:
        query = urllib.parse.quote('"Ralph Workflow" site:linkedin.com')
        url = f"https://www.google.com/search?q={query}&num=3"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1)"
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
            count = html.count("linkedin.com")
            return {"mentions": count}
    except Exception as e:
        return {"error": str(e)}

# ─── STRATEGY UPDATE ───────────────────────────────────────────────────────

def update_strategy_report(data):
    """Append a weekly report to the strategy file."""
    today = datetime.now().strftime("%Y-%m-%d")
    
    report = f"""
## Strategy Review — {today}

### Site Health
- ralphworkflow.com: {data['site_health']}

### SEO Rankings
"""
    for kw in data.get("seo_rankings", []):
        status = "✅" if kw.get("ranked") else "❌"
        report += f"- {status} {kw.get('keyword', '?')}\n"
    
    report += f"""
### GitHub
- Stars: {data.get('github', {}).get('stars', '?')}
- Forks: {data.get('github', {}).get('forks', '?')}

### Content Performance (write.as)
"""
    for article in data.get("writeas_views", []):
        report += f"- [{article.get('title', '')[:50]}]({article.get('url', '')}) — {article.get('views', 0)} views\n"
    
    report += f"""
### Recommendations
{generate_recommendations(data)}

---
"""
    
    # Append to strategy file
    try:
        with open(STRATEGY_FILE) as f:
            content = f.read()
        
        # Find "Last Strategy Review" and replace everything after it
        marker = "## Last Strategy Review"
        if marker in content:
            parts = content.split(marker)
            content = parts[0] + marker + "\n" + report.split("---")[0] + "\n" + "".join(parts[1].split("---")[1:])
        else:
            content += "\n" + report
        
        with open(STRATEGY_FILE, "w") as f:
            f.write(content)
    except Exception as e:
        return f"Strategy update failed: {e}"
    
    return "Strategy updated"

def generate_recommendations(data):
    """Generate recommendations based on collected data."""
    recs = []
    
    github = data.get("github", {})
    if github.get("stars", 0) < 10:
        recs.append("- Low GitHub visibility. Consider: posting to relevant subreddits, asking for stars from early users")
    
    seo_ranks = [kw for kw in data.get("seo_rankings", []) if kw.get("ranked")]
    if len(seo_ranks) == 0:
        recs.append("- No SEO presence yet. Focus on building backlinks from dev blogs and GitHub READMEs")
    
    views = data.get("writeas_views", [])
    total_views = sum(a.get("views", 0) for a in views)
    if total_views < 50 and len(views) > 0:
        recs.append("- Low content traction. Try: more engaging titles, posting to Hacker News, Reddit threads")
    
    if not recs:
        recs.append("- Metrics look healthy. Keep posting consistently and watch for content that drives GitHub stars")
    
    return "\n".join(recs)

# ─── AUTOMATED ACTIONS ─────────────────────────────────────────────────────

def auto_post_content():
    """Post content to available platforms."""
    results = []
    
    # Check for new drafts
    drafts_dir = "/home/mistlight/.openclaw/workspace/drafts"
    today = datetime.now().strftime("%Y-%m-%d")
    
    if not os.path.exists(drafts_dir):
        return results
    
    todays_drafts = [f for f in os.listdir(drafts_dir) if today in f]
    
    for draft_file in todays_drafts:
        path = os.path.join(drafts_dir, draft_file)
        with open(path) as f:
            content = f.read()
        
        title_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        title = title_match.group(1) if title_match else "Untitled"
        body = re.sub(r"^#+\s+", "", content, flags=re.MULTILINE)
        
        # Post to write.as
        ok, res = post_writeas(title, body)
        results.append(f"{'✅' if ok else '❌'} write.as: {res}")
        
        # Post to Telegraph
        ok, res = post_telegraph(title, body[:5000])
        results.append(f"{'✅' if ok else '❌'} Telegraph: {res}")
    
    return results

def post_writeas(title, body):
    import urllib.request, json as jsonlib
    data = jsonlib.dumps({"title": title, "body": body}).encode()
    req = urllib.request.Request(
        "https://api.write.as/new",
        data=data,
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = jsonlib.loads(resp.read())
            return True, result.get("url", f"https://write.as/{result.get('id')}")
    except Exception as e:
        return False, str(e)

def post_telegraph(title, body):
    import urllib.request, urllib.parse, json as jsonlib
    try:
        with urllib.request.urlopen("https://api.telegra.ph/createAccount?short_name=agent&author_name=Marketing+Bot", timeout=10) as resp:
            account = jsonlib.loads(resp.read())
            if account.get("ok"):
                token = account["result"]["access_token"]
            else:
                return False, "Token failed"
    except Exception as e:
        return False, f"Token: {e}"

    data = urllib.parse.urlencode({
        "access_token": token,
        "title": title,
        "author_name": "Marketing Bot",
        "content": f"<p>{body}</p>",
        "return_content": "false"
    }).encode()

    try:
        req = urllib.request.Request("https://api.telegra.ph/createArticle", data=data)
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = jsonlib.loads(resp.read())
            if result.get("ok"):
                return True, result["result"]["url"]
            return False, result.get("error", "Unknown")
    except Exception as e:
        return False, str(e)

# ─── MAIN ───────────────────────────────────────────────────────────────────

def main():
    now = datetime.now()
    is_daily = True  # Always run daily
    
    # Run every day: data collection + auto-post
    # Run weekly (Mondays): full strategy update
    is_weekly = now.weekday() == 0  # Monday = 0
    
    print(f"[Marketing] {now.isoformat()} | daily={is_daily} weekly={is_weekly}")
    
    data = {}
    
    # Collect data
    print("[Marketing] Checking site health...")
    data["site_health"] = check_site_health()
    
    print("[Marketing] Checking GitHub stars...")
    data["github"] = check_github_stars()
    
    print("[Marketing] Checking write.as views...")
    data["writeas_views"] = check_writeas_views()
    
    print("[Marketing] Checking SEO rankings...")
    data["seo_rankings"] = check_seo_rankings()
    
    # Auto-post if drafts exist
    print("[Marketing] Running auto-poster...")
    post_results = auto_post_content()
    
    # Weekly strategy review
    if is_weekly:
        print("[Marketing] Running weekly strategy review...")
        update_strategy_report(data)
    
    # Log results
    log_file = f"{LOG_DIR}/marketing_{now.strftime('%Y-%m-%d')}.json"
    with open(log_file, "w") as f:
        json.dump({
            "timestamp": now.isoformat(),
            "is_weekly": is_weekly,
            "data": data,
            "post_results": post_results,
        }, f, indent=2)
    
    # Print summary
    print(f"[Marketing] Site: {data['site_health']}")
    print(f"[Marketing] GitHub: {data.get('github', {}).get('stars', '?')} stars")
    print(f"[Marketing] write.as views: {sum(a.get('views',0) for a in data.get('writeas_views',[]))}")
    ranked = [kw for kw in data.get('seo_rankings', []) if kw.get('ranked')]
    print(f"[Marketing] SEO: {len(ranked)}/{len(data.get('seo_rankings',[]))} keywords ranked")
    for r in post_results:
        print(f"[Marketing] {r}")
    print(f"[Marketing] Log: {log_file}")

if __name__ == "__main__":
    main()
