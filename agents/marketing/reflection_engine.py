#!/usr/bin/env python3
"""
Reflection Engine — Runs after weekly data collection
Analyzes what happened, what worked, what didn't, generates next actions.
"""
import os, json
from datetime import datetime, timedelta
from pathlib import Path

AGENTS_DIR = "/home/mistlight/.openclaw/workspace/agents/marketing"
LOG_DIR = f"{AGENTS_DIR}/logs"
STRATEGY_FILE = f"{AGENTS_DIR}/STRATEGY.md"

def load_history():
    """Load all historical logs for trend analysis."""
    logs = []
    if os.path.exists(LOG_DIR):
        for f in sorted(Path(LOG_DIR).glob("*.json")):
            try:
                with open(f) as fh:
                    logs.append(json.load(fh))
            except:
                pass
    return logs

def analyze_trends(logs):
    """Detect patterns across historical data."""
    if not logs:
        return {"trends": [], "summary": "No historical data yet"}
    
    # Telegraph view trends (write.as is permanently blocked — do not use)
    telegraph_data = []
    for log in logs:
        if "data" in log and "telegraph_views" in log["data"]:
            views = sum(a.get("views", 0) for a in log["data"]["telegraph_views"])
            ts = log.get("timestamp", "")
            telegraph_data.append({"views": views, "date": ts[:10]})
        elif "data" in log and "writeas_views" in log["data"]:
            # Historical write.as data — no longer actionable (platform blocked)
            pass
    
    # GitHub trends
    github_data = []
    for log in logs:
        if "data" in log and "github" in log["data"]:
            stars = log["data"]["github"].get("stars", 0)
            ts = log.get("timestamp", "")
            github_data.append({"stars": stars, "date": ts[:10]})
    
    # Content performance (telegraph_views preferred; writeas_views is historical only)
    best_article = None
    best_views = 0
    for log in logs:
        if "data" in log:
            # Prefer Telegraph data
            views_key = "telegraph_views" if "telegraph_views" in log["data"] else "writeas_views"
            for a in log["data"].get(views_key, []):
                if a.get("views", 0) > best_views:
                    best_views = a.get("views", 0)
                    best_article = a
    
    trends = []
    
    # Detect view trajectory
    if len(telegraph_data) >= 2:
        if telegraph_data[-1]["views"] > telegraph_data[0]["views"] * 1.5:
            trends.append({"type": "improving", "channel": "telegraph", 
                          "message": f"Views growing: {telegraph_data[0]['views']} → {telegraph_data[-1]['views']}"})
        elif telegraph_data[-1]["views"] < telegraph_data[0]["views"] * 0.5:
            trends.append({"type": "declining", "channel": "telegraph",
                          "message": f"Views dropping: {telegraph_data[0]['views']} → {telegraph_data[-1]['views']}"})
    
    # Best content analysis
    if best_article and best_views > 0:
        trends.append({"type": "insight", "channel": "content",
                      "message": f"Best article: '{best_article.get('title','')[:50]}' with {best_views} views"})
    
    # GitHub activity
    if github_data and len(github_data) >= 2:
        stars_now = github_data[-1]["stars"]
        stars_then = github_data[0]["stars"]
        if stars_now > stars_then:
            trends.append({"type": "improving", "channel": "github",
                          "message": f"GitHub stars: {stars_then} → {stars_now}"})
    
    return {
        "trends": trends,
        "telegraph_data": telegraph_data,
        "github_data": github_data,
        "best_article": best_article,
        "total_logs": len(logs)
    }

def generate_insights(analysis):
    """Generate actionable insights from analysis."""
    insights = []
    trends = analysis.get("trends", [])
    
    for trend in trends:
        if trend["type"] == "declining":
            if trend["channel"] == "telegraph":
                insights.append({
                    "priority": "HIGH",
                    "action": "Telegraph views declining — try new headlines, post at different times, or change topics",
                    "reason": "Content not reaching audience"
                })
        
        if trend["type"] == "improving":
            if trend["channel"] == "telegraph":
                insights.append({
                    "priority": "MEDIUM", 
                    "action": "Telegraph growing — identify what changed (topic, headline, timing) and double down",
                    "reason": "Something is working"
                })
    
    # If no trends yet, give baseline recommendations
    if not insights and analysis.get("total_logs", 0) <= 1:
        insights.append({
            "priority": "INFO",
            "action": "Collecting baseline data — no trends yet. Continue posting consistently for 2-3 weeks before evaluating.",
            "reason": "Need more data points"
        })
    
    # Always recommend next actions
    insights.append({
        "priority": "ONGOING",
        "action": "Wait for GitHub read-write PAT to enable outreach pipeline",
        "reason": "75+ repos need backlink outreach but current token is read-only"
    })
    
    insights.append({
        "priority": "ONGOING", 
        "action": "Try to unblock Twitter when login errors clear",
        "reason": "Twitter would unlock massive reach"
    })
    
    return insights

