#!/usr/bin/env python3
"""
Simple cross-platform poster using curl (works with SSL properly).
"""
import subprocess, json, sys
from datetime import datetime

def post_writeas(title, body):
    """Post to write.as using curl."""
    data = json.dumps({"title": title, "body": body, "font": "sans"})
    
    result = subprocess.run([
        "curl", "-s", "-X", "POST",
        "https://write.as/api/posts",
        "-H", "Content-Type: application/json",
        "-d", data
    ], capture_output=True, text=True, timeout=15)
    
    try:
        result_data = json.loads(result.stdout)
        if result_data.get("code") == 201:
            return True, f"https://write.as/{result_data['data']['id']}"
        return False, result_data.get("error", result.stdout[:100])
    except:
        return False, result.stdout[:100]

def post_telegraph(title, body):
    """Post to Telegraph using curl."""
    # Get account first
    r1 = subprocess.run([
        "curl", "-s", "https://api.telegra.ph/createAccount?short_name=rwbot&author_name=RW+Marketing"
    ], capture_output=True, text=True, timeout=10)
    
    try:
        account = json.loads(r1.stdout)
        if not account.get("ok"):
            return False, "Account creation failed"
        token = account["result"]["access_token"]
    except:
        return False, f"Token error: {r1.stdout[:50]}"
    
    # Create article - Telegraph expects form-encoded data
    import urllib.parse
    content = "<p>" + body.replace("\n\n", "</p><p>") + "</p>"
    params = urllib.parse.urlencode({
        "access_token": token,
        "title": title,
        "author_name": "RW Marketing",
        "content": content,
        "return_content": "false"
    })
    
    r2 = subprocess.run([
        "curl", "-s", "-X", "POST",
        f"https://api.telegra.ph/createArticle?{params}"
    ], capture_output=True, text=True, timeout=15)
    
    try:
        article = json.loads(r2.stdout)
        if article.get("ok"):
            return True, article["result"]["url"]
        return False, article.get("error", r2.stdout[:100])
    except:
        return False, f"Article error: {r2.stdout[:100]}"

if __name__ == "__main__":
    import sys, os, re
    drafts_dir = "/home/mistlight/.openclaw/workspace/drafts"
    today = datetime.now().strftime("%Y-%m-%d")
    
    files = [f for f in os.listdir(drafts_dir) if today in f] if os.path.exists(drafts_dir) else []
    
    results = []
    for f in files:
        path = os.path.join(drafts_dir, f)
        with open(path) as fh:
            content = fh.read()
        
        title_m = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        title = title_m.group(1) if title_m else "Untitled"
        body = re.sub(r"^#+\s+", "", content, flags=re.MULTILINE)
        
        ok, url = post_writeas(title, body)
        results.append(f"{'✅' if ok else '❌'} write.as: {url}")
        
        ok, url = post_telegraph(title, body[:5000])
        results.append(f"{'✅' if ok else '❌'} Telegraph: {url}")
    
    for r in results:
        print(r)
