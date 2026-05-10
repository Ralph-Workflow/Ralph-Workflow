#!/usr/bin/env python3
"""
Post helpers using curl (avoids Python SSL cert issues).
All posting functions return (success: bool, url: str, error: str)
"""
import subprocess, json, urllib.parse

def post_writeas(title, body, font="sans"):
    """Post to write.as anonymously. Returns (ok, url, error)."""
    data = json.dumps({"title": title, "body": body, "font": font})
    r = subprocess.run([
        "curl", "-s", "-X", "POST",
        "https://write.as/api/posts",
        "-H", "Content-Type: application/json",
        "-d", data
    ], capture_output=True, text=True, timeout=15)
    
    try:
        result = json.loads(r.stdout)
        if result.get("code") == 201:
            post_id = result["data"]["id"]
            return True, f"https://write.as/{post_id}", ""
        return False, "", result.get("error", r.stdout[:100])
    except:
        return False, "", r.stdout[:100]

def get_writeas_postviews(post_id):
    """Get view count for a write.as post."""
    r = subprocess.run([
        "curl", "-s", f"https://write.as/api/posts/{post_id}"
    ], capture_output=True, text=True, timeout=10)
    try:
        data = json.loads(r.stdout)
        if data.get("code") == 200:
            return data["data"].get("views", 0)
    except:
        pass
    return 0

def post_telegraph(title, body, author_name="RW Marketing"):
    """Post to Telegraph. Returns (ok, url, error).
    
    NOTE: Telegraph API currently returns UNKNOWN_METHOD for all write operations.
    This may be a platform issue.
    """
    # Get a fresh account token
    r1 = subprocess.run([
        "curl", "-s",
        f"https://api.telegra.ph/createAccount?short_name=rwbot3&author_name={urllib.parse.quote(author_name)}"
    ], capture_output=True, text=True, timeout=10)
    
    try:
        account = json.loads(r1.stdout)
        if not account.get("ok"):
            return False, "", f"Account: {account.get('error', 'unknown')}"
        token = account["result"]["access_token"]
    except Exception as e:
        return False, "", f"Token error: {e}"
    
    # Build HTML content
    import html
    content_html = "<p>" + body.replace("\n\n", "</p><p>") + "</p>"
    content_html = html.escape(content_html)
    
    # Try createPage ( Telegraph's actual method for creating pages)
    params = urllib.parse.urlencode({
        "access_token": token,
        "title": title,
        "author_name": author_name,
        "content": content_html,
        "return_content": "false"
    })
    
    r2 = subprocess.run([
        "curl", "-s", "-X", "POST",
        "https://api.telegra.ph/createPage",
        "-H", "Content-Type: application/x-www-form-urlencoded",
        "-d", params
    ], capture_output=True, text=True, timeout=15)
    
    try:
        result = json.loads(r2.stdout)
        if result.get("ok"):
            return True, result["result"]["url"], ""
        return False, "", f"createPage: {result.get('error', r2.stdout[:100])}"
    except:
        return False, "", f"Parse error: {r2.stdout[:100]}"

def post_to_both(title, body):
    """Post to both write.as and Telegraph. Returns dict of results."""
    results = {}
    
    ok, url, err = post_writeas(title, body)
    results["writeas"] = {"ok": ok, "url": url, "error": err}
    
    # Telegraph is currently broken, skip it
    results["telegraph"] = {"ok": False, "url": "", "error": "API returns UNKNOWN_METHOD - likely deprecated"}
    
    return results

if __name__ == "__main__":
    # Test
    ok, url, err = post_writeas("Test Post", "This is a test.")
    print(f"write.as: {'✅' if ok else '❌'} {url} {err}")
    
    ok, url, err = post_telegraph("Test", "Test body")
    print(f"Telegraph: {'✅' if ok else '❌'} {url} {err}")
