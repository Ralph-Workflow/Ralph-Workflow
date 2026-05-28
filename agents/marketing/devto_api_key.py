#!/usr/bin/env python3
"""Generate a Dev.to API key using local Playwright with curl-based login first.

Strategy: Use curl to authenticate (bypassing Apple OAuth redirect), then pass cookies to Playwright.
"""

import json
import subprocess
import sys
import re
from pathlib import Path

ROOT = Path('/home/mistlight/.openclaw/workspace')
CREDS_PATH = ROOT / 'accounts' / 'devto_creds.json'
COOKIES_PATH = Path('/tmp/devto_cookies.txt')
OUTPUT_PATH = Path('/tmp/devto_key_result.json')

JS_LOGIN_SCRIPT = r"""
const { chromium } = require('playwright');
const fs = require('fs');

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  const context = await browser.newContext();
  const page = await context.newPage();
  page.setDefaultTimeout(20000);

  const result = { ok: false, api_key: null, error: null };

  try {
    console.log('[Step 1] Setting cookies from curl session...');
    
    const cookiesJson = JSON.parse(fs.readFileSync(COOKIES_FILE, 'utf-8'));
    await context.addCookies(cookiesJson);
    
    console.log('[Step 2] Navigating to Dev.to home to verify login...');
    await page.goto('https://dev.to/', { waitUntil: 'networkidle' });
    await page.waitForTimeout(3000);
    
    // Check if logged in
    const pageText = await page.evaluate(() => document.body.innerText.substring(0, 500));
    const url = page.url();
    console.log('  URL: ' + url);
    console.log('  Page snippet: ' + pageText.substring(0, 200));
    
    // Check for sign-in button (means we're NOT logged in)
    if (pageText.includes('Sign In') || pageText.includes('Create account')) {
      // Wait a bit more and try again
      await page.waitForTimeout(3000);
      const newText = await page.evaluate(() => document.body.innerText.substring(0, 500));
      if (newText.includes('Sign In') || newText.includes('Create account')) {
        // Not logged in — try the login flow through Playwright directly
        console.log('[Step 2b] Not logged in via cookies, attempting direct Playwright login...');
        
        await page.goto('https://dev.to/enter', { waitUntil: 'networkidle' });
        await page.waitForTimeout(3000);
        
        // Check if there's an email login option (not just Apple)
        const emailLink = await page.$('a[href*="enter?state=email"], a[href*="email-login"]');
        if (emailLink) {
          await emailLink.click();
          await page.waitForTimeout(2000);
        }
        
        const emailSel = 'input[name="user[email]"]';
        const passSel = 'input[name="user[password]"]';
        
        try {
          await page.waitForSelector(emailSel, { timeout: 5000 });
          await page.fill(emailSel, EMAIL);
          await page.fill(passSel, PASS);
          
          // Find and click Continue
          const buttons = await page.$$('button[type="submit"], input[type="submit"]');
          for (const btn of buttons) {
            const text = await btn.textContent();
            if (text && text.toLowerCase().includes('continue')) {
              await btn.click();
              console.log('  Clicked Continue');
              await page.waitForTimeout(5000);
              break;
            }
          }
        } catch (e) {
          console.log('  No email login fields found: ' + e.message);
        }
      }
    }
    
    // Now check if we're actually logged in
    const finalUrl = page.url();
    const finalCookies = await context.cookies();
    const isLoggedIn = finalCookies.some(c => c.name === 'remember_user_token');
    console.log('  Logged in: ' + isLoggedIn);
    console.log('  Final URL: ' + finalUrl);
    
    if (!isLoggedIn && !finalUrl.includes('/dashboard') && !finalUrl.includes('/t/')) {
      result.error = 'Login failed — still not authenticated';
      require('fs').writeFileSync(OUTPUT, JSON.stringify(result));
      await browser.close();
      process.exit(1);
    }
    
    console.log('[Step 3] Navigating to extensions page...');
    await page.goto('https://dev.to/settings/extensions', { waitUntil: 'networkidle' });
    await page.waitForTimeout(4000);
    
    const extPageText = await page.evaluate(() => document.body.innerText);
    
    if (extPageText.includes('API key') || extPageText.includes('Generate API Key') || extPageText.includes('generated')) {
      console.log('  Found API key section!');
      
      // Try multiple extraction strategies
      
      // Strategy 1: Look for code/pre elements with hex text
      const codeElements = await page.$$('pre, code, input[readonly]');
      for (const el of codeElements) {
        const text = await el.evaluate(node => node.value || node.textContent || '');
        const match = text.match(/[a-f0-9]{32,64}/i);
        if (match) {
          result.api_key = match[0];
          result.ok = true;
          console.log('  Key extracted from element!');
          break;
        }
      }
      
      // Strategy 2: Full text scan
      if (!result.ok) {
        const keyMatch = extPageText.match(/[a-f0-9]{32,64}/i);
        if (keyMatch) {
          result.api_key = keyMatch[0];
          result.ok = true;
          console.log('  Key extracted from page text!');
        }
      }
      
      if (!result.ok) {
        // Strategy 3: Try to generate a new key
        const genBtn = await page.$('text="Generate API Key"');
        if (genBtn) {
          const descInput = await page.$('input[name*="description"]');
          if (descInput) {
            await descInput.fill('RalphWorkflow Automation');
          }
          await genBtn.click();
          await page.waitForTimeout(4000);
          
          // Re-scan
          const newText = await page.evaluate(() => document.body.innerText);
          const newMatch = newText.match(/[a-f0-9]{32,64}/i);
          if (newMatch) {
            result.api_key = newMatch[0];
            result.ok = true;
            console.log('  Key generated and extracted!');
          }
        }
      }
      
      if (!result.ok) {
        result.error = 'Could not extract key from extensions page';
        console.log('  Page text excerpt: ' + extPageText.substring(extPageText.indexOf('API'), extPageText.indexOf('API') + 300));
      }
    } else {
      result.error = 'Not on extensions page or API key section not visible';
      console.log('  Page text snippet: ' + extPageText.substring(0, 400));
    }
    
  } catch (e) {
    result.error = 'Exception: ' + e.message;
    console.error('ERROR: ' + e.message);
  } finally {
    require('fs').writeFileSync(OUTPUT, JSON.stringify(result));
    await browser.close();
  }
})();
"""


