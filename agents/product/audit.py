#!/usr/bin/env python3
"""
Daily Audit — Runs every 24h
Produces a monetisation health report with specific improvement actions.
The loop: MEASURE → ANALYSE → ACT → REPEAT
"""
import os, json, subprocess
from datetime import datetime
from pathlib import Path

LOG_DIR = Path("/home/mistlight/.openclaw/workspace/agents/product/logs")
os.makedirs(LOG_DIR, exist_ok=True)

# Paths
DRAFTS_DIR = Path("/home/mistlight/.openclaw/workspace/drafts")
METRICS_DIR = Path("/home/mistlight/.openclaw/workspace/metrics")
AGENTS_DIR = Path("/home/mistlight/.openclaw/workspace/agents")

# What we measure
METRICS = [
    ("revenue", "agents/revenue/logs", "mrr"),
    ("content", "agents/content/logs", "topics"),
    ("community", "agents/community/logs", "files_found"),
]

def get_revenue():
    """Get revenue data from revenue agent logs."""
    rev_dir = AGENTS_DIR / "revenue" / "logs"
    if not rev_dir.exists(): return {"mrr": 0, "total_subs": 0, "new_this_week": 0, "error": "no logs"}
    
    logs = sorted(rev_dir.glob("*.json"))
    if not logs: return {"mrr": 0, "total_subs": 0, "new_this_week": 0, "error": "no logs"}
    
    with open(logs[-1]) as f:
        return json.load(f)

def get_content_output():
    """Count content pieces generated."""
    if not DRAFTS_DIR.exists(): return 0
    today = datetime.now().strftime("%Y-%m-%d")
    count = len([f for f in DRAFTS_DIR.iterdir() if today in f.name and not f.name.endswith('.md')])
    return count

def get_community_output():
    """Check what community actions were taken."""
    comm_dir = AGENTS_DIR / "community" / "logs"
    if not comm_dir.exists(): return {"actions": 0, "platforms": []}
    
    today = datetime.now().strftime("%Y-%m-%d")
    log_file = comm_dir / f"{today}.json"
    if not log_file.exists(): return {"actions": 0, "platforms": []}
    
    with open(log_file) as f:
        data = json.load(f)
    return {"actions": len(data.get("actions", [])), "platforms": [a["platform"] for a in data.get("actions", [])]}

def get_seo_output():
    """Check SEO findings."""
    seo_dir = AGENTS_DIR / "seo" / "logs"
    if not seo_dir.exists(): return {"issues": [], "opportunities": []}
    
    today = datetime.now().strftime("%Y-%m-%d")
    log_file = seo_dir / f"{today}.json"
    if not log_file.exists(): return {"issues": [], "opportunities": []}
    
    with open(log_file) as f:
        data = json.load(f)
    
    all_issues = []
    for site, issues in data.get("sites", {}).items():
        for issue in issues:
            all_issues.append(f"{site}: {issue}")
    
    return {
        "issues": all_issues,
        "opportunities": [o["keyword"] for o in data.get("opportunities", [])],
    }

def get_conversion_metrics():
    """
    Estimate conversion from available signals.
    Real conversion tracking needs analytics - this is directional.
    """
    # Check if there are any signup/trial signals in the app
    # For now: check LemonSqueezy for any checkout activity
    rev = get_revenue()
    
    if rev.get("error"):
        return {
            "checkouts_initiated": "unknown",
            "checkout_completion_rate": "unknown",
            "bottleneck": "API access blocked - fix revenue agent first"
        }
    
    # We don't have funnel data without analytics
    # Use what we have
    return {
        "mrr": rev.get("mrr", 0),
        "subs": rev.get("total_subs", 0),
        "checkouts_initiated": rev.get("total_subs", 0),  # rough proxy
        "checkout_completion_rate": "unknown - needs analytics",
        "bottleneck": "no funnel analytics configured",
    }

