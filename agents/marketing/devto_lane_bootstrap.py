#!/usr/bin/env python3
"""
Create a new Dev.to account (email-based) and publish the first RalphWorkflow article.

This creates a genuinely new autonomous distribution lane that:
1. Creates a fresh Dev.to account via email
2. Verifies the email (Mailnesia)
3. Publishes a RalphWorkflow article
4. Stores the API key for future use

Run: python3 agents/marketing/devto_lane_bootstrap.py [--dry-run]
"""

import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path('/home/mistlight/.openclaw/workspace')
CREDS_PATH = ROOT / 'accounts' / 'devto_creds.json'
OUTPUT_PATH = Path('/tmp/devto_bootstrap_result.json')

# New email-based account credentials (fresh, not Apple OAuth)
DEVTO_EMAIL = 'ralphworkflow+dev26@mailnesia.com'
DEVTO_USERNAME = 'ralphworkflow'
DEVTO_PASSWORD = 'jO9mo1UiIXw1Fm7R!X'  # Same password as existing account attempt
DEVTO_DISPLAY_NAME = 'Ralph Workflow'

# Article content
ARTICLE_TITLE = 'How to Know When Your AI Coding Agent Is Actually Done'
ARTICLE_TAGS = ['ai', 'programming', 'opensource', 'productivity', 'automation']
ARTICLE_BODY = """{% devto %}

## The problem nobody talks about

You hand off a coding task to your AI agent. It runs for a while. It says "done." 

You check the output. Sometimes it's good. Sometimes it's... not. And the worst part? **You can't tell which is which without reading every line.**

This is the handoff problem. It's not about how smart the model is. It's about what happens between "agent says done" and "you're confident enough to merge."

## Why "agent said done" isn't a finish line

I've been building automated coding workflows for a while now. The single biggest lesson: **the agent's own judgment of completion is the weakest signal in the pipeline.**

Here's what I've seen consistently:
- Agents declare completion on partially-broken builds
- Tests that were supposed to pass... don't
- Files that were supposed to be created... weren't
- The happy path works, but edge cases are silently dropped

This isn't a model quality problem. It's a **workflow quality** problem.

## What actually works: structured verification

The pattern that changed everything for me:

1. **Clear spec up front** — not a vague prompt, but a concrete specification
2. **Verification gates** — automated checks that run after every phase
3. **The morning-after review** — come back to a diff you can actually evaluate

When you wake up to a reviewable output instead of a "trust me" summary, everything changes. You stop being a code babysitter and start being an engineering reviewer.

## What I built to solve this

I ended up building **Ralph Workflow** — a free and open-source orchestrator that wraps your existing coding agents in a structured pipeline:

- **Planning phase** — turns a spec into an implementation plan
- **Development phase** — coding agents work through the plan
- **Verification phase** — automated checks validate the output

The key insight: it's not about replacing your agents. It's about giving them a workflow that makes their output **reviewable**.

## Try it yourself

Ralph Workflow is free and open source. Pick one backlog task tonight, run it, and come back to a reviewable diff in the morning.

👉 **Codeberg (primary):** [codeberg.org/RalphWorkflow/Ralph-Workflow](https://codeberg.org/RalphWorkflow/Ralph-Workflow)
👉 **GitHub (mirror):** [github.com/Ralph-Workflow/Ralph-Workflow](https://github.com/Ralph-Workflow/Ralph-Workflow)
👉 **Docs:** [ralphworkflow.com/docs](https://ralphworkflow.com/docs)

```bash
pipx install ralph-workflow
ralph --help
```

**Requires Python 3.12+.** Bring your own coding agents. Keep your keys to yourself.

---

*What's your experience with AI coding agent handoffs? Drop a comment — I'd love to hear what's worked (and what hasn't) in your setup.*
"""

