#!/usr/bin/env python3
"""
Weekly Reflection & Analysis — Runs every Monday
Collects data, scores channels, updates strategy, generates next actions.
"""
import os, json, subprocess
from datetime import datetime, timedelta
from pathlib import Path

AGENTS_DIR = "/home/mistlight/.openclaw/workspace/agents/marketing"
LOG_DIR = f"{AGENTS_DIR}/logs"
STRATEGY_FILE = f"{AGENTS_DIR}/STRATEGY.md"

CHANNEL_SCORES_FILE = f"{LOG_DIR}/channel_scores.json"

# Channel performance tracking
CHANNEL_WEIGHTS = {
    "writeas": {"views": 1.0, "low_effort": 0.5},
    "telegraph": {"views": 0.8, "low_effort": 0.5},
    "github_outreach": {"backlinks": 5.0, "prs": 3.0, "issues": 1.0},
    "seo": {"rankings": 2.0, "backlinks": 3.0},
    "directory": {"backlinks": 1.0, "referral_traffic": 2.0},
}

def load_channel_scores():
    if os.path.exists(CHANNEL_SCORES_FILE):
        with open(CHANNEL_SCORES_FILE) as f:
            return json.load(f)
    return {"channels": {}, "last_update": None}

def save_channel_scores(scores):
    scores["last_update"] = datetime.now().isoformat()
    with open(CHANNEL_SCORES_FILE, "w") as f:
        json.dump(scores, f, indent=2)

def get_writeas_views():
    """Get view counts for all write.as posts."""
    try:
        r = subprocess.run([
            "curl", "-s", "--max-time", "10",
            "https://write.as/api/posts/me/posts?access_token=bE6eEGPmW3gNMHLsIDt6pSnPzpXwIowtK8g2r5WqVs"
        ], capture_output=True, text=True, timeout=15)
        data = json.loads(r.stdout)
        if data.get("code") == 200:
            posts = data.get("data", [])
            return [{"id": p["id"], "title": p["title"], "views": p.get("views", 0), 
                    "url": f"https://write.as/{p['id']}", "date": p.get("created_at", "")[:10]}
                   for p in posts]
    except Exception as e:
        pass
    return []

def check_seo_rankings():
    """Check if target keywords improved."""
    # This would normally use an SEO API - for now, return placeholder
    # In production, use SerpAPI or similar
    return {"ranked_keywords": [], "note": "Needs SEO API for live data"}