def generate_audit_report():
    """The main audit: assess, score, and recommend."""
    rev = get_revenue()
    content_count = get_content_output()
    comm = get_community_output()
    seo = get_seo_output()
    conv = get_conversion_metrics()
    
    # Calculate health score (0-100)
    score = 0
    max_score = 100
    
    # Revenue (40 points)
    if rev.get("error"):
        score += 0
    elif rev.get("mrr", 0) > 0:
        score += 40
    else:
        score += 10  # some credit for running
    
    # Content (25 points)
    if content_count >= 3:
        score += 25
    elif content_count >= 1:
        score += 15
    else:
        score += 0
    
    # Community (20 points)
    if comm["actions"] >= 3:
        score += 20
    elif comm["actions"] >= 1:
        score += 10
    else:
        score += 5
    
    # SEO (15 points)
    if len(seo["issues"]) == 0 and len(seo["opportunities"]) > 0:
        score += 15
    elif len(seo["issues"]) <= 2:
        score += 10
    else:
        score += 5
    
    # Determine overall status
    if score >= 80:
        status = "HEALTHY"
    elif score >= 50:
        status = "NEEDS_ATTENTION"
    else:
        status = "CRITICAL"
    
    # Generate specific actions
    actions = []
    
    if rev.get("error") == "no logs" or (rev.get("mrr", 0) == 0 and not rev.get("error")):
        actions.append({
            "id": "REVENUE-001",
            "priority": "CRITICAL",
            "area": "revenue",
            "action": "Fix LemonSqueezy API access - can't track or optimize without revenue data",
            "impact": "Without this, all other efforts are unmeasurable",
        })
    
    if rev.get("mrr", 0) == 0 and not rev.get("error"):
        actions.append({
            "id": "REVENUE-002", 
            "priority": "CRITICAL",
            "area": "revenue",
            "action": "Get first dollar: post consulting offer to Twitter, HN, and Indie Hackers",
            "impact": "First revenue validates the business exists",
        })
    
    if content_count == 0:
        actions.append({
            "id": "CONTENT-001",
            "priority": "HIGH",
            "area": "content",
            "action": "Content engine failed to generate content - check logs",
            "impact": "No content = no traffic = no revenue",
        })
    
    if content_count >= 1 and rev.get("mrr", 0) == 0:
        actions.append({
            "id": "CONTENT-002",
            "priority": "HIGH",
            "area": "content",
            "action": "Content being created but not converting - review CTA placement and product-market fit",
            "impact": "Traffic exists but isn't becoming revenue",
        })
    
    if seo["issues"]:
        for issue in seo["issues"][:3]:
            actions.append({
                "id": "SEO-001",
                "priority": "MEDIUM",
                "area": "seo",
                "action": f"Fix SEO issue: {issue}",
                "impact": "Technical SEO issues hurt organic discovery",
            })
    
    if conv.get("bottleneck"):
        actions.append({
            "id": "CONV-001",
            "priority": "HIGH",
            "area": "conversion",
            "action": f"Address bottleneck: {conv['bottleneck']}",
            "impact": "Can't optimize funnel without measurement",
        })
    
    # Generate strategic recommendations
    strategic = []
    
    if rev.get("mrr", 0) == 0 and content_count > 0:
        strategic.append({
            "id": "STRAT-001",
            "priority": "HIGH",
            "area": "strategy",
            "action": "Pivot content strategy: focus 80% on topics that lead to consulting, not product CTAs",
            "why": "Product-market fit not proven - consulting revenue proves demand",
        })
    
    if comm["actions"] == 0:
        strategic.append({
            "id": "STRAT-002",
            "priority": "HIGH",
            "area": "strategy",
            "action": "Start distributing content manually - community agent checklists are ready",
            "why": "Content without distribution is noise",
        })
    
    if len(seo["opportunities"]) > 0:
        strategic.append({
            "id": "STRAT-003",
            "priority": "MEDIUM",
            "area": "strategy",
            "action": f"Target these keywords: {', '.join(seo['opportunities'][:3])}",
            "why": "High-intent searches = buyers, not browsers",
        })
    
    return {
        "timestamp": datetime.now().isoformat(),
        "health_score": score,
        "max_score": max_score,
        "status": status,
        "metrics": {
            "mrr": rev.get("mrr", 0),
            "subscribers": rev.get("total_subs", 0),
            "new_this_week": rev.get("new_this_week", 0),
            "content_generated_today": content_count,
            "community_actions": comm["actions"],
            "platforms_reached": comm["platforms"],
            "seo_issues": seo["issues"],
            "keyword_opportunities": seo["opportunities"],
            "conversion_bottleneck": conv.get("bottleneck"),
        },
        "immediate_actions": actions[:5],
        "strategic_recommendations": strategic[:3],
        "revenue_error": rev.get("error"),
    }

def main():
    print(f"[Daily Audit] Running at {datetime.now().isoformat()}")
    
    report = generate_audit_report()
    
    # Save report
    today = datetime.now().strftime("%Y-%m-%d")
    report_file = LOG_DIR / f"daily_audit_{today}.json"
    with open(report_file, 'w') as f:
        json.dump(report, f, indent=2)
    
    # Save markdown version
    md_file = LOG_DIR / f"daily_audit_{today}.md"
    with open(md_file, 'w') as f:
        f.write(f"# Daily Audit — {today}\n\n")
        f.write(f"**Health Score:** {report['health_score']}/{report['max_score']} — {report['status']}\n\n")
        f.write(f"## Metrics\n")
        for k, v in report['metrics'].items():
            f.write(f"- **{k}:** {v}\n")
        
        f.write(f"\n## Immediate Actions\n")
        for a in report['immediate_actions']:
            f.write(f"\n### [{a['priority']}] {a['id']}: {a['action']}\n")
            f.write(f"*Impact:* {a['impact']}\n")
        
        f.write(f"\n## Strategic Recommendations\n")
        for s in report['strategic_recommendations']:
            f.write(f"\n### [{s['priority']}] {s['id']}: {s['action']}\n")
            f.write(f"*Why:* {s['why']}\n")
    
    # Print summary
    print(f"[Audit] Health: {report['health_score']}/100 — {report['status']}")
    print(f"[Audit] MRR: ${report['metrics']['mrr']} | Content: {report['metrics']['content_generated_today']} pieces | Community: {report['metrics']['community_actions']} actions")
    print(f"[Audit] Immediate actions: {len(report['immediate_actions'])}")
    print(f"[Audit] Report saved: {report_file}")
    
    # Check if there's an actionable improvement to execute
    critical = [a for a in report['immediate_actions'] if a['priority'] == 'CRITICAL']
    if critical:
        print(f"[Audit] CRITICAL: {critical[0]['action']}")
    
    return 0

if __name__ == "__main__":
    import sys
    sys.exit(main())
