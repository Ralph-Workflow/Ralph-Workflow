#!/usr/bin/env python3
"""
Community Agent — Runs daily
Posts content to HN, Reddit, Dev.to, Twitter.
Generates posting checklists for manual posting (no API needed).
"""
import os, json
from datetime import datetime

LOG_DIR = "/home/mistlight/.openclaw/workspace/agents/community/logs"
OUT_DIR = "/home/mistlight/.openclaw/workspace/drafts"
os.makedirs(LOG_DIR, exist_ok=True)

def get_todays_content():
    today = datetime.now().strftime('%Y-%m-%d')
    if not os.path.exists(OUT_DIR): return []
    return [f for f in os.listdir(OUT_DIR) if today in f and not f.endswith('.md')]

def make_hn_checklist(content):
    lines = content.split('\n')
    title = next((l for l in lines if len(l) > 10), "See details")[:100]
    txt = "\n".join(lines[1:20])[:2000]
    script = f"""
=== HN POSTING CHECKLIST ===
URL: https://news.ycombinator.com/submit
Title: {title}
Text:\n{txt}
---
1. Open URL
2. Fill form
3. Submit
"""
    return script

def make_lobsters_checklist(content):
    lines = content.split('\n')
    title = next((l for l in lines if len(l) > 10), "See details")[:80]
    script = f"""
=== LOBSTERS POSTING CHECKLIST ===
URL: https://lobste.rs/stories/new
Tags: coding, programming, ai, tools
Title: {title}
Story: {content[:1500]}
---
1. Open URL
2. Fill form
3. Submit
"""
    return script

def make_reddit_checklist(content, sub="cscareerquestions"):
    lines = content.split('\n')
    title = next((l for l in lines if len(l) > 10), "See details")[:300]
    script = f"""
=== REDDIT POSTING CHECKLIST ===
URL: https://www.reddit.com/r/{sub}/submit
Title: {title}
Body: {content[:3000]}
---
1. Open URL
2. Fill form
3. Submit
"""
    return script

def main():
    print(f"[Community] Starting at {datetime.now().isoformat()}")
    files = get_todays_content()
    results = {"timestamp": datetime.now().isoformat(), "files_found": len(files), "actions": []}
    
    for fname in files:
        path = os.path.join(OUT_DIR, fname)
        with open(path) as f: content = f.read()
        
        if 'twitter' in fname or 'thread' in fname:
            results["actions"].append({"platform": "twitter", "file": fname, "status": "manual_post_needed"})
        elif 'hackernews' in fname or 'hn' in fname:
            script = make_hn_checklist(content)
            out = f"{OUT_DIR}/checklist_{fname}"
            with open(out, 'w') as f: f.write(script)
            results["actions"].append({"platform": "hackernews", "file": out, "status": "checklist_ready"})
        elif 'lobsters' in fname:
            script = make_lobsters_checklist(content)
            out = f"{OUT_DIR}/checklist_{fname}"
            with open(out, 'w') as f: f.write(script)
            results["actions"].append({"platform": "lobsters", "file": out, "status": "checklist_ready"})
        elif 'reddit' in fname:
            script = make_reddit_checklist(content)
            out = f"{OUT_DIR}/checklist_{fname}"
            with open(out, 'w') as f: f.write(script)
            results["actions"].append({"platform": "reddit", "file": out, "status": "checklist_ready"})
        elif 'devto' in fname or 'article' in fname:
            results["actions"].append({"platform": "devto", "file": fname, "status": "needs_devto_api"})
    
    log_file = f"{LOG_DIR}/{datetime.now().strftime('%Y-%m-%d')}.json"
    with open(log_file, 'w') as f: json.dump(results, f, indent=2)
    
    for a in results["actions"]:
        print(f"[Community] {a['platform']}: {a['status']}")
    print(f"[Community] Done.")
    return 0

if __name__ == "__main__":
    import sys
    sys.exit(main())
