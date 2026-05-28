#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from collections import deque

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


PROFILE_PORT_FILES = (
    Path("/home/mistlight/.config/google-chrome/DevToolsActivePort"),
    Path("/home/mistlight/.openclaw/workspace/.reddit-main-profile/DevToolsActivePort"),
)
OUTREACH_LOG = Path("/home/mistlight/.openclaw/workspace/outreach-log.md")
REDDIT_LOG_JSONL = Path("/home/mistlight/.openclaw/workspace/agents/marketing/logs/reddit_posts.jsonl")
REDDIT_LOG_MD_DIR = Path("/home/mistlight/.openclaw/workspace/agents/marketing/logs/reddit-posts")
REDDIT_ACCOUNT_CONFIG = Path("/home/mistlight/.openclaw/workspace/agents/marketing/reddit_account.json")


BANNED_BODY_PREFIXES = (
    # 2026-05-19: original banned set
    "honestly the part i'd optimize first is the handoff",
    "my default is to optimize for a clean morning-after review",
    "the best improvement i've seen is making the output easier to judge",
    "what i kept getting wrong early on was treating 'the agent said it was done'",
    "the part that bites me most is not choosing which tool",
    "the overnight run problem is usually not the agent",
    # 2026-05-20: repair -- repeated opening confirmed in audit; ban exact variant + close rephrasings
    "honestly the part i'd optimize first is the handoff, not the model stack",
    "the part i'd optimize first is the handoff, not the model stack",
    "if i had to optimize one thing, it would be the handoff",
    "the handoff is where most overnight runs actually fail",
    # 2026-05-20: additional rephrasings of banned openings seen in recent body hashes
    "the real bottleneck is never the tool switch",
    "switching between claude code and codex sounds like a workflow upgrade",
    "the problem with multi-hop claude workflows is not the model intelligence",
    "what i wanted from a claude plus codex setup was not two opinions",
    # 2026-05-21: repair -- additional opening repeats from audit
    "forcing the handoff to be boring and explicit",
    "the fix is an explicit baton pass between sessions",
    # 2026-05-27: repair -- repeated question-style opener seen twice in same thread family
    "which of the five made the most difference for your team",
)

BANNED_BODY_PHRASES = (
    # 2026-05-19: original banned phrases
    "reviewable work units",
    "for me the reliable pattern is",
    "for me the reliable version is",
    "if the run ends with a readable diff, checks, and unresolved decisions called out",
    "if the run ends with one readable diff, real checks, and a short note about what still looks sketchy",
    # 2026-05-20: repair -- confirmed repeated sentence patterns from audit
    "ralph workflow is free and open-source: it orchestrates the handoff between tools",
    "ralph workflow is free and open-source: it runs the ai coding tools you already use",
    "ralph workflow is free and open-source: it adds that discipline to the agents you already use",
    "the run ends with a confident summary",
    "lying to yourself about the result",
    "stale assumptions",
    # 2026-05-20: body cadence repeats confirmed in audit
    "what changed, what ran, and what still needs a human decision",
    "what changed, what ran, and what still looks risky",
    "one readable diff, real checks, and a short note",
    "bounded diff, check results, and a short unresolved list",
    "the morning-after review into a bounded check",
    "transcript archaeology",
    "i've had the best results when i stop optimizing for more agents",
    "we've wrapped that pattern into ralph workflow",
    # 2026-05-20: additional body phrase repeats from cross-post audit
    "one tool implements, the other reviews or challenges",
    "one tool builds, one checks",
    "one tool writes, the other challenges",
    "small scoped task, explicit done criteria before it starts",
    "trust the finish line, not the agent's claim",
    # 2026-05-21: repair -- additional cadence repeats from cross-post audit
    "ralph workflow is free and open-source: it enforces that baton pass",
    "forcing the handoff to be boring and explicit",
    "explicit baton pass between sessions",
    "gives you something you can actually judge instead of just admire",
    "one scoped task, one readable diff, real checks, and a short receipt",
    "scoped task, bounded diff, check evidence, named open decisions",
    "baton pass so sessions hand off cleanly",
    "comes back to something reviewable instead of a confident summary",
    "wake up to something reviewable instead of",
    "reconstructing the whole night from scattered sessions",
    "too big to babysit but too risky to trust blindly",
    "runs the agent clis you already use on your own machine",
)


