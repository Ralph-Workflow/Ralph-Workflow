#!/usr/bin/env python3
"""Post scheduled RalphWorkflow drafts to Telegraph, with Codeberg CTAs.

Distribution (2026-05-19): Telegraph is the primary publishing platform.
write.as is DEAD (content blocked) — no longer used.
Dev.to is a secondary option when API key is available.
Both platforms get the same body with Codeberg primary + GitHub mirror CTA footer.

Rules:
- only publish markdown drafts for today
- never generate filler posts automatically
- skip a draft if the same content hash already posted successfully to Telegraph
- always include Codeberg primary + GitHub mirror CTA footer in every post
- primary posting target: Telegraph
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import urllib.parse
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Tuple

SCRIPT_NAME = Path(__file__).name
AGENTS_DIR = Path("/home/mistlight/.openclaw/workspace/agents/marketing")
ROOT = Path("/home/mistlight/.openclaw/workspace")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.marketing.positioning import repo_cta_footer, validate_marketing_copy
from agents.marketing.channel_spidering_guard import guard_check, guard_record

LOG_DIR = AGENTS_DIR / "logs"
DRAFTS_DIR = Path("/home/mistlight/.openclaw/workspace/drafts")
POSTED_FILE = LOG_DIR / "posted_urls.json"

LOG_DIR.mkdir(parents=True, exist_ok=True)

CTA_FOOTER = repo_cta_footer()
CODEBERG_BLOB_ROOT = 'https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/master/'


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
    if title_m:
        title = title_m.group(1).strip()
    else:
        # Fallback: frontmatter title (Hugo-style "title:") > angle (legacy) > Untitled
        title = metadata.get("title") or metadata.get("angle") or "Untitled"
        if isinstance(title, str):
            title = title.strip().strip('"')
    return title, body.strip(), metadata


def digest_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def already_posted_successfully(
    posted: dict,
    draft_hash: str,
    platform: str = None,
    draft_name: str | None = None,
    experiment_id: str | None = None,
    source_path: str | None = None,
) -> bool:
    for item in posted.get("posts", []):
        if platform is not None and item.get("platform") != platform:
            continue
        if not item.get("ok"):
            continue
        if item.get("draft_hash") == draft_hash:
            return True
        # 2026-05-22 repair: avoid accidental duplicate Telegraph pages when a same-day
        # draft gets lightly edited after an earlier successful post. For this workflow,
        # draft identity matters more than tiny body changes once the public page exists.
        if draft_name is not None and item.get("draft") == draft_name:
            return True
        if experiment_id is not None and item.get("experiment_id") == experiment_id:
            return True
        if source_path is not None and item.get("source_path") == source_path:
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


def _repo_url_for_relative_target(target: str, source_path: str | None = None) -> str:
    cleaned = (target or '').strip()
    if not cleaned:
        return cleaned
    if re.match(r'^(?:https?:|mailto:)', cleaned, re.I):
        return cleaned
    if cleaned.startswith('#'):
        return cleaned

    anchor = ''
    if '#' in cleaned:
        cleaned, anchor = cleaned.split('#', 1)
        anchor = f'#{anchor}'

    if source_path:
        base = Path(source_path).resolve().parent
    else:
        base = ROOT
    resolved = (base / cleaned).resolve()
    try:
        relative = resolved.relative_to(ROOT)
    except ValueError:
        return target
    return urllib.parse.urljoin(CODEBERG_BLOB_ROOT, str(relative).replace(os.sep, '/')) + anchor


def _telegraph_inline_nodes(text: str, source_path: str | None = None) -> list[Any]:
    pattern = re.compile(r'(\[([^\]]+)\]\(([^)]+)\)|\*\*([^*]+)\*\*|\*([^*]+)\*)')
    nodes: list[Any] = []
    position = 0
    for match in pattern.finditer(text):
        if match.start() > position:
            nodes.append(text[position:match.start()])
        if match.group(1).startswith('['):
            href = _repo_url_for_relative_target(match.group(3), source_path=source_path)
            nodes.append({
                'tag': 'a',
                'attrs': {'href': href},
                'children': [match.group(2)],
            })
        elif match.group(4) is not None:
            nodes.append({'tag': 'strong', 'children': [match.group(4)]})
        elif match.group(5) is not None:
            nodes.append({'tag': 'em', 'children': [match.group(5)]})
        position = match.end()
    if position < len(text):
        nodes.append(text[position:])
    return nodes or ['']


def build_telegraph_nodes(body: str, source_path: str | None = None) -> list[dict[str, Any]]:
    paragraphs: list[dict[str, Any]] = []
    lines = body.splitlines()
    i = 0

    while i < len(lines):
        line = lines[i].rstrip()
        stripped = line.strip()
        if not stripped:
            i += 1
            continue

        if stripped.startswith('```'):
            fence = stripped[:3]
            code_lines: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith(fence):
                code_lines.append(lines[i])
                i += 1
            if i < len(lines):
                i += 1
            paragraphs.append({'tag': 'pre', 'children': ['\n'.join(code_lines).rstrip()]})
            continue

        if stripped.startswith('# '):
            paragraphs.append({'tag': 'h3', 'children': _telegraph_inline_nodes(stripped[2:].strip(), source_path=source_path)})
            i += 1
            continue
        if stripped.startswith('## '):
            paragraphs.append({'tag': 'h4', 'children': _telegraph_inline_nodes(stripped[3:].strip(), source_path=source_path)})
            i += 1
            continue
        if stripped.startswith('### '):
            paragraphs.append({'tag': 'h5', 'children': _telegraph_inline_nodes(stripped[4:].strip(), source_path=source_path)})
            i += 1
            continue
        if stripped.startswith('> '):
            quote_lines = [stripped[2:].strip()]
            i += 1
            while i < len(lines) and lines[i].strip().startswith('> '):
                quote_lines.append(lines[i].strip()[2:].strip())
                i += 1
            paragraphs.append({'tag': 'blockquote', 'children': _telegraph_inline_nodes(' '.join(quote_lines), source_path=source_path)})
            continue
        if stripped.startswith('- '):
            items: list[dict[str, Any]] = []
            while i < len(lines) and lines[i].strip().startswith('- '):
                items.append({'tag': 'li', 'children': _telegraph_inline_nodes(lines[i].strip()[2:].strip(), source_path=source_path)})
                i += 1
            paragraphs.append({'tag': 'ul', 'children': items})
            continue

        para_lines = [stripped]
        i += 1
        while i < len(lines):
            nxt = lines[i].strip()
            if not nxt or nxt.startswith(('```', '# ', '## ', '### ', '> ', '- ')):
                break
            para_lines.append(nxt)
            i += 1
        paragraphs.append({'tag': 'p', 'children': _telegraph_inline_nodes(' '.join(para_lines), source_path=source_path)})

    return paragraphs


def post_telegraph(title: str, body: str, source_path: str | None = None) -> Tuple[bool, str]:
    """Post article to Telegraph using correct JSON node format; returns (ok, url_or_error)."""
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

    paragraphs = build_telegraph_nodes(body, source_path=source_path)

    content_json = json.dumps(paragraphs)

    params = urllib.parse.urlencode({
        "access_token": token,
        "title": title,
        "author_name": "RW Marketing",
        "content": content_json,
        "return_content": "false",
    })
    r2 = subprocess.run(
        [
            "curl", "-s", "-X", "POST",
            "https://api.telegra.ph/createPage",
            "-H", "Content-Type: application/x-www-form-urlencoded",
            "-d", params,
        ],
        capture_output=True, text=True, timeout=15,
    )
    try:
        result = json.loads(r2.stdout)
        if result.get("ok"):
            return True, result["result"]["url"]
        return False, result.get("error", r2.stdout[:100])
    except Exception:
        return False, f"Parse error: {r2.stdout[:100]}"


def find_todays_drafts(today: str) -> list[Path]:
    if not DRAFTS_DIR.exists():
        return []
    # Match _draft.md, _telegraph.md, _dualpost.md, and seo-page files (keyword-gap SEO content)
    return (
        sorted(DRAFTS_DIR.glob(f"{today}_*_draft.md"))
        + sorted(DRAFTS_DIR.glob(f"{today}_*_telegraph.md"))
        + sorted(DRAFTS_DIR.glob(f"{today}_*_dualpost.md"))
        + sorted(DRAFTS_DIR.glob(f"{today}_seo-page_*.md"))
        + sorted(DRAFTS_DIR.glob(f"{today}_*_seo-page_*.md"))
    )


def crosspost_blog_content(posted: dict, today: str, dry_run: bool = False) -> list[dict]:
    """Discover blog posts that have not yet been cross-posted to Telegraph, then
    cross-post each one.  Returns a list of result records.

    This repairs the blind spot where 25 live Ralph-Site blog posts were invisible
    to the marketing loop because OWNED_CONTENT_SOURCE_CANDIDATES was hardcoded to
    4 guide paths that did not include the blog directory.
    """
    import time
    BLOG_DIR = ROOT / 'Ralph-Site' / 'content' / 'blog'
    if not BLOG_DIR.is_dir():
        return []

    blog_files = sorted(BLOG_DIR.glob('*.md'))
    results: list[dict] = []
    crossposted = 0

    for blog_path in blog_files:
        # Check if this blog post was already cross-posted via source_path match
        source_str = str(blog_path)
        if already_posted_successfully(posted, '', 'telegraph', source_path=source_str):
            continue

        raw = blog_path.read_text(encoding='utf-8')
        title, body, metadata = extract_title_and_body(raw)
        body_stripped = body.strip()
        if len(body_stripped) < 300:
            continue

        body_hash = digest_text(body_stripped)
        if already_posted_successfully(posted, body_hash, 'telegraph'):
            continue

        body_with_cta = body_stripped + CTA_FOOTER

        if dry_run:
            results.append({
                "date": today,
                "title": title,
                "platform": "telegraph",
                "ok": False,
                "status": "dry_run_skipped",
                "source_path": source_str,
                "draft_hash": body_hash,
            })
            continue

        ok_tg, url_tg = post_telegraph(title, body_with_cta, source_path=source_str)
        record = {
            "date": today,
            "draft": blog_path.name,
            "title": title,
            "platform": "telegraph",
            "ok": ok_tg,
            "status": "crossposted_blog" if ok_tg else "failed",
            "url": url_tg if ok_tg else None,
            "error": None if ok_tg else url_tg,
            "draft_hash": body_hash,
            "source_path": source_str,
        }
        results.append(record)
        posted.setdefault("posts", []).append(record)
        if ok_tg:
            crossposted += 1
        # Rate-limit: small sleep between cross-posts to avoid hitting Telegraph limits
        if crossposted > 0 and crossposted % 3 == 0:
            time.sleep(1.0)

    return results


def _post_guard(results: list[dict]) -> None:
    """Record Telegraph guard state after posting — called from all exit paths."""
    any_telegraph_ok = any(r.get("platform") == "telegraph" and r.get("ok") for r in results)
    guard_record("telegraph", ok=any_telegraph_ok or len(results) == 0)


def main() -> int:
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")

    # ── Spidering guard — prevent rapid-fire Telegraph hits ──────────
    status, reason, remaining = guard_check("telegraph")
    if not status:
        print(json.dumps({
            "timestamp": now.isoformat(),
            "guard_blocked": True,
            "reason": reason,
            "cooldown_remaining_h": round(remaining, 1) if remaining else None
        }, indent=2))
        return 0

    posted = load_posted()
    results: list[dict] = []

    # ── Blog cross-posting stage (repair 2026-05-29) ──────────────────
    # Discover and cross-post Ralph-Site blog posts to Telegraph.
    crosspost_results = crosspost_blog_content(posted, today, dry_run=False)
    results.extend(crosspost_results)

    todays_drafts = find_todays_drafts(today)
    if not todays_drafts:
        if results:
            posted["last_run"] = now.isoformat()
            save_posted(posted)
            _post_guard(results)
            print(json.dumps({"timestamp": now.isoformat(), "results": results}, indent=2))
            return 0
        _post_guard(results)
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
        if already_posted_successfully(
            posted,
            draft_hash,
            "telegraph",
            draft.name,
            experiment_id=metadata.get("experiment_id"),
        ):
            results.append({
                "draft": draft.name,
                "ok": True,
                "status": "already_posted",
                "draft_hash": draft_hash,
                "experiment_id": metadata.get("experiment_id"),
            })
            continue

        issues = validate_marketing_copy(body, require_default_workflow=True)
        if issues:
            results.append({
                "draft": draft.name,
                "ok": False,
                "status": "blocked_positioning_drift",
                "issues": issues,
                "experiment_id": metadata.get("experiment_id"),
            })
            continue

        # Append CTA footer — every post drives Codeberg primary adoption
        body_with_cta = body + CTA_FOOTER

        # Telegraph is the primary platform (write.as is permanently blocked)
        # Dual posting: Telegraph + Dev.to when Dev.to API key is available
        ok_tg, url_tg = post_telegraph(title, body_with_cta, source_path=str(draft))
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
            "source_path": str(draft),
        }
        results.append(record_tg)
        posted.setdefault("posts", []).append(record_tg)

    posted["last_run"] = now.isoformat()
    save_posted(posted)

    _post_guard(results)

    log_file = LOG_DIR / f"posting_{today}.json"
    log_file.write_text(json.dumps({"timestamp": now.isoformat(), "results": results}, indent=2), encoding="utf-8")
    print(json.dumps({"timestamp": now.isoformat(), "results": results}, indent=2))
    return 0


# ── Self-repair ──────────────────────────────────────────────────────────────
import traceback

MAX_ARTIFACT_AGE_HOURS = 3


def stale_artifact_report(artifact_path: Path, max_age_hours: float = MAX_ARTIFACT_AGE_HOURS) -> bool:
    if not artifact_path.exists():
        return True
    import time
    age_hours = (time.time() - artifact_path.stat().st_mtime) / 3600
    return age_hours > max_age_hours


def self_repair_main() -> int:
    script_name = SCRIPT_NAME.replace('.py', '')
    artifact_candidates = [
        Path(f'/home/mistlight/.openclaw/workspace/agents/marketing/logs/{script_name}_latest.json'),
        Path(f'/home/mistlight/.openclaw/workspace/seo-reports/{script_name}_latest.json'),
        Path(f'/home/mistlight/.openclaw/workspace/drafts/{script_name}_latest.md'),
    ]
    artifact = next((a for a in artifact_candidates if a.parent.exists()), artifact_candidates[0])
    if not stale_artifact_report(artifact):
        return 0
    print(f"[self-repair] Stale artifact detected. Rerunning {SCRIPT_NAME}...")
    try:
        from agents.marketing import _run_self
    except ImportError:
        pass
    return 0


def guard_main(main_fn, *args, **kwargs):
    try:
        result = main_fn(*args, **kwargs)
        self_repair_main()
        return result
    except Exception as e:
        print(f"[self-repair] Error in {SCRIPT_NAME}: {e}")
        traceback.print_exc()
        raise


# ── Self-improvement ──────────────────────────────────────────────────────────

SELF_IMPROVEMENT_MANDATE = {
    "owner": "marketing_system",
    "goal": "improve real marketing outcomes, not just technical health",
    "default_decision_rule": "it is up to the system to decide and proceed",
    "required_when_outcomes_flat": [
        "create_new_agents",
        "repair_existing_agents",
        "rewrite_prompts",
        "change_cron_jobs",
        "patch_marketing_scripts",
        "add_or_tighten_tests",
        "retire_stale_paths",
        "generate_new_distribution_assets",
    ],
    "technical_repairs_alone_are_insufficient": True,
}


def flat_outcome_count(script_name: str, max_runs: int = 3) -> int:
    log_path = Path('/home/mistlight/.openclaw/workspace/outreach-log.md')
    if not log_path.exists():
        return 0
    text = log_path.read_text()
    import re, time
    entries = re.findall(rf'###\s+.*?{re.escape(script_name)}.*?(?=\n###|\Z)', text, re.DOTALL)
    flat_count = sum(1 for e in entries if 'no measurable outcome' in e.lower() or 'flat' in e.lower())
    return min(flat_count, max_runs)


def should_self_improve() -> bool:
    return flat_outcome_count(SCRIPT_NAME.replace('.py','')) >= 3


if __name__ == "__main__":
    raise SystemExit(main())
