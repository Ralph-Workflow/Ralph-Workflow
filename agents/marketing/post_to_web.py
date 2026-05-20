#!/usr/bin/env python3
"""
Cross-platform poster using curl.
Posts to Telegraph (primary) and Dev.to (when API key available).
write.as is DEAD (contentisblocked) — do not use.
"""
import subprocess, json, sys, re, os
from datetime import datetime
from pathlib import Path

# Dev.to credentials — load from accounts file
DEVTO_CREDS_FILE = "/home/mistlight/.openclaw/workspace/accounts/devto_creds.json"
CODEBERG_PRIMARY = "https://codeberg.org/RalphWorkflow/Ralph-Workflow"
GITHUB_MIRROR   = "https://github.com/Ralph-Workflow/Ralph-Workflow"

CODEBERG_CTA = (
    f"\n\n---\n"
    f"**Try it on Codeberg (primary repo):** [{CODEBERG_PRIMARY}]({CODEBERG_PRIMARY})\n"
    f"GitHub mirror: [{GITHUB_MIRROR}]({GITHUB_MIRROR})"
)

TELEGRAPH_TOKEN_FILE = Path("/home/mistlight/.openclaw/workspace/agents/marketing/logs/telegraph_token.json")


def get_telegraph_token():
    """Reuse a cached Telegraph access token, or create a new one if missing/stale."""
    if TELEGRAPH_TOKEN_FILE.exists():
        try:
            data = json.loads(TELEGRAPH_TOKEN_FILE.read_text())
            # Token is a short-lived secret; cache for up to 1 hour
            if data.get("access_token"):
                return data["access_token"]
        except Exception:
            pass
    # Create a fresh account
    r1 = subprocess.run([
        "curl", "-s",
        "https://api.telegra.ph/createAccount?short_name=rwbot&author_name=RW+Marketing"
    ], capture_output=True, text=True, timeout=10)
    try:
        account = json.loads(r1.stdout)
        if account.get("ok"):
            token = account["result"]["access_token"]
            TELEGRAPH_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
            TELEGRAPH_TOKEN_FILE.write_text(json.dumps({"access_token": token}))
            return token
    except Exception:
        pass
    return None


def post_devto(title, body, tags=None):
    """Post to Dev.to via API. Requires API key in credentials file."""
    creds_path = os.path.expanduser(DEVTO_CREDS_FILE)
    if not os.path.exists(creds_path):
        return False, "devto_creds.json not found"
    try:
        creds = json.loads(open(creds_path).read())
    except Exception as e:
        return False, f"Failed to read creds: {e}"

    api_key = creds.get("api_key", "")
    if not api_key:
        return False, "No dev.to API key in creds file — skipping"

    article_body = body + f"\n\n---\n*This article is also published on [Ralph Workflow]({CODEBERG_PRIMARY}).*"
    payload = json.dumps({
        "article": {
            "title": title,
            "body": article_body,
            "published": True,
            "tags": tags or ["ai", "automation", "workflow", "open-source"],
        }
    })
    result = subprocess.run([
        "curl", "-s", "-X", "POST",
        "https://dev.to/api/articles",
        "-H", "Content-Type: application/json",
        "-H", f"api-key: {api_key}",
        "-d", payload,
    ], capture_output=True, text=True, timeout=20)
    try:
        data = json.loads(result.stdout)
        if data.get("url"):
            return True, data["url"]
        return False, data.get("error", result.stdout[:100])
    except:
        return False, result.stdout[:100]

def post_telegraph(title, body):
    """Post to Telegraph using curl with correct JSON node format. Reuses cached token."""
    import urllib.parse
    token = get_telegraph_token()
    if not token:
        return False, "Could not obtain Telegraph access token"

    # Convert markdown to Telegraph JSON node format
    paragraphs = []
    for para in body.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        if para.startswith("# "):
            paragraphs.append({"tag": "h3", "children": [para[2:]]})
        elif para.startswith("## "):
            paragraphs.append({"tag": "h4", "children": [para[3:]]})
        elif para.startswith("### "):
            paragraphs.append({"tag": "h5", "children": [para[4:]]})
        elif para.startswith("> "):
            paragraphs.append({"tag": "blockquote", "children": [para[2:]]})
        elif para.startswith("```"):
            paragraphs.append({"tag": "pre", "children": [para[3:].strip()]})
        else:
            parts = re.split(r'(\*{1,2}[^\*]+\*{1,2})', para)
            nodes = []
            for part in parts:
                if re.match(r'\*{1,2}.*\*{1,2}', part):
                    tag = "b" if part.startswith("**") else "i"
                    inner = part[2 if tag == "b" else 1:-2 if tag == "b" else 1]
                    nodes.append({"tag": tag, "children": [inner]})
                elif part:
                    nodes.append(part)
            if len(nodes) > 1:
                paragraphs.append({"tag": "p", "children": nodes})
            else:
                paragraphs.append({"tag": "p", "children": [para]})
    
    content_json = json.dumps(paragraphs)
    params = urllib.parse.urlencode({
        "access_token": token,
        "title": title,
        "author_name": "RW Marketing",
        "content": content_json,
        "return_content": "false"
    })
    
    r2 = subprocess.run([
        "curl", "-s", "-X", "POST",
        "https://api.telegra.ph/createPage",
        "-H", "Content-Type: application/x-www-form-urlencoded",
        "-d", params,
    ], capture_output=True, text=True, timeout=15)
    
    try:
        result = json.loads(r2.stdout)
        if result.get("ok"):
            return True, result["result"]["url"]
        return False, result.get("error", r2.stdout[:100])
    except:
        return False, f"Parse error: {r2.stdout[:100]}"

if __name__ == "__main__":
    import os, re
    drafts_dir = "/home/mistlight/.openclaw/workspace/drafts"
    today = datetime.now().strftime("%Y-%m-%d")

    files = [f for f in os.listdir(drafts_dir) if today in f] if os.path.exists(drafts_dir) else []

    results = []
    for f in files:
        # Skip non-article files (checklists, threads, etc.)
        if any(x in f for x in ["checklist", "thread", "comment", "_comment", "reddit_autopost"]):
            continue

        path = os.path.join(drafts_dir, f)
        with open(path) as fh:
            content = fh.read()

        title_m = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        title = title_m.group(1) if title_m else "Untitled"
        body = re.sub(r"^#+\s+", "", content, flags=re.MULTILINE)

        # Telegraph: append Codeberg CTA (within 5000 char limit)
        telegraph_body = (body + CODEBERG_CTA)[:5000]
        ok, url = post_telegraph(title, telegraph_body)
        results.append(f"{'✅' if ok else '❌'} Telegraph: {url}")

        # Dev.to: requires API key — gracefully skip if not available
        ok, url = post_devto(title, body, tags=["ai", "automation", "workflow", "open-source"])
        results.append(f"{'✅' if ok else '❌'} Dev.to: {url}")

    for r in results:
        print(r)
