#!/usr/bin/env python3
"""
Channel Discovery — Each week, try 3 new platforms
Track what works, what fails, why.
"""
import subprocess, json, time
from datetime import datetime

AGENTS_DIR = "/home/mistlight/.openclaw/workspace/agents/marketing"
LOG_DIR = f"{AGENTS_DIR}/logs"
CHANNEL_LOG = f"{LOG_DIR}/channel_discovery.json"

CHANNELS_TO_TRY = [
    # Name, URL check, submission method, difficulty
    ("dev.to", "https://dev.to", "api", "hard"),  # needs API key
    ("stackoverflow", "https://stackoverflow.com", "answer", "medium"),  # answer questions
    ("quora", "https://quora.com", "answer", "medium"),  # answer questions
    ("reddit-r-programming", "https://reddit.com/r/rprogramming", "post", "hard"),  # needs karma
    ("reddit-programming", "https://reddit.com/r/programming", "post", "hard"),
    ("hackernews", "https://news.ycombinator.com", "submit", "impossible"),  # needs account
    ("lobsters", "https://lobste.rs", "submit", "impossible"),  # needs invite
    ("producthunt", "https://producthunt.com", "submit", "hard"),  # needs real product
    ("indiehackers", "https://indiehackers.com", "post", "medium"),  # web form
    ("medium", "https://medium.com", "article", "hard"),  # needs account
    ("dev.to-gist", "https://gist.github.com", "gist", "blocked"),  # PAT read-only
    ("slashdot", "https://slashdot.org", "submit", "medium"),  # web form
    ("newsbrew", "https://newsbrew.io", "submit", "unknown"),
    ("toolhunt", "https://toolhunt.ai", "submit", "easy"),  # already listed
    ("theresanaiforthat", "https://theresanaiforthat.com", "submit", "easy"),  # already listed
    ("alternativeTo", "https://alternativeto.net", "submit", "blocked"),  # 403
    ("saashub", "https://saashub.com", "submit", "easy"),  # already listed
    ("productpapa", "https://productpapa.com", "submit", "unknown"),
    ("stackshare", "https://stackshare.io", "submit", "unknown"),
    ("github-readme", "https://github.com/RalphWorkflow/Ralph-Workflow", "update", "blocked"),  # PAT read-only
    ("RSS directories", "https://blogsearch.google.com", "submit", "medium"),
    ("dmoz", "https://dmoz-odp.org", "submit", "blocked"),  # shut down
    ("dirwell", "https://dirwell.com", "submit", "unknown"),
    ("smashingmagazine", "https://smashingmagazine.com", "contribute", "hard"),
    ("css-tricks", "https://css-tricks.com", "article", "hard"),
]

def check_platform(name, url, method, difficulty):
    """Test if a platform is accessible and what it would take to post."""
    result = {"name": name, "url": url, "method": method, "difficulty": difficulty, "timestamp": datetime.now().isoformat()}
    
    try:
        r = subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "8", url],
                         capture_output=True, text=True, timeout=10)
        code = r.stdout.strip()
        result["http_code"] = code
        
        if code == "200":
            result["status"] = "accessible"
        elif code == "403":
            result["status"] = "blocked"
            result["note"] = "Cloudflare or bot protection"
        elif code == "301":
            result["status"] = "redirects"
        else:
            result["status"] = f"http_{code}"
    except Exception as e:
        result["status"] = "error"
        result["note"] = str(e)[:50]
    
    return result

def try_stackoverflow_answer():
    """Try to find and answer a relevant Stack Overflow question."""
    # Search for questions about AI agents, unattended workflows
    search_url = "https://api.stackexchange.com/2.3/search/excerpts?order=desc&sort=relevance&q=AI%20agent%20workflow&site=stackoverflow&filter=withbody"
    
    try:
        r = subprocess.run(["curl", "-s", "--max-time", "10", search_url], capture_output=True, text=True, timeout=15)
        data = json.loads(r.stdout)
        if "items" in data and len(data["items"]) > 0:
            question = data["items"][0]
            return {
                "action": "answer",
                "question_id": question.get("question_id"),
                "title": question.get("title"),
                "link": question.get("link"),
                "score": question.get("score"),
                "status": "found_question"
            }
    except Exception as e:
        return {"action": "answer", "status": "error", "note": str(e)[:50]}
    
    return {"action": "answer", "status": "no_questions_found"}

def try_devto_api():
    """Check if we can post to dev.to via API (needs key)."""
    # Try reading published articles to see if account exists
    try:
        r = subprocess.run(["curl", "-s", "https://dev.to/api/articles?username=ralphworkflow"],
                         capture_output=True, text=True, timeout=10)
        data = json.loads(r.stdout)
        if isinstance(data, list) and len(data) > 0:
            return {"status": "has_articles", "count": len(data)}
        elif isinstance(data, dict) and data.get("error"):
            return {"status": "no_account", "error": data.get("error")}
    except Exception as e:
        return {"status": "error", "note": str(e)[:50]}
    return {"status": "unknown"}

def load_discovery_log():
    if os.path.exists(CHANNEL_LOG):
        with open(CHANNEL_LOG) as f:
            return json.load(f)
    return {"tried": [], "results": [], "working": []}

import os

def save_discovery_log(log):
    with open(CHANNEL_LOG, "w") as f:
        json.dump(log, f, indent=2)

def main():
    log = load_discovery_log()
    
    print(f"[Discovery] Running at {datetime.now().isoformat()}")
    print(f"[Discovery] Previously tried: {len(log['tried'])} channels")
    print(f"[Discovery] Working channels: {len(log.get('working', []))}")
    
    # Pick 3 untested channels to try this week
    remaining = [c for c in CHANNELS_TO_TRY if c[0] not in log["tried"]]
    to_try = remaining[:5]
    
    print(f"\n[Discovery] Testing {len(to_try)} new channels:")
    
    for name, url, method, difficulty in to_try:
        print(f"\n  Testing {name}...", flush=True)
        
        result = check_platform(name, url, method, difficulty)
        print(f"    HTTP: {result.get('http_code', 'N/A')} | Status: {result.get('status', 'unknown')}")
        
        # Special checks
        if name == "stackoverflow":
            so_result = try_stackoverflow_answer()
            result["stackoverflow"] = so_result
            print(f"    SO: {so_result.get('status')}")
        
        elif name == "dev.to":
            dt_result = try_devto_api()
            result["devto"] = dt_result
            print(f"    dev.to: {dt_result.get('status')}")
        
        log["tried"].append(name)
        log["results"].append(result)
        
        time.sleep(0.5)
    
    # Identify working channels
    working = []
    for r in log["results"]:
        if r.get("status") == "accessible" and r.get("difficulty") in ["easy", "medium"]:
            working.append({
                "name": r["name"],
                "url": r["url"],
                "method": r["method"],
                "difficulty": r["difficulty"]
            })
    
    log["working"] = working
    
    # Save
    save_discovery_log(log)
    
    print(f"\n=== Discovery Results ===")
    print(f"Accessible: {[r['name'] for r in log['results'] if r.get('status') == 'accessible']}")
    print(f"Blocked: {[r['name'] for r in log['results'] if r.get('status') == 'blocked']}")
    print(f"Redirects: {[r['name'] for r in log['results'] if r.get('status') == 'redirects']}")
    
    if working:
        print(f"\n✅ Actionable channels found:")
        for w in working:
            print(f"  - {w['name']} ({w['method']}) — {w['difficulty']}")

if __name__ == "__main__":
    main()