def get_cookies_netscape(cookies_path):
    """Parse Netscape cookie file to JSON format Playwright can use."""
    cookies_json = []
    with open(cookies_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split('\t')
            if len(parts) < 7:
                continue
            domain, domain_flag, path, secure, expires, name, value = parts[:7]
            if expires == '0':
                expires_sec = 2147483647  # session cookie
            else:
                expires_sec = int(expires)
            cookies_json.append({
                'name': name,
                'value': value,
                'domain': domain,
                'path': path,
                'expires': expires_sec,
                'httpOnly': domain_flag == 'TRUE',
                'secure': secure == 'TRUE',
                'sameSite': 'Lax',
            })
    return cookies_json


def main() -> int:
    dry_run = '--dry-run' in sys.argv

    creds = json.loads(CREDS_PATH.read_text(encoding='utf-8'))
    email = creds['email']
    password = creds['password']

    existing = creds.get('api_key', '')
    if existing and '--refresh' not in sys.argv:
        print(f'API key already exists: {existing[:8]}...{existing[-4:]}')
        return 0

    if dry_run:
        print('[DRY RUN] Would generate Dev.to API key via Playwright with curl pre-auth')
        return 0

    # Step 1: Login via curl to get cookies
    print('Step 1: Logging into Dev.to via curl...')
    
    # First GET to get CSRF token
    get_result = subprocess.run([
        'curl', '-s', '-c', str(COOKIES_PATH),
        '-H', 'User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'https://dev.to/enter',
    ], capture_output=True, text=True, timeout=15)
    
    csrf_match = re.search(r'name="authenticity_token"[^>]*value="([^"]+)"', get_result.stdout)
    csrf = csrf_match.group(1) if csrf_match else ''
    print(f'  CSRF: {bool(csrf)}, length={len(csrf)}')
    
    if not csrf:
        print('  ERROR: Could not extract CSRF token')
        return 1
    
    # POST login
    import urllib.parse
    post_data = urllib.parse.urlencode({
        'utf8': '✓',
        'authenticity_token': csrf,
        'user[email]': email,
        'user[password]': password,
        'user[remember_me]': '1',
        'commit': 'Continue',
    })
    
    login_result = subprocess.run([
        'curl', '-s', '-L', '-c', str(COOKIES_PATH), '-b', str(COOKIES_PATH),
        '-X', 'POST',
        '-d', post_data,
        'https://dev.to/enter',
        '-H', 'User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        '-H', 'Content-Type: application/x-www-form-urlencoded',
    ], capture_output=True, text=True, timeout=20)
    
    print(f'  Login response length: {len(login_result.stdout)} bytes')
    
    # Check if login succeeded by looking at cookies
    cookies_text = COOKIES_PATH.read_text()
    has_forem = '_Devto_Forem_Session' in cookies_text
    has_user = 'remember_user_token' in cookies_text or '_Devto_Remember_user_token' in cookies_text
    print(f'  Session cookie: {has_forem}')
    print(f'  User cookie: {has_user}')
    
    # Check if we're on dashboard page
    now_on_dashboard = '/dashboard' in login_result.stdout or 'Create Post' in login_result.stdout[:2000]
    print(f'  On dashboard: {now_on_dashboard}')
    
    if not has_forem:
        print('  Login may have failed — no session cookie')
        # Check if we hit Apple OAuth
        if 'appleid.apple.com' in login_result.stdout:
            print('  Detected Apple OAuth redirect — email login blocked')
        
        # Try extracting CSRF from the response for another attempt
        new_csrf_match = re.search(r'name="authenticity_token"[^>]*value="([^"]+)"', login_result.stdout)
        if new_csrf_match:
            new_csrf = new_csrf_match.group(1)
            # Check if we got an error message
            if 'Invalid' in login_result.stdout or 'error' in login_result.stdout.lower():
                print('  Login error detected in response')
                print(f'  Response snippet: {login_result.stdout[:500]}')
    
    # Step 2: Parse cookies and run Playwright
    print('\nStep 2: Converting cookies and running Playwright...')
    
    cookies_json_path = Path('/tmp/devto_cookies.json')
    try:
        cookies_list = get_cookies_netscape(COOKIES_PATH)
        cookies_json_path.write_text(json.dumps(cookies_list))
        print(f'  Converted {len(cookies_list)} cookies')
    except Exception as e:
        print(f'  Cookie conversion error: {e}')
        cookies_list = []
        cookies_json_path.write_text('[]')
    
    script = JS_LOGIN_SCRIPT.replace('COOKIES_FILE', json.dumps(str(cookies_json_path)))
    script = script.replace('EMAIL', json.dumps(email))
    script = script.replace('PASS', json.dumps(password))
    script = script.replace('OUTPUT', json.dumps(str(OUTPUT_PATH)))
    
    script_path = Path('/tmp/devto_key_gen.js')
    script_path.write_text(script)
    
    print('  Running Playwright...')
    result = subprocess.run(
        ['node', str(script_path)],
        capture_output=True, text=True, timeout=60,
    )
    print(result.stdout)
    if result.stderr:
        print(f'  Stderr: {result.stderr[:500]}')
    
    if OUTPUT_PATH.exists():
        result_data = json.loads(OUTPUT_PATH.read_text())
        if result_data.get('ok') and result_data.get('api_key'):
            creds['api_key'] = result_data['api_key']
            CREDS_PATH.write_text(json.dumps(creds, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
            print(f'\nSUCCESS: Saved API key ({result_data["api_key"][:8]}...{result_data["api_key"][-4:]}) to {CREDS_PATH}')
            return 0
        else:
            print(f'\nFAILED: {result_data.get("error", "Unknown")}')
            return 1
    
    print('\nFAILED: No result file produced')
    return 1


if __name__ == '__main__':
    sys.exit(main())