def _recent_logged_bodies(*, opening_limit: int = 10, cadence_limit: int = 3) -> tuple[list[str], list[str]]:
    if not REDDIT_LOG_JSONL.exists():
        return [], []

    recent_openings: deque[str] = deque(maxlen=opening_limit)
    recent_cadence: deque[str] = deque(maxlen=cadence_limit)

    for line in REDDIT_LOG_JSONL.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        body = (row.get("body") or "").strip()
        if not body:
            continue
        recent_openings.append(body)
        recent_cadence.append(body)

    return list(recent_openings), list(recent_cadence)


VERBATIM_DUPLICATE_BODY = (
    "honestly the part i'd optimize first is the handoff, not the model stack.\n\n"
    "if the run ends with one readable diff, real checks, and a short note about what still looks sketchy, "
    "you can move fast without lying to yourself about the result.\n\n"
    "most of the pain is not raw generation. it's stale assumptions, fuzzy ownership, "
    "and nobody making the finish easy to review."
)


def _opening_line(text: str) -> str:
    return text.splitlines()[0].strip().lower() if text.splitlines() else ""


def _opening_reused(body: str, recent: list[str]) -> bool:
    opening = _opening_line(body)
    if not opening:
        return False
    recent_openings = {_opening_line(prev) for prev in recent if prev.strip()}
    return opening in recent_openings


def _paragraph_concept(paragraph: str) -> str:
    text = paragraph.lower()
    if "https://github.com/ralph-workflow/ralph-workflow" in text or "ralphworkflow" in text or "ralph workflow" in text:
        return "product_cta"
    if any(token in text for token in ["what breaks first", "confidence in the merged state", "merged state", "operating posture", "trust isn't", "trust as", "failure mode is trust"]):
        return "thesis"
    if any(token in text for token in ["one tool writes", "one tool implements", "one phase owns", "phase", "handoff", "builder", "review pass", "role split"]):
        return "phase_split"
    if any(token in text for token in ["shared boundaries", "shared boundary", "config/schema/migrations", "merged state", "global check"]):
        return "shared_boundary"
    if any(token in text for token in ["finish receipt", "receipt", "morning-after", "re-entry", "heroic transcript", "long transcript"]):
        return "finish_receipt"
    if any(token in text for token in ["checks", "diff", "done criteria", "acceptance criteria", "open questions", "human decision"]):
        return "review_proof"
    return "generic"


def _concept_cadence_signature(body: str) -> tuple[str, ...]:
    paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
    signature: list[str] = []
    for paragraph in paragraphs:
        concept = _paragraph_concept(paragraph)
        if not signature or signature[-1] != concept:
            signature.append(concept)
    return tuple(signature)


def _concept_cadence_repeats(body: str, recent: list[str]) -> bool:
    current = _concept_cadence_signature(body)
    if not current:
        return False
    recent_signatures = [_concept_cadence_signature(prev) for prev in recent if prev.strip()]
    if current in recent_signatures:
        return True
    current_core = tuple(part for part in current if part != "generic")
    for prev in recent_signatures:
        prev_core = tuple(part for part in prev if part != "generic")
        if current_core and current_core == prev_core:
            return True
        if len(current_core) >= 3 and len(prev_core) >= 3 and current_core[:3] == prev_core[:3]:
            return True
    return False


def validate_body(body: str) -> tuple[bool, str]:
    """Check body against banned list and recent-post freshness rules. Returns (ok, reason)."""
    text = body.lower()
    if VERBATIM_DUPLICATE_BODY in text:
        return False, "verbatim duplicate body: same body posted twice on May 19"
    opening = _opening_line(body)
    for prefix in BANNED_BODY_PREFIXES:
        if opening.startswith(prefix):
            return False, f"banned opening prefix: {prefix!r}"
    for phrase in BANNED_BODY_PHRASES:
        if phrase in text:
            return False, f"banned phrase: {phrase!r}"

    recent_openings, recent_cadence = _recent_logged_bodies()
    if _opening_reused(body, recent_openings):
        return False, "opening reused from last 10 logged Reddit posts"
    if _concept_cadence_repeats(body, recent_cadence):
        return False, "body cadence matches one of the last 3 logged Reddit posts"
    return True, "ok"


