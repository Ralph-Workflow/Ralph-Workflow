#!/usr/bin/env python3
"""
Apollo Browserless Fix — Fix the 0-contact curator follow-up list.

Uses browserless (headless Chrome) to:
1. Log into Apollo
2. Navigate to the lists page
3. Find the "Ralph Workflow — curator follow-up 2026-05-22" list
4. Upload the CSV to import contacts

Run with: python3 agents/marketing/apollo_browserless_fix.py
"""
import json
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path("/home/mistlight/.openclaw/workspace")
TOOLS_PATH = ROOT / "TOOLS.md"
BROWSERLESS_TOKEN = "2UWbL11RUlO4quE8238557491eab7d21b44da3db127e3d5e4"
BROWSERLESS_BASE = "https://chrome.browserless.io"
CSV_PATH = ROOT / "tmp_apollo_curators_simple.csv"
LOG_PATH = ROOT / "agents/marketing/logs/marketing_2026-05-22_apollo_browserless_fix.json"

# Read credentials
def get_credentials():
    text = TOOLS_PATH.read_text(encoding="utf-8")
    import re
    match = re.search(r"^### Apollo\.io\n(?P<section>.*?)(?=^### |\Z)", text, re.M | re.S)
    if not match:
        raise RuntimeError("Apollo.io section not found in TOOLS.md")
    section = match.group("section")
    username_match = re.search(r"Login username:\*\*\s*`([^`]+)`", section)
    password_match = re.search(r"Password:\*\*\s*`([^`]+)`", section)
    if not username_match or not password_match:
        raise RuntimeError("Apollo.io credentials are incomplete in TOOLS.md")
    return username_match.group(1), password_match.group(1)


def browserless_cdp(script: str, params: dict = None) -> dict:
    """Send CDP command to browserless."""
    payload = {
        "code": script,
        "maxDuration": 30,
    }
    if params:
        payload["context"] = params

    req = urllib.request.Request(
        f"{BROWSERLESS_BASE}/function",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {BROWSERLESS_TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode()[:200]}"}
    except Exception as e:
        return {"error": str(e)}


def main() -> int:
    username, password = get_credentials()

    print("[Apollo Browserless Fix] Starting...")
    print(f"[Apollo Browserless Fix] CSV exists: {CSV_PATH.exists()}")

    # First, navigate to Apollo and take a screenshot to see the current state
    result = browserless_cdp("""
    const { chromium } = require('playwright');
    (async () => {
      const browser = await chromium.launch();
      const page = await browser.newPage();
      await page.goto('https://app.apollo.io/#/login', { waitUntil: 'networkidle', timeout: 20000 });
      const title = await page.title();
      const url = page.url();
      await page.screenshot({ path: '/tmp/apollo_login.png' });
      await browser.close();
      return { title, url };
    })();
    """)
    print(f"[Apollo Browserless Fix] Login page: {result}")

    if "error" in result:
        print(f"[Apollo Browserless Fix] Error: {result['error']}")
        return 1

    # Log in
    login_result = browserless_cdp(f"""
    const {{ chromium }} = require('playwright');
    (async () => {{
      const browser = await chromium.launch();
      const page = await browser.newPage();
      await page.goto('https://app.apollo.io/#/login', {{ waitUntil: 'networkidle', timeout: 20000 }});
      await page.fill('input[type="email"]', '{username}');
      await page.fill('input[type="password"]', '{password}');
      await page.click('button[type="submit"]');
      await page.waitForURL('**/home', {{ timeout: 15000 }}).catch(() => {{}});
      await page.waitForTimeout(3000);
      const url = page.url();
      const logged_in = !url.includes('/login');
      await page.screenshot({{ path: '/tmp/apollo_after_login.png' }});
      await browser.close();
      return {{ url, logged_in }};
    }})();
    """)
    print(f"[Apollo Browserless Fix] Login result: {login_result}")

    # Navigate to lists
    lists_result = browserless_cdp("""
    const { chromium } = require('playwright');
    (async () => {
      const browser = await chromium.launch();
      const page = await browser.newPage();
      await page.goto('https://app.apollo.io/#/lists?sortByField=updated_at&sortAscending=false', { waitUntil: 'networkidle', timeout: 20000 });
      await page.waitForTimeout(3000);
      const url = page.url();
      // Get list names and counts
      const lists = await page.evaluate(() => {
        const items = document.querySelectorAll('[data-testid="list-item"]');
        return Array.from(items).slice(0, 10).map(el => ({
          name: el.querySelector('.name')?.textContent?.trim(),
          count: el.querySelector('.count')?.textContent?.trim()
        }));
      });
      await page.screenshot({ path: '/tmp/apollo_lists.png' });
      await browser.close();
      return { url, lists };
    })();
    """)
    print(f"[Apollo Browserless Fix] Lists result: {lists_result}")

    log_data = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "login": login_result,
        "lists": lists_result,
        "csv_path": str(CSV_PATH),
    }
    LOG_PATH.write_text(json.dumps(log_data, indent=2))
    print(f"[Apollo Browserless Fix] Log: {LOG_PATH}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
