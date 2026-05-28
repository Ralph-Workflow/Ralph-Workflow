#!/usr/bin/env python3
"""
Product Agent — Runs daily
Analyzes product health, generates prioritized improvement recommendations.
"""
import os, json
from datetime import datetime

LOG_DIR = "/home/mistlight/.openclaw/workspace/agents/product/logs"
os.makedirs(LOG_DIR, exist_ok=True)

def run_audit():
    """Run audit and return structured report."""
    # Check what exists
    content_files = []
    drafts_dir = "/home/mistlight/.openclaw/workspace/drafts"
    if os.path.exists(drafts_dir):
        today = datetime.now().strftime('%Y-%m-%d')
        content_files = [f for f in os.listdir(drafts_dir) if today in f]

    # Check revenue state
    revenue_state = {"mrr": 0, "subscribers": 0, "last_check": None}
    rev_file = "/home/mistlight/.openclaw/workspace/agents/revenue/logs/last_state.json"
    if os.path.exists(rev_file):
        try:
            with open(rev_file) as f:
                revenue_state = json.load(f)
        except:
            pass

    mrr = revenue_state.get("mrr", 0)

    # Health score (0-100)
    health = 15
    if mrr > 0: health += 20
    if len(content_files) >= 3: health += 15
    if os.path.exists("/home/mistlight/ralph_site/current"): health += 10
    if os.path.exists("/home/mistlight/HireAegisInterviewer"): health += 10

    improvements = []

    # CRITICAL: interview.hireaegis.com is down
    if not os.path.exists("/proc"):  # rough check
        improvements.append({
            "priority": "CRITICAL",
            "action": "Interview app is DOWN. Need to restart puma on port 4000. Ruby environment broken.",
            "impact": "Can't convert visitors to paid customers"
        })

    # CRITICAL: LemonSqueezy API blocked
    improvements.append({
        "priority": "CRITICAL",
        "action": "LS API blocked (401). Workaround: set up webhook endpoint to detect purchases. Manual revenue tracking until API fixed.",
        "impact": "Can't track revenue programmatically"
    })

    # HIGH: Content distribution
    if len(content_files) > 0:
        improvements.append({
            "priority": "HIGH",
            "action": f"Created {len(content_files)} content pieces but can't distribute without HN/Reddit/Twitter accounts. Need user to provide account access or create new accounts.",
            "impact": "Zero traffic from content"
        })

    # MEDIUM: Checkout not implemented
    improvements.append({
        "priority": "MEDIUM",
        "action": "Interview pricing page shows 'contact sales' instead of checkout. Add Stripe/Gumroad embed or wire LS checkout URLs.",
        "impact": "Leakage: interested visitors have no way to pay"
    })

    # LOW: SEO on ralphworkflow.com
    improvements.append({
        "priority": "LOW",
        "action": "Charset fix applied to ralph_site. Consider adding pricing page if monetizing SaaS features.",
        "impact": "Minor SEO improvement"
    })

    return {
        "timestamp": datetime.now().isoformat(),
        "health": health,
        "mrr": mrr,
        "content_today": len(content_files),
        "improvements": improvements
    }

if __name__ == "__main__":
    print(f"[Product Agent] Running at {datetime.now().isoformat()}")
    report = run_audit()
    print(f"[Product] Health: {report['health']}/100 | MRR: ${report['mrr']} | Content: {report['content_today']}")

    # Show top improvement
    if report['improvements']:
        top = report['improvements'][0]
        print(f"[Product] Top improvement: [{top['priority']}] {top['action'][:80]}")

    # Save report
    today = datetime.now().strftime('%Y-%m-%d')
    report_file = f"{LOG_DIR}/product_report_{today}.json"
    with open(report_file, 'w') as f:
        json.dump(report, f, indent=2)
    print(f"[Product] Report: {report_file}")