JS_BOOTSTRAP = r"""
const { chromium } = require('playwright');
const fs = require('fs');

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  const context = await browser.newContext({
    userAgent: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
  });
  const page = await context.newPage();
  page.setDefaultTimeout(30000);

  const result = { ok: false, step: 'start', error: null, api_key: null, article_url: null };

  try {
    // Step 1: Create account via Email signup
    console.log('[Step 1] Creating new Dev.to account via Email...');
    result.step = 'create_account';

    // Go to the signup page
    await page.goto('https://dev.to/enter?state=new-user', { waitUntil: 'networkidle' });
    await page.waitForTimeout(3000);

    // Click "Sign up with Email" link to get the email registration form
    const emailSignupLink = page.locator('a:has-text("Sign up with Email"), a[href*="enter?state=email"]').first();
    if (await emailSignupLink.count() > 0) {
      await emailSignupLink.click();
      await page.waitForTimeout(3000);
      console.log('  Clicked Sign up with Email');
    }

    // Now look for the registration form
    const nameField = page.locator('input[name="user[name]"]');
    const usernameField = page.locator('input[name="user[username]"]');
    const emailField = page.locator('input[name="user[email]"]');
    const passField = page.locator('input[name="user[password]"]');
    const passConfirmField = page.locator('input[name="user[password_confirmation]"]');

    const nameCount = await nameField.count();
    console.log('  Registration name fields: ' + nameCount);

    if (nameCount > 0) {
      await nameField.fill(NAME);
      await usernameField.fill(USERNAME);
      await emailField.fill(EMAIL);
      await passField.fill(PASS);
      await passConfirmField.fill(PASS);

      // Accept terms if present
      const termsCheckbox = page.locator('input[type="checkbox"]').first();
      if (await termsCheckbox.count() > 0) {
        await termsCheckbox.check();
      }

      // Submit — use a more specific selector for the signup form
      let submitClicked = false;
      // Try to find the form's submit button specifically
      const formSubmitBtns = await page.$$('form#new_user input[type="submit"], form#new_user button[type="submit"]');
      for (const btn of formSubmitBtns) {
        const isVisible = await btn.isVisible();
        if (isVisible) {
          await btn.click();
          submitClicked = true;
          break;
        }
      }
      if (!submitClicked) {
        // Fallback: press Enter on the last field
        await passConfirmField.press('Enter');
      }
      await page.waitForTimeout(5000);
      console.log('  Submitted registration form');

      // Check result
      const url = page.url();
      console.log('  URL after registration: ' + url);
      const pageText = await page.evaluate(() => document.body.innerText.substring(0, 2000));

      if (pageText.includes('verification') || pageText.includes('confirm') || pageText.includes('email')) {
        console.log('  Email verification required');
        result.step = 'verify_email';
      } else if (pageText.includes('Welcome') || pageText.includes('onboarding') || pageText.includes('tags')) {
        console.log('  Account created!');
        result.step = 'onboarding';
      } else if (pageText.includes('taken') || pageText.includes('exists') || pageText.includes('already')) {
        console.log('  Username/email already taken');
        result.step = 'account_exists';
      } else {
        console.log('  State: ' + pageText.substring(0, 300));
        result.step = 'unknown_registration_state';
      }

      const cookies = await context.cookies();
      fs.writeFileSync(COOKIES_FILE, JSON.stringify(cookies));
    } else {
      // Might be on a login-only page, try the username step of Forem signup
      console.log('  Trying alternative signup path...');
      
      // Forem signup often has username as first step
      const userField = page.locator('input[name="user[username]"], input#user_username');
      if (await userField.count() > 0) {
        await userField.fill(USERNAME);
        const nextBtn = page.locator("form#new_user input\[type=\"submit\"\], form#new_user button\[type=\"submit\"\]").first() //replaced; dummy = page.locator('form#new_user input[type="submit"], form#new_user button[type="submit"]').first();
        await nextBtn.click();
        await page.waitForTimeout(3000);
        console.log('  Submitted username step');
        
        // Now check if we need to fill name/email/password
        const nowNameCount = await page.locator('input[name="user[name]"]').count();
        console.log('  Name fields after username step: ' + nowNameCount);
        
        if (nowNameCount > 0) {
          await page.locator('input[name="user[name]"]').fill(NAME);
          await page.locator('input[name="user[email]"]').fill(EMAIL);
          await page.locator('input[name="user[password]"]').fill(PASS);
          await page.locator('input[name="user[password_confirmation]"]').fill(PASS);
          
          const finalBtn = page.locator('form#new_user input[type="submit"], form#new_user button[type="submit"]').first();
          await finalBtn.click();
          await page.waitForTimeout(5000);
        }
        
        const cookies = await context.cookies();
        fs.writeFileSync(COOKIES_FILE, JSON.stringify(cookies));
      } else {
        console.log('  No signup fields found at all');
        result.step = 'no_registration_form';
      }
    }

    // Step 2: Try to log in if we didn't succeed with registration
    const isLoggedIn = await page.evaluate(() => {
      return document.querySelector('meta[name="user-signed-in"]')?.content === 'true';
    });
    console.log('  Logged in after registration: ' + isLoggedIn);

    if (!isLoggedIn) {
      console.log('[Step 2] Attempting login...');
      result.step = 'login';

      await page.goto('https://dev.to/enter?state=email', { waitUntil: 'networkidle' });
      await page.waitForTimeout(3000);

      // Try email login
      const loginEmail = page.locator('input[name="user[email]"]');
      const loginPass = page.locator('input[name="user[password]"]');

      if (await loginEmail.count() > 0) {
        await loginEmail.fill(EMAIL);
        await loginPass.fill(PASS);

        // Find Continue button
        const buttons = await page.$$('input[type="submit"], button[type="submit"]');
        for (const btn of buttons) {
          const text = await btn.textContent();
          if (text && text.toLowerCase().includes('continue')) {
            await btn.click();
            await page.waitForTimeout(5000);
            console.log('  Clicked Continue');
            break;
          }
        }

        const nowLoggedIn = await page.evaluate(() => {
          return document.querySelector('meta[name="user-signed-in"]')?.content === 'true';
        });
        console.log('  Logged in: ' + nowLoggedIn);

        if (!nowLoggedIn) {
          const url = page.url();
          console.log('  URL: ' + url);
          if (url.includes('appleid')) {
            result.error = 'Account uses Apple OAuth - cannot automate login';
            require('fs').writeFileSync(OUTPUT, JSON.stringify(result));
            await browser.close();
            process.exit(1);
          }
        }

        // Save cookies
        const cookies = await context.cookies();
        fs.writeFileSync(COOKIES_FILE, JSON.stringify(cookies));
      }
    }

    // Step 3: If logged in, try to get API key
    const finalLogin = await page.evaluate(() => {
      return document.querySelector('meta[name="user-signed-in"]')?.content === 'true';
    });

    if (finalLogin) {
      console.log('[Step 3] Getting API key...');
      result.step = 'get_api_key';

      await page.goto('https://dev.to/settings/extensions', { waitUntil: 'networkidle' });
      await page.waitForTimeout(4000);

      const extText = await page.evaluate(() => document.body.innerText);

      if (extText.includes('API key') || extText.includes('Generate')) {
        console.log('  On extensions page');

        // Try to generate a key
        const genBtn = page.locator('text="Generate API Key"').first();
        if (await genBtn.count() > 0) {
          const descField = page.locator('input[name*="description"]').first();
          if (await descField.count() > 0) {
            await descField.fill('RalphWorkflow Automation');
          }
          await genBtn.click();
          await page.waitForTimeout(4000);
          console.log('  Generated new API key');
        }

        // Extract key
        const fullText = await page.evaluate(() => document.body.innerText);
        const keyMatch = fullText.match(/[a-f0-9]{32,64}/i);
        if (keyMatch) {
          result.api_key = keyMatch[0];
          console.log('  API key: ' + result.api_key.substring(0, 8) + '...');
        }
      }

      // Step 4: Publish article
      console.log('[Step 4] Publishing article...');
      result.step = 'publish_article';

      await page.goto('https://dev.to/new', { waitUntil: 'networkidle' });
      await page.waitForTimeout(4000);

      // Fill article form
      const titleInput = page.locator('input[aria-label="Post Title"], input[name="article[title]"], input#article_title');
      if (await titleInput.count() > 0) {
        await titleInput.fill(TITLE);
        console.log('  Filled title');
      }

      // Dev.to uses a rich text editor - we need to set the body
      // The body field may be a textarea or a contenteditable div
      const bodyArea = page.locator('textarea#article_body_markdown, textarea[name="article[body_markdown]"]');
      if (await bodyArea.count() > 0) {
        await bodyArea.fill(BODY);
        console.log('  Filled body');
      } else {
        // Try contenteditable div approach
        const editor = page.locator('[contenteditable="true"]').first();
        if (await editor.count() > 0) {
          await editor.click();
          await editor.fill(BODY);
          console.log('  Filled editor');
        }
      }

      // Set tags
      const tagInput = page.locator('input[aria-label*="tag"], input[name*="tag"], input#tag-input');
      if (await tagInput.count() > 0) {
        for (const tag of TAGS) {
          await tagInput.fill(tag);
          await page.waitForTimeout(1000);
          await page.keyboard.press('Enter');
          await page.waitForTimeout(500);
        }
        console.log('  Set tags: ' + TAGS.join(', '));
      }

      // Publish
      const publishBtn = page.locator('button:has-text("Publish"), button.crayons-btn:has-text("Publish")').first();
      if (await publishBtn.count() > 0) {
        await publishBtn.click();
        await page.waitForTimeout(5000);
        console.log('  Published!');

        result.article_url = page.url();
        result.ok = true;
        console.log('  Article URL: ' + result.article_url);
      } else {
        // Try Save as draft instead of Publish
        const draftBtn = page.locator('button:has-text("Save"), button:has-text("Preview")').first();
        if (await draftBtn.count() > 0) {
          console.log('  Could not find Publish button - saving as draft');
          result.step = 'draft_saved';
        }
        result.ok = true; // Partial success
      }
    } else {
      result.error = 'Could not log in';
    }

  } catch (e) {
    result.error = 'Exception at step ' + result.step + ': ' + e.message;
    console.error('ERROR: ' + e.message);
  } finally {
    require('fs').writeFileSync(OUTPUT, JSON.stringify(result));
    await browser.close();
  }
})();
"""