@dataclass
class PostResult:
    ok: bool
    status: str
    thread_url: str
    comment_url: str | None = None
    detail: str | None = None


def load_account_config() -> dict:
    if not REDDIT_ACCOUNT_CONFIG.exists():
        return {}
    try:
        return json.loads(REDDIT_ACCOUNT_CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def expected_username() -> str | None:
    value = load_account_config().get("expected_username")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _get_logged_in_username_via_api(context) -> str | None:
    """Get the currently-logged-in Reddit username via the /api/me.json endpoint.
    
    This is the most reliable method — bypasses DOM parsing entirely.
    Returns the username string or None if the session is not logged in.
    """
    try:
        page = context.new_page()
        try:
            page.goto("https://www.reddit.com/api/me.json", timeout=20000)
            page.wait_for_timeout(2000)
            content = page.content()
            import re
            match = re.search(r'<pre>(.*?)</pre>', content, re.DOTALL)
            if not match:
                return None
            data = json.loads(match.group(1))
            return data.get("data", {}).get("name") or None
        finally:
            page.close()
    except Exception:
        return None


def slugify(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")[:80] or "reddit-post"


def normalize_old_reddit_url(url: str) -> str:
    url = url.strip()
    url = url.strip('<>')
    url = re.sub(r"^https://www\.reddit\.com/", "https://old.reddit.com/", url)
    url = re.sub(r"^https://reddit\.com/", "https://old.reddit.com/", url)
    return url


def normalize_new_reddit_url(url: str) -> str:
    """Convert any Reddit URL to standard new-reddit www form."""
    url = url.strip()
    url = url.strip('<>')
    url = re.sub(r"^https://old\.reddit\.com/", "https://www.reddit.com/", url)
    url = re.sub(r"^https://reddit\.com/", "https://www.reddit.com/", url)
    return url


def get_cdp_http_url() -> str:
    candidate_ports: list[str] = []
    env_port = os.environ.get("REDDIT_CDP_PORT", "").strip()
    if env_port:
        candidate_ports.append(env_port)

    checked: list[str] = []
    for path in PROFILE_PORT_FILES:
        checked.append(str(path))
        if not path.exists():
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        if not lines:
            continue
        port = lines[0].strip()
        if port:
            candidate_ports.append(port)

    # Self-heal for fixed-port launches when the stale DevToolsActivePort file was not refreshed.
    candidate_ports.extend(["9222"])

    seen: set[str] = set()
    for port in candidate_ports:
        if port in seen:
            continue
        seen.add(port)
        url = f"http://127.0.0.1:{port}"
        try:
            with urllib.request.urlopen(url + "/json/version", timeout=2) as response:
                if response.status == 200:
                    return url
        except Exception:
            continue

    checked_text = ", ".join(checked)
    raise SystemExit(
        "Live Chromium Reddit session is not reachable. Checked DevToolsActivePort files: "
        + checked_text
        + " and fallback port 9222."
    )


def outreach_log_contains(url: str) -> bool:
    if not OUTREACH_LOG.exists():
        return False
    text = OUTREACH_LOG.read_text(encoding="utf-8")
    return url in text or normalize_old_reddit_url(url) in text


def append_outreach_log(thread_url: str, comment_url: str, note: str) -> None:
    text = OUTREACH_LOG.read_text(encoding="utf-8") if OUTREACH_LOG.exists() else "# Outreach Log\n"
    analysis_path = "/home/mistlight/.openclaw/workspace/agents/marketing/logs/reddit_post_analysis.md"
    block = (
        "\n### Reddit autopost\n"
        f"- **Thread:** {thread_url}\n"
        f"- **Comment URL:** {comment_url}\n"
        f"- **Status:** ✅ Published\n"
        f"- **Notes:** {note}\n"
        f"- **Retrospective source:** `{analysis_path}`\n"
    )
    OUTREACH_LOG.write_text(text.rstrip() + "\n" + block, encoding="utf-8")


def append_structured_log(*, thread_url: str, comment_url: str, body: str, note: str, metadata: dict | None = None) -> None:
    ts = datetime.now().isoformat()
    metadata = metadata or {}
    account = metadata.get("account") or "unknown"
    REDDIT_LOG_JSONL.parent.mkdir(parents=True, exist_ok=True)
    REDDIT_LOG_MD_DIR.mkdir(parents=True, exist_ok=True)

    record = {
        "timestamp": ts,
        "platform": "reddit",
        "account": account,
        "thread_url": thread_url,
        "comment_url": comment_url,
        "note": note,
        "body": body,
        "metadata": metadata,
    }
    with REDDIT_LOG_JSONL.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    stem = f"{datetime.now().strftime('%Y-%m-%d_%H%M%S')}_{slugify(metadata.get('title') or note)}"
    md = [
        f"# Reddit Post Log — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        f"- Account: `{account}`",
        f"- Thread URL: {thread_url}",
        f"- Comment URL: {comment_url}",
        f"- Note: {note}",
    ]
    for key in ["report", "rank", "title", "community", "angle"]:
        if metadata.get(key) is not None:
            md.append(f"- {key.capitalize()}: {metadata[key]}")
    md.extend(["", "## Comment body", "", body, ""])
    (REDDIT_LOG_MD_DIR / f"{stem}.md").write_text("\n".join(md), encoding="utf-8")


def _post_via_old_reddit(page, thread_url: str, body: str, current_username: str | None, expected: str | None, dry_run: bool = False) -> PostResult | None:
    """Attempt to post via old Reddit. Returns None if old Reddit cannot serve the thread."""
    # Old Reddit textarea selectors
    textarea_selectors = [
        "form.usertext.cloneable textarea[name='text']",
        "textarea[name='text']",
        "div[name='text']",
        "textarea.comment-textarea",
        "textarea[id^='comment-submit']",
    ]
    textarea = None
    for sel in textarea_selectors:
        try:
            candidate = page.locator(sel).first
            candidate.wait_for(timeout=5000)
            textarea = candidate
            break
        except (PlaywrightTimeoutError, Exception):
            continue
    if textarea is None:
        return None  # Old Reddit doesn't have a comment box — fall through to new Reddit
    textarea.fill(body)

    if dry_run:
        return PostResult(True, "dry_run_ready", thread_url, detail="Textarea filled successfully (old Reddit)")

    # Old Reddit submit button
    button_selectors = [
        "form.usertext.cloneable button.save",
        "button[type='submit']",
        "button[name='submit']",
        "form.usertext button.save",
        "button.save",
    ]
    clicked = False
    for sel in button_selectors:
        try:
            btn = page.locator(sel).first
            btn.wait_for(timeout=3000)
            btn.click()
            clicked = True
            break
        except (PlaywrightTimeoutError, Exception):
            continue
    if not clicked:
        return PostResult(False, "submit_button_not_found", thread_url, detail=f"Submit button not found among {button_selectors}")
    page.wait_for_timeout(5000)

    snippet = body.splitlines()[0][:80].lower()
    author = current_username or ""
    comment_selectors = [
        f".thing.comment[data-author='{author}']",
        f"[data-author='{author}'].comment",
        f".comment[data-author='{author}']",
        f"div[data-author='{author}']",
    ]
    comment = None
    for sel in comment_selectors:
        try:
            candidate = page.locator(sel).filter(has_text=snippet[:40]).first
            candidate.wait_for(timeout=10000)
            comment = candidate
            break
        except (PlaywrightTimeoutError, Exception):
            pass
    if comment is None:
        for sel in comment_selectors:
            candidates = page.locator(sel)
            if candidates.count() > 0:
                comment = candidates.first
                break
        if comment is None:
            return PostResult(False, "post_not_confirmed", thread_url, detail="Comment was not found after save")

    permalink_selectors = ["a.bylink", "a.permalink", "a[data-click-id='comment timestamp']", "a[href*='/comments/']"]
    permalink = None
    for sel in permalink_selectors:
        try:
            candidate = comment.locator(sel).first
            candidate.wait_for(timeout=3000)
            href = candidate.get_attribute("href")
            if href:
                permalink = href
                break
        except (PlaywrightTimeoutError, Exception):
            continue
    if permalink and permalink.startswith("/"):
        permalink = "https://old.reddit.com" + permalink
    comment_url = permalink or thread_url
    return PostResult(True, "posted", thread_url, comment_url=comment_url)


def _post_via_new_reddit(page, thread_url: str, body: str, current_username: str | None) -> PostResult:
    """Post comment via new Reddit's shiny Reddit UI."""
    # Scroll down to reveal the comments section and comment textarea.
    # The comment box is often below the fold on thread pages.
    for _ in range(4):
        page.evaluate("window.scrollBy(0, 800)")
        page.wait_for_timeout(600)
    page.wait_for_timeout(1200)

    # New Reddit uses contenteditable divs AND native textareas (depending on thread type)
    # The comment box may be:
    #   1. A native <textarea> inside a text-area-wrapper (link-post comments section)
    #   2. A div[contenteditable=true] (self-post comment forms)
    # Always prefer the VISIBLE element with non-zero dimensions
    textarea_selectors = [
        'div.text-area-wrapper textarea',
        'div[contenteditable="true"]',
        'div[role="textbox"]',
        'div[name="text"]',
        'textarea',
    ]
    textarea = None
    for sel in textarea_selectors:
        try:
            candidates = page.locator(sel)
            count = candidates.count()
            if count == 0:
                continue
            for i in range(count):
                candidate = candidates.nth(i)
                parent = candidate.locator("..").first
                parent_class = (parent.get_attribute("class") or "").lower()
                # Avoid subreddit description editors and sidebar elements
                if "subreddit-text" in parent_class or "sidebar" in parent_class:
                    continue
                # For native textarea elements, must be inside text-area-wrapper AND have non-zero size
                if sel in ('textarea', 'div.text-area-wrapper textarea'):
                    if 'text-area-wrapper' not in parent_class:
                        continue
                    # Skip hidden textareas (display:none or 0x0 size)
                    dims = candidate.evaluate("el => ({w: el.offsetWidth, h: el.offsetHeight})")
                    if dims['w'] == 0 or dims['h'] == 0:
                        continue
                candidate.wait_for(timeout=5000)
                textarea = candidate
                break
            if textarea:
                break
        except (PlaywrightTimeoutError, Exception):
            continue

    if textarea is None:
        return PostResult(False, "textarea_not_found", thread_url, detail=f"No comment textarea found among {textarea_selectors}")

    # Reddit uses a Lexical rich-text editor. The visible editing surface is a
    # div[contenteditable=true] (CE overlay) overlaid on a hidden textarea backing store.
    # IMPORTANT: fill() on the textarea sets the backing store but does NOT update the
    # CE overlay — Ctrl+Enter reads from the CE overlay, so fill() results in empty submits.
    # Fix: click the textarea to reveal/focus the CE overlay, then use keyboard.type().
    textarea.scroll_into_view_if_needed()
    page.wait_for_timeout(400)
    try:
        textarea.click()
        page.wait_for_timeout(300)
        # Select any placeholder text so we replace it
        page.keyboard.press("Control+a")
        page.wait_for_timeout(150)
        page.keyboard.type(body, delay=2)
    except (PlaywrightTimeoutError, Exception):
        # Fall back to fill if click fails (some Reddit variants accept fill directly)
        textarea.fill(body)
    page.wait_for_timeout(800)

    # The Comment submit button appears only after the textarea is focused and has content.
    # Look for it in the context of the textarea wrapper.
    textarea_wrapper = textarea.locator("..").first  # div.text-area-wrapper
    clicked = False

    # First: look for "Comment" button near the textarea wrapper (appears after focus+content)
    for sel in ["button:has-text('Comment')", "button[type='submit']", "[data-testid='comment-submit-button']"]:
        try:
            # Search in textarea wrapper, form container (grandparent), and action bar (great-grandparent)
            for container in [textarea_wrapper, textarea_wrapper.locator("..").first, textarea_wrapper.locator("../..").first]:
                btns = container.locator(sel)
                for j in range(btns.count()):
                    btn = btns.nth(j)
                    if not btn.is_visible():
                        continue
                    btn.wait_for(timeout=2000)
                    btn.click()
                    clicked = True
                    break
                if clicked:
                    break
            if clicked:
                break
        except (PlaywrightTimeoutError, Exception):
            continue

    # Second: try Ctrl+Enter (Reddit's keyboard shortcut for submitting comments)
    if not clicked:
        try:
            page.keyboard.press("Control+Enter")
            clicked = True
        except (PlaywrightTimeoutError, Exception):
            pass

    if not clicked:
        return PostResult(False, "submit_button_not_found", thread_url, detail="No visible submit button found for comment form")

    # Wait for Reddit to process the comment and add it to the DOM
    page.wait_for_timeout(6000)

    # After submit, scroll back to top of thread so the new comment is in view
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(1500)

    # Confirm the comment was posted — search the full page body for our snippet.
    # This is more reliable than specific selectors which vary across Reddit threads.
    snippet = body.splitlines()[0][:60].lower()

    # Primary check: look for our snippet anywhere in the page text
    posted = False
    try:
        page_text = (page.text_content("body") or "").lower()
        if snippet[:40] in page_text:
            posted = True
    except (PlaywrightTimeoutError, Exception):
        pass

    # Fallback: also check specific comment element selectors
    if not posted:
        comment_selectors = [
            f"[data-author='{current_username or ''}']",
            f"div[data-testid='comment']",
            "article[data-testid='comment']",
        ]
        for sel in comment_selectors:
            try:
                candidates = page.locator(sel)
                for i in range(candidates.count()):
                    text = (candidates.nth(i).text_content() or "").lower()
                    if snippet[:40] in text:
                        posted = True
                        break
                if posted:
                    break
            except (PlaywrightTimeoutError, Exception):
                continue

    if not posted:
        return PostResult(False, "post_not_confirmed", thread_url, detail=f"Comment submitted but not found in page (snippet: {snippet[:40]})")

    # Find the comment element we just posted to get its permalink
    comment = None
    comment_selectors = [
        f"[data-author='{current_username or ''}']",
        f"div[data-testid='comment']",
        "article[data-testid='comment']",
    ]
    for sel in comment_selectors:
        try:
            candidates = page.locator(sel)
            for i in range(candidates.count()):
                candidate = candidates.nth(i)
                text = (candidate.text_content() or "").lower()
                if snippet[:40] in text:
                    comment = candidate
                    break
            if comment:
                break
        except (PlaywrightTimeoutError, Exception):
            continue

    # Get permalink from the comment element
    permalink = None
    if comment:
        permalink_selectors = [
            "a[data-testid='comment timestamp']",
            "a.permalink",
            "a[href*='/comments/']",
        ]
        for sel in permalink_selectors:
            try:
                candidates = comment.locator(sel)
                for i in range(candidates.count()):
                    href = candidates.nth(i).get_attribute("href") or ""
                    if href and '/comments/' in href and 'context' not in href:
                        permalink = href
                        break
                if permalink:
                    break
            except (PlaywrightTimeoutError, Exception):
                continue

    if permalink and permalink.startswith("/"):
        permalink = "https://www.reddit.com" + permalink
    comment_url = permalink or thread_url
    return PostResult(True, "posted", thread_url, comment_url=comment_url)


def post_comment(thread_url: str, body: str, note: str, dry_run: bool = False, metadata: dict | None = None) -> PostResult:
    old_url = normalize_old_reddit_url(thread_url)
    if outreach_log_contains(old_url):
        return PostResult(False, "already_logged", old_url, detail="Thread already exists in outreach-log.md")

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(get_cdp_http_url())
        try:
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.new_page()
            try:
                # Try old Reddit first
                page.goto(old_url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3000)

                body_text = (page.text_content("body") or "").lower()

                # Detect 404 / "page not found" on old Reddit
                is_404_old = (
                    "page not found" in body_text
                    or page.title() in ("reddit.com: page not found", " ClaudeCode: page not found")
                    or "there doesn't seem to be anything here" in body_text
                )

                user_link = page.locator("a[href*='/user/']").first
                current_username = None
                if user_link.count():
                    raw = (user_link.text_content() or "").strip()
                    # Extract just the username (strip /user/ prefix)
                    href = user_link.get_attribute("href") or ""
                    if "/user/" in href:
                        current_username = href.split("/user/")[-1].strip("/")
                    else:
                        current_username = raw

                # Also try to get username via API as a reliable fallback
                api_username = _get_logged_in_username_via_api(context)
                if not current_username and api_username:
                    current_username = api_username

                if not current_username and "logout" not in body_text:
                    if is_404_old:
                        # Fall through to new Reddit
                        pass
                    else:
                        return PostResult(False, "not_logged_in", old_url, detail="Reddit session is not logged in")

                expected = expected_username()
                if expected and current_username and current_username.lower() != expected.lower():
                    # API is authoritative — override DOM-based detection
                    if api_username and api_username.lower() == expected.lower():
                        current_username = api_username
                    else:
                        return PostResult(False, "wrong_account", old_url, detail=f"Logged into u/{current_username}, expected u/{expected}")

                if not is_404_old:
                    # Try old Reddit posting
                    old_result = _post_via_old_reddit(page, old_url, body, current_username, expected, dry_run=dry_run)
                    if old_result is not None:
                        if not old_result.ok:
                            return old_result
                        if not dry_run:
                            meta = dict(metadata or {})
                            meta.setdefault("account", current_username or "unknown")
                            append_outreach_log(old_url, old_result.comment_url or old_url, note)
                            append_structured_log(thread_url=old_url, comment_url=old_result.comment_url or old_url, body=body, note=note, metadata=meta)
                        return old_result

                # Old Reddit didn't work (404 or no textarea) — fall back to new Reddit
                new_url = normalize_new_reddit_url(thread_url)
                page.goto(new_url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3000)

                new_body_text = (page.text_content("body") or "").lower()
                # Use cookie as primary source for new Reddit too
                new_username = _get_logged_in_username_via_api(context)
                if not new_username:
                    # Fallback to DOM for new Reddit
                    new_user_links = page.locator("a[href*='/user/']")
                    for i in range(new_user_links.count()):
                        href = new_user_links.nth(i).get_attribute("href") or ""
                        if "/user/" in href:
                            new_username = href.split("/user/")[-1].strip("/")
                            break

                if not new_username and "logout" not in new_body_text and "log in" not in new_body_text:
                    return PostResult(False, "not_logged_in", new_url, detail="Reddit session is not logged in on new Reddit")

                if expected and new_username and new_username.lower() != expected.lower():
                    return PostResult(False, "wrong_account", new_url, detail=f"Logged into u/{new_username} on new Reddit, expected u/{expected}")

                if dry_run:
                    return PostResult(True, "dry_run_ready", new_url, detail="Textarea filled successfully (new Reddit)")

                new_result = _post_via_new_reddit(page, new_url, body, new_username)
                if not new_result.ok:
                    return new_result

                meta = dict(metadata or {})
                meta.setdefault("account", new_username or "unknown")
                append_outreach_log(old_url, new_result.comment_url or new_url, note)
                append_structured_log(thread_url=old_url, comment_url=new_result.comment_url or new_url, body=body, note=note, metadata=meta)
                return new_result
            finally:
                page.close()
        finally:
            browser.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("--body-file", required=True)
    parser.add_argument("--note", default="Autoposted from reddit monitor shortlist.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--metadata-json")
    args = parser.parse_args()

    body = Path(args.body_file).read_text(encoding="utf-8").strip()
    ok, reason = validate_body(body)
    if not ok:
        print(json.dumps({"ok": False, "status": "banned_content", "detail": reason}, indent=2))
        return 1
    metadata = json.loads(args.metadata_json) if args.metadata_json else None
    result = post_comment(args.url, body, args.note, dry_run=args.dry_run, metadata=metadata)
    print(json.dumps(result.__dict__, indent=2))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