def update_strategy_reflection(analysis, insights):
    """Append reflection to strategy file."""
    today = datetime.now().strftime("%Y-%m-%d")
    
    reflection = f"""
## Weekly Reflection — {today}

### Trends Detected
"""
    for t in analysis.get("trends", []):
        emoji = "📈" if t["type"] == "improving" else "📉" if t["type"] == "declining" else "💡"
        reflection += f"- {emoji} [{t['channel'].upper()}] {t['message']}\n"
    
    reflection += f"""
### Best Content
"""
    best = analysis.get("best_article")
    if best:
        reflection += f"- *{best.get('title', '')}* — {best.get('views', 0)} views\n"
    else:
        reflection += "- No content data yet\n"
    
    reflection += """
### Action Items
"""
    for ins in insights:
        reflection += f"- **[{ins['priority']}]** {ins['action']}\n"
        reflection += f"  → *{ins['reason']}*\n"
    
    reflection += """
---
"""
    
    try:
        with open(STRATEGY_FILE) as f:
            content = f.read()
        
        # Find last reflection and everything after it
        marker = "## Weekly Reflection"
        if marker in content:
            idx = content.rfind(marker)
            content = content[:idx] + reflection
        else:
            content += "\n" + reflection
        
        with open(STRATEGY_FILE, "w") as f:
            f.write(content)
    except Exception as e:
        return f"Strategy update failed: {e}"
    
    return "Strategy updated with reflection"

def generate_next_experiment(insights):
    """Decide what single experiment to run next."""
    # Priority experiments based on what's actionable
    experiments = [
        {
            "name": "keyword-targeted-content",
            "action": "Write Telegraph post targeting 'unattended coding agent' or 'AI coding workflow automation' — target the keyword gaps from SEO report",
            "expected_impact": "5-20 views/day from search via Telegraph",
            "status": "ready"
        },
        {
            "name": "product-hunt-submission", 
            "action": "Prepare Product Hunt launch assets — needs screenshot, description, promotional video",
            "expected_impact": "100-500 visitors from PH launch",
            "status": "needs_human"
        },
        {
            "name": "github-readme-seo",
            "action": "Update Ralph-Workflow GitHub repo description and add keywords to top of README",
            "expected_impact": "Better GitHub search ranking",
            "status": "ready"
        },
        {
            "name": "alternatives-list",
            "action": "Find 'alternatives to Cursor/Claude Code' pages and submit RW as alternative",
            "expected_impact": "Referral traffic from search",
            "status": "ready"
        },
    ]
    
    # Filter to ready experiments
    ready = [e for e in experiments if e["status"] == "ready"]
    
    # Return the highest priority one
    return ready[0] if ready else {"name": "none", "action": "All experiments need human input or external unblock"}

def main():
    now = datetime.now()
    print(f"[Reflection] Running at {now.isoformat()}")
    
    logs = load_history()
    print(f"[Reflection] Loaded {len(logs)} historical logs")
    
    analysis = analyze_trends(logs)
    print(f"[Reflection] Trends: {len(analysis.get('trends', []))}")
    
    insights = generate_insights(analysis)
    print(f"[Reflection] Insights generated: {len(insights)}")
    
    update_strategy_reflection(analysis, insights)
    
    next_exp = generate_next_experiment(insights)
    print(f"[Reflection] Next experiment: {next_exp['name']}")
    print(f"[Reflection]   → {next_exp['action']}")
    
    # Save analysis
    log_file = f"{LOG_DIR}/reflection_{now.strftime('%Y-%m-%d')}.json"
    with open(log_file, "w") as f:
        json.dump({
            "timestamp": now.isoformat(),
            "analysis": analysis,
            "insights": insights,
            "next_experiment": next_exp
        }, f, indent=2)
    
    print(f"\n[Reflection] Summary:")
    for ins in insights:
        print(f"  [{ins['priority']}] {ins['action'][:70]}")
    print(f"[Reflection] Full log: {log_file}")

if __name__ == "__main__":
    main()
