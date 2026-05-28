# Reddit PRAW Setup — RalphWorkflow Marketing

**Status:** Infrastructure ready — credentials needed to activate

## Why PRAW instead of Playwright

The current `reddit_post.py` uses headless Playwright with a Chrome profile (`.reddit-main-profile`).
Reddit detects this reliably and blocks posts before they go live.

PRAW (Python Reddit API Wrapper) uses Reddit's official API — no browser, no headless Chrome,
no CDP-based detection. Posts appear as normal Reddit activity from the account.

## Credentials needed

Reddit requires a **script-type OAuth app** (not a web app, not a native app).

### Step 1: Register an OAuth app

1. Go to: https://www.reddit.com/prefs/apps
2. Click **"Create Another App..."** (at the bottom)
3. Fill in:
   - **name:** `RalphWorkflow Marketing`
   - **App type:** Select **`script`**
   - **description:** `Reddit marketing and community research for RalphWorkflow`
   - **about url:** `https://ralphworkflow.com`
   - **redirect uri:** `http://localhost:8080`
4. Click **Create App**
5. Copy the:
   - **`personal use script`** — this is your `client_id`
   - **`secret`** — this is your `client_secret`

### Step 2: Add credentials to TOOLS.md

Add a `### Reddit API (PRAW)` section to `TOOLS.md`:

```
### Reddit API (PRAW)
- **Account:** u/Informal-Salt827 (ken.li156@gmail.com)
- **Client ID:** <paste from above>
- **Client Secret:** <paste from above>
- **Redirect URI:** http://localhost:8080
```

### Step 3: Verify

Run:
```bash
cd /home/mistlight/.openclaw/workspace
python3 agents/marketing/reddit_praw_post.py --test-connection
```

## Files

- `agents/marketing/reddit_praw_post.py` — PRAW-based posting module
- `agents/marketing/reddit_praw_credentials.json.example` — credentials template

## How it works

The module:
1. Reads credentials from `TOOLS.md` (same pattern as Apollo credentials)
2. Authenticates via PRAW's OAuth2 flow (userless or script-based)
3. Posts to subreddits and replies to threads via the Reddit API
4. Falls back to Playwright posting only if PRAW fails

## Switching the autoposter

Once PRAW is working, update `reddit_autopost.py` to prefer PRAW over Playwright:

```python
# In reddit_autopost.py — prefer PRAW if available
try:
    from agents.marketing.reddit_praw_post import praw_post, praw_reply
    PRAW_AVAILABLE = True
except ImportError:
    PRAW_AVAILABLE = False
```

## Notes

- PRAW posts appear as normal account activity — no special Reddit treatment
- Rate limiting: Reddit API allows ~1 post per 10 minutes per subreddit safely
- The account `u/Informal-Salt827` is the verified posting account
- Keep the Chrome profile (`.reddit-main-profile`) as fallback for manual recovery