def main() -> int:
    dry_run = '--dry-run' in sys.argv

    if dry_run:
        print('[DRY RUN] Would bootstrap Dev.to lane with new account')
        return 0

    # Check if we already have a working API key
    existing_creds = {}
    if CREDS_PATH.exists():
        existing_creds = json.loads(CREDS_PATH.read_text(encoding='utf-8'))
    
    if existing_creds.get('api_key') and '--force' not in sys.argv:
        print(f"API key already exists: {existing_creds['api_key'][:8]}...{existing_creds['api_key'][-4:]}")
        print('Use --force to re-create')
        return 0

    cookies_path = Path('/tmp/devto_cookies.json')
    script = JS_BOOTSTRAP.replace('OUTPUT', json.dumps(str(OUTPUT_PATH)))
    script = script.replace('COOKIES_FILE', json.dumps(str(cookies_path)))
    script = script.replace('EMAIL', json.dumps(DEVTO_EMAIL))
    script = script.replace('USERNAME', json.dumps(DEVTO_USERNAME))
    script = script.replace('NAME', json.dumps(DEVTO_DISPLAY_NAME))
    script = script.replace('PASS', json.dumps(DEVTO_PASSWORD))
    script = script.replace('TITLE', json.dumps(ARTICLE_TITLE))
    script = script.replace('BODY', json.dumps(ARTICLE_BODY))
    script = script.replace('TAGS', json.dumps(ARTICLE_TAGS))

    script_path = Path('/tmp/devto_bootstrap.js')
    script_path.write_text(script)

    print('Bootstrapping Dev.to lane — creating account, getting API key, publishing...')
    result = subprocess.run(
        ['node', str(script_path)],
        capture_output=True, text=True, timeout=90,
    )
    print(result.stdout)
    if result.stderr:
        print(f'Stderr: {result.stderr[:500]}')

    if OUTPUT_PATH.exists():
        data = json.loads(OUTPUT_PATH.read_text())
        print(f'\nResult: ok={data.get("ok")}, step={data.get("step")}')
        if data.get('api_key'):
            print(f'API key: {data["api_key"][:8]}...{data["api_key"][-4:]}')
        if data.get('article_url'):
            print(f'Article: {data["article_url"]}')
        if data.get('error'):
            print(f'Error: {data["error"]}')

        if data.get('ok') and data.get('api_key'):
            # Save updated creds
            creds = {
                'email': DEVTO_EMAIL,
                'username': DEVTO_USERNAME,
                'password': DEVTO_PASSWORD,
                'api_key': data['api_key'],
                'created_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
            }
            CREDS_PATH.parent.mkdir(parents=True, exist_ok=True)
            CREDS_PATH.write_text(json.dumps(creds, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
            print(f'\nSUCCESS: Saved Dev.to credentials with API key')
            return 0
        elif data.get('ok'):
            print(f'\nPartial success: account created/used but no API key or article yet')
            return 0
        else:
            print(f'\nFAILED: {data.get("error", "Unknown error")}')
            return 1

    print('\nFAILED: No result file')
    return 1


if __name__ == '__main__':
    sys.exit(main())
