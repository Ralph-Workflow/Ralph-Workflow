#!/usr/bin/env python3
"""Post scheduled RalphWorkflow drafts to write.as and Telegraph, with Codeberg CTAs.

Rules:
- only publish markdown drafts for today
- never generate filler posts automatically
- skip a draft if the same content hash already posted successfully to all platforms
- always include Codeberg primary + GitHub mirror CTA footer
- post to write.as AND Telegraph simultaneously (diversified distribution)

Repair (2026-05-19): stop write.as-only publishing. Diversified distribution
is critical for Codeberg repo adoption growth.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Tuple

AGENTS_DIR = Path("/home/mistlight/.openclaw/workspace/agents/marketing")
LOG_DIR = AGENTS_DIR / "logs"
DRAFTS_DIR = Path("/home/mistlight/.openclaw/workspace/drafts")
POSTED_FILE = LOG_DIR / "posted_urls.json"

LOG_DIR.mkdir(parents=True, exist_ok=True)

# CTA footer appended to every post body
CODEBERG_PRIMARY = "https://codeberg.org/RalphWorkflow/Ralph-Workflow"
GITHUB_MIRROR = "https://github.com/Ralph-Workflow/Ralph-Workflow"
CTA_FOOTER = (
    f"\n\n---\n\n"
    f"**Ralph Workflow** — "
    f"[View on Codeberg]({CODEBERG_PRIMARY}) (primary) · "
    f"[GitHub mirror]({GITHUB_MIRROR}). "
    f"Free, open-source CLI that orchestrates the coding agents you already use on your own machine."
)


def load_posted() -> dict:
    if POSTED_FILE.exists():
        return json.loads(POSTED_FILE.read_text(encoding="utf-8"))
    return {"posts": []}


def save_posted(data: dict) -> None:
    POSTED_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def parse_front_matter(content: str) -> tuple[dict, str]:
    if not content.startswith("---\n"):
        return {}, content
    marker = "\n---\n"
    end = content.find(marker, 4)
    if end == -1:
        return {}, content
    raw = content[4:end].strip().splitlines()
    metadata = {}
    for line in raw:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        try:
            metadata[key] = json.loads(value)
        except json.JSONDecodeError:
            metadata[key] = value.strip('"')
    body = content[end + len(marker):]
    return metadata, body


def extract_title_and_body(content: str) -> tuple[str, str, dict]:
    metadata, body = parse_front_matter(content)
    title_m = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
    title = title_m.group(1).strip() if title_m else metadata.get("angle", "Untitled")
    return title, body.strip(), metadata


def digest_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def already_posted_successfully(posted: dict, draft_hash: str, platform: str = "write.as") -> bool:
    for item in posted.get("posts", []):
        if item.get("platform") == platform and item.get("ok") and item.get("draft_hash") == draft_hash:
            return True
    return False


def post_writeas(title: str, body: str) -> Tuple[bool, str]:
    data = json.dumps({"title": title, "body": body, "font": "sans"})
    result = subprocess.run(
        [
            "curl",
            "-s",
            "-X",
            "POST",
            "https://write.as/api/posts",
            "-H",
            "Content-Type: application/json",
            "-d",
            data,
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False, result.stdout[:200] or result.stderr[:200]
    if parsed.get("code") == 201:
        return True, f"https://write.as/{parsed['data']['id']}"
    return False, json.dumps(parsed)[:200]


def post_telegraph(title: str, body: str) -> Tuple[bool, str]:
    """Post article to Telegraph; returns (ok, url_or_error)."""
    r1 = subprocess.run(
        [
            "curl", "-s",
            "https://api.telegra.ph/createAccount?short_name=rwbot&author_name=RW+Marketing"
        ],
        capture_output=True, text=True, timeout=10,
    )
    try:
        account = json.loads(r1.stdout)
        if not account.get("ok"):
            return False, f"Account error: {account.get('error', r1.stdout[:50])}"
        token = account["result"]["access_token"]
    except Exception:
        return False, f"Token error: {r1.stdout[:100]}"

    # Convert markdown body to Telegraph-compatible HTML
    paragraphs = []
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("# "):
            paragraphs.append(f"<h3>{line[2:]}</h3>")
        elif line.startswith("## "):
            paragraphs.append(f"<h4>{line[3:]}</h4>")
        elif line.startswith("```"):
            paragraphs.append(f"<pre>{line[3:]}</pre>")
        else:
            paragraphs.append(f"<p>{line}</p>")

    content = "".join(paragraphs)

    params = urllib.parse.urlencode({
        "access_token": token,
        "title": title,
        "author_name": "RW Marketing",
        "content": content,
        "return_content": "false",
    })
    r2 = subprocess.run(
        [
            "curl", "-s", "-X", "POST",
            f"https://api.telegra.ph/createArticle?{params}",
        ],
        capture_output=True, text=True, timeout=15,
    )
    try:
        article = json.loads(r2.stdout)
        if article.get("ok"):
            return True, article["result"]["url"]
        return False, article.get("error", r2.stdout[:100])
    except Exception:
        return False, f"Article error: {r2.stdout[:100]}"


def find_todays_drafts(today: str) -> list[Path]:
    if not DRAFTS_DIR.exists():
        return []
    return sorted(DRAFTS_DIR.glob(f"{today}_*_draft.md"))


def main() -> int:
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    posted = load_posted()
    results: list[dict] = []

    todays_drafts = find_todays_drafts(today)
    if not todays_drafts:
        summary = {"timestamp": now.isoformat(), "results": [], "message": "No scheduled drafts for today."}
        print(json.dumps(summary, indent=2))
        return 0

    for draft in todays_drafts:
        raw = draft.read_text(encoding="utf-8")
        title, body, metadata = extract_title_and_body(raw)
        if len(body) < 300:
            results.append({"draft": draft.name, "ok": False, "status": "skipped_too_short"})
            continue

        draft_hash = digest_text(body)
        if already_posted_successfully(posted, draft_hash, "write.as"):
            results.append({
                "draft": draft.name,
                "ok": True,
                "status": "already_posted",
                "draft_hash": draft_hash,
                "experiment_id": metadata.get("experiment_id"),
            })
            continue

        # Append CTA footer — every post drives Codeberg primary adoption
        body_with_cta = body + CTA_FOOTER

        # Post to write.as
        ok_was, url_was = post_writeas(title, body_with_cta)
        record_was = {
            "date": today,
            "draft": draft.name,
            "title": title,
            "platform": "write.as",
            "ok": ok_was,
            "status": "posted" if ok_was else "failed",
            "url": url_was if ok_was else None,
            "error": None if ok_was else url_was,
            "draft_hash": draft_hash,
            "experiment_id": metadata.get("experiment_id"),
            "content_type": metadata.get("content_type"),
            "keyword": metadata.get("keyword"),
            "cta": metadata.get("cta"),
            "hypothesis": metadata.get("hypothesis"),
        }
        results.append(record_was)
        posted.setdefault("posts", []).append(record_was)

        # Also post to Telegraph (diversified distribution — was write.as-only)
        ok_tg, url_tg = post_telegraph(title, body_with_cta)
        record_tg = {
            "date": today,
            "draft": draft.name,
            "title": title,
            "platform": "telegraph",
            "ok": ok_tg,
            "status": "posted" if ok_tg else "failed",
            "url": url_tg if ok_tg else None,
            "error": None if ok_tg else url_tg,
            "draft_hash": draft_hash,
            "experiment_id": metadata.get("experiment_id"),
        }
        results.append(record_tg)
        posted.setdefault("posts", []).append(record_tg)

    posted["last_run"] = now.isoformat()
    save_posted(posted)

    log_file = LOG_DIR / f"posting_{today}.json"
    log_file.write_text(json.dumps({"timestamp": now.isoformat(), "results": results}, indent=2), encoding="utf-8")
    print(json.dumps({"timestamp": now.isoformat(), "results": results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
