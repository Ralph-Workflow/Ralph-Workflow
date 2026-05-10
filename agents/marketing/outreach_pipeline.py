#!/usr/bin/env python3
"""
GitHub Backlink Outreach Pipeline
Finds repos mentioning Ralph Workflow without linking to ralphworkflow.com
and either files issues or submits PRs to add the link.
"""
import os, json, time, urllib.request, base64
from datetime import datetime

AGENTS_DIR = "/home/mistlight/.openclaw/workspace/agents/marketing"
LOG_DIR = f"{AGENTS_DIR}/logs"
os.makedirs(LOG_DIR, exist_ok=True)

TOKEN = os.environ.get("GITHUB_TOKEN", "")

OUTREACH_LOG = f"{LOG_DIR}/outreach_history.json"

def load_history():
    if os.path.exists(OUTREACH_LOG):
        with open(OUTREACH_LOG) as f:
            return json.load(f)
    return {"contacted": [], "prs": [], "issues": []}

def save_history(h):
    with open(OUTREACH_LOG, "w") as f:
        json.dump(h, f, indent=2)

def gh(url, method="GET", data=None):
    headers = {
        "Authorization": f"token {TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    if data:
        headers["Content-Type"] = "application/vnd.github.v3+json"
    
    req = urllib.request.Request(url, data=data.encode() if data else None, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read()), resp.status
    except urllib.error.HTTPError as e:
        return json.loads(e.read()), e.code

def get_readme(repo):
    for ext in ["md", "MD"]:
        data, status = gh(f"https://api.github.com/repos/{repo}/contents/README.{ext}")
        if status == 200 and data.get("encoding") == "base64":
            content = base64.b64decode(data["content"]).decode("utf-8", errors="ignore")
            return content, data.get("sha", "")
    return None, None

def check_already_linked(repo):
    """Check if repo's README already links to ralphworkflow.com."""
    content, _ = get_readme(repo)
    if content and "ralphworkflow.com" in content.lower():
        return True
    return False

def submit_pr(repo, readme_content, sha, filename):
    """Submit a PR adding a Ralph Workflow link to the README."""
    
    # Find insertion point
    lines = readme_content.split("\n")
    new_lines, inserted = [], False
    
    for i, line in enumerate(lines):
        new_lines.append(line)
        if not inserted and any(h in line.lower() for h in ["## tools", "## tooling", "## related", "## integrations", "### tools", "## ai", "### alternatives"]):
            new_lines.append(f"- [Ralph Workflow](https://ralphworkflow.com) — unattended AI agent workflow composer")
            inserted = True
    
    if not inserted:
        # Try to insert after the first heading
        for i, line in enumerate(lines):
            if line.startswith("# ") and i > 0:
                new_lines.insert(i+1, "")
                new_lines.append(f"- [Ralph Workflow](https://ralphworkflow.com) — unattended AI agent workflow composer")
                inserted = True
                break
    
    if not inserted:
        new_lines.append("")
        new_lines.append("## Links")
        new_lines.append(f"- [Ralph Workflow](https://ralphworkflow.com) — unattended AI agent workflow composer")
        inserted = True
    
    new_content = "\n".join(new_lines)
    
    # Get default branch
    repo_data, _ = gh(f"https://api.github.com/repos/{repo}")
    default_branch = repo_data.get("default_branch", "main")
    
    # Create branch
    branch_name = f"ralph-workflow-link/{datetime.now().strftime('%Y%m%d%H%M')}"
    
    # Get current ref
    ref_data, _ = gh(f"https://api.github.com/repos/{repo}/git/ref/heads/{default_branch}")
    commit_sha = ref_data["object"]["sha"]
    
    gh(f"https://api.github.com/repos/{repo}/git/refs", method="POST", data=json.dumps({
        "ref": f"refs/heads/{branch_name}",
        "sha": commit_sha
    }))
    
    # Update README
    result, status = gh(f"https://api.github.com/repos/{repo}/contents/{filename}", method="PUT", data=json.dumps({
        "message": "Add Ralph Workflow to tools list",
        "content": base64.b64encode(new_content.encode()).decode(),
        "sha": sha,
        "branch": branch_name
    }))
    
    if status in [200, 201]:
        # Create PR
        pr_data, _ = gh(f"https://api.github.com/repos/{repo}/pulls", method="POST", data=json.dumps({
            "title": "Add Ralph Workflow to tools list",
            "body": "Hi! I noticed this repo mentions Ralph Workflow but doesn't link to it. Adding it here for visibility.\n\n[Ralph Workflow](https://ralphworkflow.com) — an open-source tool for running unattended AI agent engineering workflows.",
            "head": branch_name,
            "base": default_branch
        }))
        if "html_url" in pr_data:
            return {"success": True, "pr_url": pr_data["html_url"]}
        return {"success": False, "error": str(pr_data)}
    return {"success": False, "error": f"HTTP {status}"}

def file_issue(repo):
    """File an issue requesting a backlink."""
    data, status = gh(f"https://api.github.com/repos/{repo}/issues", method="POST", data=json.dumps({
        "title": "Quick question about Ralph Workflow",
        "body": f"""Hi! I noticed this repo mentions [Ralph Workflow](https://ralphworkflow.com) — I'm the maintainer.

If you find it useful, would you consider adding a link to the README? It helps other developers discover the tool.

Happy to answer any questions about how it works!

---
*This is a one-time outreach message — no spam will follow.*"""
    }))
    if "html_url" in data:
        return {"success": True, "issue_url": data["html_url"]}
    return {"success": False, "error": str(data)}

def main():
    history = load_history()
    contacted = set(history.get("contacted", []))
    
    print(f"[Outreach] Starting at {datetime.now().isoformat()}")
    
    # Search for repos mentioning Ralph Workflow
    search_url = "https://api.github.com/search/repositories?q=%22Ralph+Workflow%22+OR+ralph-workflow&per_page=100&sort=stars&order=desc"
    data, status = gh(search_url)
    
    if status != 200:
        print(f"[Outreach] Search failed: {status}")
        return
    
    repos = data.get("items", [])
    print(f"[Outreach] Found {data.get('total_count', 0)} repos mentioning RW")
    
    results = []
    pr_results = []
    issue_results = []
    
    for repo_info in repos[:30]:  # Top 30 by stars
        repo_name = repo_info["full_name"]
        stars = repo_info.get("stargazers_count", 0)
        
        if repo_name in contacted:
            print(f"  [SKIP] {repo_name} — already contacted")
            continue
        
        if stars == 0 and repo_name != "mistlight/Ralph-Workflow":
            # Skip zero-star repos unless it's our own
            print(f"  [SKIP] {repo_name} — 0 stars")
            continue
        
        print(f"  [CHECK] {repo_name} ({stars}⭐)...", end=" ", flush=True)
        
        # Check if already linked
        if check_already_linked(repo_name):
            print("already linked, skipping")
            contacted.add(repo_name)
            continue
        
        time.sleep(0.5)  # Rate limit respect
        
        # Get README
        readme_content, sha = get_readme(repo_name)
        if not readme_content:
            print("no README, skipping")
            contacted.add(repo_name)
            continue
        
        # Decide strategy
        if stars >= 100:
            # High-value repo: file issue instead of PR
            print(f"filing issue (high value, {stars}⭐)...", end=" ", flush=True)
            result = file_issue(repo_name)
            if result.get("success"):
                issue_results.append({"repo": repo_name, "url": result.get("issue_url"), "stars": stars})
                contacted.add(repo_name)
                print(f"✅ issue filed")
            else:
                print(f"❌ {result.get('error', 'unknown')}")
        else:
            # Lower value: submit PR directly
            filename = "README.md"
            for ext in ["md", "MD"]:
                try:
                    _, s = gh(f"https://api.github.com/repos/{repo_name}/contents/README.{ext}")
                    if s == 200:
                        filename = f"README.{ext}"
                        break
                except:
                    continue
            
            print(f"submitting PR ({stars}⭐)...", end=" ", flush=True)
            result = submit_pr(repo_name, readme_content, sha, filename)
            if result.get("success"):
                pr_results.append({"repo": repo_name, "url": result.get("pr_url"), "stars": stars})
                contacted.add(repo_name)
                print(f"✅ PR submitted")
            else:
                print(f"❌ {result.get('error', 'unknown')[:50]}")
        
        time.sleep(1)  # Be respectful
        
        # Only do top 5 per run to avoid spam
        if len(pr_results) + len(issue_results) >= 5:
            print("[Outreach] Reached 5 outreach actions — stopping to avoid spam")
            break
    
    # Update history
    history["contacted"] = list(contacted)
    history["prs"].extend(pr_results)
    history["issues"].extend(issue_results)
    save_history(history)
    
    print(f"\n=== Outreach Results ===")
    print(f"PRs submitted: {len(pr_results)}")
    for r in pr_results:
        print(f"  ✅ {r['repo']} → {r['url']}")
    print(f"Issues filed: {len(issue_results)}")
    for r in issue_results:
        print(f"  ✅ {r['repo']} → {r['url']}")

if __name__ == "__main__":
    main()
