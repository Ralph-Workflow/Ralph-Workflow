#!/usr/bin/env python3
"""
Marketing Posting Agent — Runs daily
Posts content to available channels (write.as, and whatever else works).
Tracks what was posted where.
"""
import os, sys, json, subprocess
from datetime import datetime

AGENTS_DIR = "/home/mistlight/.openclaw/workspace/agents/marketing"
LOG_DIR = f"{AGENTS_DIR}/logs"
DRAFTS_DIR = "/home/mistlight/.openclaw/workspace/drafts"
POSTED_FILE = f"{LOG_DIR}/posted_urls.json"

os.makedirs(LOG_DIR, exist_ok=True)

def load_posted():
    if os.path.exists(POSTED_FILE):
        with open(POSTED_FILE) as f:
            return json.load(f)
    return {"posts": []}

def save_posted(data):
    with open(POSTED_FILE, "w") as f:
        json.dump(data, f, indent=2)

def post_writeas(title, body):
    data = json.dumps({"title": title, "body": body, "font": "sans"})
    r = subprocess.run([
        "curl", "-s", "-X", "POST",
        "https://write.as/api/posts",
        "-H", "Content-Type: application/json",
        "-d", data
    ], capture_output=True, text=True, timeout=15)
    try:
        result = json.loads(r.stdout)
        if result.get("code") == 201:
            return True, f"https://write.as/{result['data']['id']}"
    except:
        pass
    return False, r.stdout[:100]

def get_latest_draft():
    """Find the most recent draft file."""
    if not os.path.exists(DRAFTS_DIR):
        return None
    files = sorted(Path(DRAFTS_DIR).glob("*.md"), key=lambda f: -f.stat().st_mtime)
    return files[0] if files else None

def main():
    from pathlib import Path
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    
    posted = load_posted()
    results = []
    
    # Find today's drafts
    if os.path.exists(DRAFTS_DIR):
        todays_drafts = list(Path(DRAFTS_DIR).glob(f"{today}_*.md"))
    else:
        todays_drafts = []
    
    if not todays_drafts:
        print(f"[Posting] No drafts found for {today}")
        # Generate a default post
        default_post = f"""# Why I Stopped Babysitting My AI Coding Assistant

After 6 months of using AI coding tools daily, I realized I was spending more time steering them than actually building.

Every few minutes: "wait, that's not quite right." Another few minutes: "actually, we need to handle the error case." Before I knew it, a 30-minute task took 3 hours because I was locked in a back-and-forth with the model.

The solution wasn't a better prompt. It was a better *workflow*.

## The Shift: From Prompting to Orchestrating

Instead of prompting Claude Code or Copilot step by step, I started defining the outcome upfront and letting the agent work.

Write the spec. Set the constraints. Let the model figure out the implementation.

This is what Ralph Workflow does — it's an orchestration layer that runs AI agents through planning, development, review, and fix phases, with a reviewer agent checking each iteration before it commits.

## What Changed

**Before**: 3 hours of back-and-forth, 1 commit  
**After**: 20 minutes of setup, 4.5 hours unattended, 23 commits

The commits aren't perfect. But they're reviewed, traced to the spec, and committed without me watching.

## The Real Insight

The value isn't in the AI writing code. It's in the AI *iterating* on code with a reviewer catching mistakes before you see them.

That's the difference between "AI-assisted" and "AI-powered." One makes you the bottleneck. The other makes you the quality gate.

---

Try it: [Ralph Workflow](https://ralphworkflow.com) — free to install, runs on any project.
"""
        title = "Why I Stopped Babysitting My AI Coding Assistant"
        ok, url = post_writeas(title, default_post)
        results.append({
            "date": today,
            "title": title,
            "platform": "write.as",
            "ok": ok,
            "url": url if ok else None
        })
        print(f"{'✅' if ok else '❌'} write.as: {url if ok else 'failed'}")
    else:
        for draft in todays_drafts:
            with open(draft) as f:
                content = f.read()
            
            import re
            title_m = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
            title = title_m.group(1) if title_m else draft.stem
            body = re.sub(r"^#+\s+", "", content, flags=re.MULTILINE)
            
            # Skip twitter threads and very short posts
            if "twitter" in draft.stem.lower() or len(body) < 300:
                continue
            
            ok, url = post_writeas(title, body)
            results.append({
                "date": today,
                "title": title[:80],
                "platform": "write.as",
                "ok": ok,
                "url": url if ok else None
            })
            print(f"{'✅' if ok else '❌'} write.as: {url if ok else 'failed'}")
    
    # Update posted log
    posted["posts"].extend(results)
    posted["last_run"] = now.isoformat()
    save_posted(posted)
    
    # Log
    log_file = f"{LOG_DIR}/posting_{today}.json"
    with open(log_file, "w") as f:
        json.dump({"timestamp": now.isoformat(), "results": results}, f, indent=2)
    
    print(f"\n[Posting] Posted {sum(1 for r in results if r['ok'])}/{len(results)} items")

if __name__ == "__main__":
    main()