def get_github_data():
    """Get GitHub stats for Ralph-Workflow repos."""
    repos = ["Ralph-Workflow/Ralph-Workflow", "mistlight/Ralph-Workflow", "mistlight/Ralph-Site-Rs"]
    results = []
    for repo in repos:
        try:
            r = subprocess.run([
                "curl", "-s", "--max-time", "8",
                f"https://api.github.com/repos/{repo}"
            ], capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                data = json.loads(r.stdout)
                if "full_name" in data:
                    results.append({
                        "repo": data["full_name"],
                        "stars": data.get("stargazers_count", 0),
                        "forks": data.get("forks_count", 0),
                        "issues": data.get("open_issues_count", 0),
                        "description": data.get("description", ""),
                    })
        except:
            pass
        import time; time.sleep(0.3)
    return results

def score_channels(scores, new_data):
    """Score each channel based on recent performance."""
    # write.as scoring
    total_views = sum(p["views"] for p in new_data.get("writeas", []))
    num_posts = len(new_data.get("writeas", []))
    
    if "writeas" not in scores["channels"]:
        scores["channels"]["writeas"] = {"score": 0, "history": []}
    
    writeas_score = 0
    if num_posts > 0:
        avg_views = total_views / num_posts
        # Score: 0-10 based on avg views per post
        writeas_score = min(10, avg_views / 10)
    
    scores["channels"]["writeas"]["history"].append({
        "date": datetime.now().isoformat()[:10],
        "total_views": total_views,
        "num_posts": num_posts,
        "score": writeas_score
    })
    scores["channels"]["writeas"]["score"] = writeas_score
    
    # GitHub scoring  
    total_stars = sum(r["stars"] for r in new_data.get("github", []))
    if "github" not in scores["channels"]:
        scores["channels"]["github"] = {"score": 0, "history": []}
    
    gh_score = min(10, total_stars / 10)
    scores["channels"]["github"]["history"].append({
        "date": datetime.now().isoformat()[:10],
        "total_stars": total_stars,
        "score": gh_score
    })
    scores["channels"]["github"]["score"] = gh_score
    
    # SEO scoring
    if "seo" not in scores["channels"]:
        scores["channels"]["seo"] = {"score": 5, "history": []}  # Start at 5, baseline
    
    scores["channels"]["seo"]["history"].append({
        "date": datetime.now().isoformat()[:10],
        "note": "No live SEO API - using site health as proxy"
    })
    
    return scores

def generate_reflection(scores):
    """Generate text reflection on performance."""
    lines = []
    today = datetime.now().strftime("%Y-%m-%d")
    lines.append(f"## Weekly Reflection — {today}\n")
    
    # Channel scores
    lines.append("### Channel Scores (0-10)")
    for ch, data in sorted(scores["channels"].items(), key=lambda x: -x[1].get("score", 0)):
        score = data.get("score", 0)
        emoji = "🟢" if score >= 7 else "🟡" if score >= 4 else "🔴"
        hist = data.get("history", [])
        recent = hist[-1] if hist else {}
        trend = ""
        if len(hist) >= 2:
            delta = recent.get("score", 0) - hist[-2].get("score", 0)
            trend = f" ({'+' if delta > 0 else ''}{delta:.1f})"
        lines.append(f"{emoji} **{ch}**: {score:.1f}{trend}")
    
    # Top performing content
    lines.append("\n### Top Content")
    writeas = scores.get("last_data", {}).get("writeas", [])
    if writeas:
        sorted_posts = sorted(writeas, key=lambda x: -x.get("views", 0))[:3]
        for p in sorted_posts:
            lines.append(f"- *{p.get('title', 'Untitled')[:60]}* — {p.get('views', 0)} views")
    else:
        lines.append("- No content data yet")
    
    # Recommendations
    lines.append("\n### Recommendations")
    writeas_score = scores["channels"].get("writeas", {}).get("score", 0)
    gh_score = scores["channels"].get("github", {}).get("score", 0)
    
    if writeas_score < 5:
        lines.append("- 🔴 write.as content needs improvement — try better headlines or keywords")
    if gh_score < 3:
        lines.append("- 🔴 GitHub presence weak — need write-access PAT to enable outreach")
    
    if writeas_score >= 5 and gh_score >= 3:
        lines.append("- 🟢 Both channels healthy — focus on scaling what works")
    
    lines.append("- 💡 Try new content format this week (comparison, tutorial, case study)")
    lines.append("- 💡 If new credentials available, re-run channel discovery")
    
    return "\n".join(lines)

def update_strategy_with_reflection(reflection_text):
    """Append reflection to strategy file."""
    try:
        with open(STRATEGY_FILE) as f:
            content = f.read()
        
        # Remove old weekly reflections
        import re
        pattern = r"## Weekly Reflection.*?(?=\n## |\Z)"
        content = re.sub(pattern, "", content, flags=re.DOTALL)
        
        content += "\n\n" + reflection_text
        
        with open(STRATEGY_FILE, "w") as f:
            f.write(content)
        return True
    except Exception as e:
        print(f"Strategy update error: {e}")
        return False

def main():
    print(f"[WeeklyReview] Starting at {datetime.now().isoformat()}")
    
    scores = load_channel_scores()
    
    # Collect new data
    print("[WeeklyReview] Collecting data...")
    new_data = {
        "writeas": get_writeas_views(),
        "github": get_github_data(),
        "seo": check_seo_rankings(),
    }
    
    print(f"[WeeklyReview] write.as posts: {len(new_data['writeas'])}")
    print(f"[WeeklyReview] GitHub repos: {len(new_data['github'])}")
    
    total_views = sum(p["views"] for p in new_data["writeas"])
    print(f"[WeeklyReview] Total write.as views: {total_views}")
    
    total_stars = sum(r["stars"] for r in new_data["github"])
    print(f"[WeeklyReview] Total GitHub stars: {total_stars}")
    
    # Score channels
    scores = score_channels(scores, new_data)
    scores["last_data"] = new_data
    save_channel_scores(scores)
    
    # Generate reflection
    reflection = generate_reflection(scores)
    print("\n" + reflection)
    
    # Update strategy
    update_strategy_with_reflection(reflection)
    
    # Save full log
    log_file = f"{LOG_DIR}/weekly_review_{datetime.now().strftime('%Y-%m-%d')}.json"
    with open(log_file, "w") as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "scores": scores,
            "new_data": new_data,
            "reflection": reflection
        }, f, indent=2)
    
    print(f"\n[WeeklyReview] Log: {log_file}")

if __name__ == "__main__":
    main()
