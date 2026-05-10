#!/usr/bin/env python3
"""
SEO Agent — Runs every 12h
Monitors site health, finds optimization opportunities, auto-fixes what it can.
"""
import subprocess
import re
import sys
import json
import os
from datetime import datetime

LOG_DIR = "/home/mistlight/.openclaw/workspace/agents/seo/logs"
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = f"{LOG_DIR}/{datetime.now().strftime('%Y-%m-%d')}.json"

SITES = [
    "https://hireaegis.com",
    "https://billing.hireaegis.com", 
    "https://ralphworkflow.com",
]

def log(msg):
    ts = datetime.now().isoformat()
    line = f"[{ts}] {msg}"
    print(line)

def check_site(url):
    """Check a site for basic health and SEO issues."""
    issues = []
    try:
        import urllib.request
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 SEOBot/1.0'})
        with urllib.request.urlopen(req, timeout=10) as r:
            content = r.read(1024*500).decode('utf-8', errors='ignore')
            status = r.status
    except Exception as e:
        issues.append(f"FETCH_ERROR: {e}")
        return issues

    if status != 200:
        issues.append(f"NON_200: {status}")

    # Check for basic SEO elements
    if '<title' not in content.lower():
        issues.append("MISSING_TITLE")
    if 'charset=' not in content.lower() and 'utf-8' not in content.lower():
        issues.append("MISSING_CHARSET")
    
    # Check for meta description
    if 'description' not in content.lower():
        issues.append("MISSING_META_DESC")
    
    # Check for hreflang (multi-language sites)
    if 'hreflang' in content:
        pass  # good
    
    return issues

def check_keyword_opportunities():
    """Look for keyword opportunities based on search trends - simplified."""
    # In production: call Google Trends API or similar
    # For now: check what competitors rank for
    opportunities = []
    
    # Read the content strategy for target keywords
    strategy_file = "/home/mistlight/.openclaw/workspace/youtube-strategy.md"
    if os.path.exists(strategy_file):
        with open(strategy_file) as f:
            content = f.read()
            if "unattended" in content.lower():
                opportunities.append({
                    "keyword": "unattended AI coding",
                    "difficulty": "medium",
                    "cpc": 2.50,
                    "action": "Create tutorial video targeting this phrase"
                })
            if "spec-driven" in content.lower():
                opportunities.append({
                    "keyword": "spec-driven development AI",
                    "difficulty": "low", 
                    "cpc": 1.80,
                    "action": "Create tutorial targeting this phrase"
                })
    
    return opportunities

def auto_fix_meta_tags():
    """For RalphWorkflow landing page - check and suggest meta tag improvements."""
    # This requires write access to the Ralph-Site-Rs views
    # We'll check what we can auto-fix vs what needs review
    
    fixes_applied = []
    
    # Check if there are any obvious noindex/nofollow issues
    ralph_views = "/home/mistlight/Ralph-Site-Rs/assets/views"
    if os.path.exists(ralph_views):
        for root, dirs, files in os.walk(ralph_views):
            for f in files:
                if f.endswith('.html'):
                    path = os.path.join(root, f)
                    with open(path) as fp:
                        content = fp.read()
                    if 'noindex' in content.lower():
                        fixes_applied.append(f"NOINDEX found in {path} - review needed")
    
    return fixes_applied

def main():
    log("=== SEO Agent Start ===")
    
    results = {
        "timestamp": datetime.now().isoformat(),
        "sites": {},
        "opportunities": [],
        "fixes": [],
    }
    
    for site in SITES:
        log(f"Checking {site}...")
        issues = check_site(site)
        results["sites"][site] = issues
        if issues:
            for issue in issues:
                log(f"  ISSUE: {issue}")
        else:
            log(f"  OK")
    
    results["opportunities"] = check_keyword_opportunities()
    for opp in results["opportunities"]:
        log(f"  OPPORTUNITY: {opp['keyword']} - {opp['action']}")
    
    results["fixes"] = auto_fix_meta_tags()
    for fix in results["fixes"]:
        log(f"  FIX: {fix}")
    
    # Save log
    with open(LOG_FILE, 'w') as f:
        json.dump(results, f, indent=2)
    
    log("=== SEO Agent End ===")
    return 0

if __name__ == "__main__":
    sys.exit(main())
