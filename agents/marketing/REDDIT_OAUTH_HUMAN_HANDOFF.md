# Reddit OAuth Setup — Human Handoff

**Time estimate:** 5 minutes
**Blocking:** Reddit posting is completely dead without this
**Your Reddit account:** u/Informal-Salt827 (ken.li156@gmail.com)

---

## Steps

1. **Go to:** https://www.reddit.com/prefs/apps

2. **Click:** "Create Another App..." (bottom of the page)

3. **Fill in the form:**
   - **Name:** `RalphWorkflow Marketing`
   - **App type:** Select **`script`** ← must be "script", not "web app" or "native app"
   - **Description:** `Reddit marketing and community research for RalphWorkflow`
   - **About URL:** `https://ralphworkflow.com`
   - **Redirect URI:** `http://localhost:8080`

4. **Click "Create App"**

5. **Copy from the new app's details:**
   - **`personal use script`** → this is your `client_id`
   - **`secret`** → this is your `client_secret`

6. **Edit `/home/mistlight/.openclaw/workspace/TOOLS.md`**
   
   Find the `### Reddit API (PRAW)` section and replace the placeholder values:
   ```
   - **Client ID:** <paste from reddit.com/prefs/apps>   ← replace <...> with the personal use script
   - **Client Secret:** <paste from reddit.com/prefs/apps> ← replace <...> with the secret
   ```

7. **Verify:**
   ```bash
   python3 /home/mistlight/.openclaw/workspace/agents/marketing/reddit_praw_post.py --test-connection
   ```
   
   Expected output: `✅ PRAW connection successful` (or similar confirmation)

8. **Done.** The marketing system automatically resumes Reddit posting once credentials are valid.

---

## Why this is needed

- Reddit's server IP is permanently 403-blocked for browser/Playwright access from this machine
- The PRAW path (official Reddit API) is the only working Reddit channel from this environment
- The code is ready; only the credentials are missing
- This unblocks Reddit posting immediately — no other changes needed

## What happens after

- The `reddit_praw_post.py` script reads credentials from TOOLS.md
- The Reddit monitor (which already found 2 fresh discussion opportunities today) can immediately route to posting
- The autoposter prefers PRAW over Playwright automatically
