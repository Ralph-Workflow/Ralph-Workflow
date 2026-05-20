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
    
    Telegraph requires JSON node format for content, not HTML.
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
    
    # Build content as proper Telegraph JSON node format
    paragraphs = []
    for para in body.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        # Handle headings
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
            # Handle inline bold/italic
            import re
            text = para
            nodes = []
            # Split on bold first
            parts = re.split(r'(\*{1,2}[^\*]+\*{1,2})', text)
            for part in parts:
                if re.match(r'\*{1,2}.*\*{1,2}', part):
                    tag = "b" if part.startswith("**") else "i"
                    nodes.append({"tag": tag, "children": [part[2 if tag == "b" else 1:-2 if tag == "b" else 1]]})
                elif part:
                    nodes.append(part)
            if nodes:
                paragraphs.append({"tag": "p", "children": nodes if len(nodes) > 1 else [text]})
            else:
                paragraphs.append({"tag": "p", "children": [para]})
    
    content_json = json.dumps(paragraphs)
    
    params = urllib.parse.urlencode({
        "access_token": token,
        "title": title,
        "author_name": author_name,
        "content": content_json,
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
    
    ok, url, err = post_telegraph(title, body)
    results["telegraph"] = {"ok": ok, "url": url, "error": err}
    
    return results

if __name__ == "__main__":
    # Test
    ok, url, err = post_writeas("Test Post", "This is a test.")
    print(f"write.as: {'✅' if ok else '❌'} {url} {err}")
    
    ok, url, err = post_telegraph("Test", "Test body")
    print(f"Telegraph: {'✅' if ok else '❌'} {url} {err}")
