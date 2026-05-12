#!/usr/bin/env python3
"""Post scheduled RalphWorkflow drafts to write.as and log outcomes.

Rules:
- only publish markdown drafts for today
- never generate filler posts automatically
- skip a draft if the same content hash already posted successfully
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Tuple

AGENTS_DIR = Path("/home/mistlight/.openclaw/workspace/agents/marketing")
LOG_DIR = AGENTS_DIR / "logs"
DRAFTS_DIR = Path("/home/mistlight/.openclaw/workspace/drafts")
POSTED_FILE = LOG_DIR / "posted_urls.json"

LOG_DIR.mkdir(parents=True, exist_ok=True)


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
        if already_posted_successfully(posted, draft_hash):
            results.append({
                "draft": draft.name,
                "ok": True,
                "status": "already_posted",
                "draft_hash": draft_hash,
                "experiment_id": metadata.get("experiment_id"),
            })
            continue

        ok, url_or_error = post_writeas(title, body)
        record = {
            "date": today,
            "draft": draft.name,
            "title": title,
            "platform": "write.as",
            "ok": ok,
            "status": "posted" if ok else "failed",
            "url": url_or_error if ok else None,
            "error": None if ok else url_or_error,
            "draft_hash": draft_hash,
            "experiment_id": metadata.get("experiment_id"),
            "content_type": metadata.get("content_type"),
            "keyword": metadata.get("keyword"),
            "cta": metadata.get("cta"),
            "hypothesis": metadata.get("hypothesis"),
        }
        results.append(record)
        posted.setdefault("posts", []).append(record)

    posted["last_run"] = now.isoformat()
    save_posted(posted)

    log_file = LOG_DIR / f"posting_{today}.json"
    log_file.write_text(json.dumps({"timestamp": now.isoformat(), "results": results}, indent=2), encoding="utf-8")
    print(json.dumps({"timestamp": now.isoformat(), "results": results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
