#!/usr/bin/env python3
"""
StackOverflow Answer DRAFTING Lane for Ralph Workflow.

Finds StackOverflow questions where Ralph Workflow provides a genuine answer,
drafts helpful non-promotional answers, and logs them for manual review + posting.

StackOverflow allows searching without an account (via Google/site search).
Posting requires a human account — this script ONLY DRAFTS ANSWER TEXT;
the answer is written to a handoff packet for manual placement. None of the
currently drafted answers have been manually posted. Treat this as a DRAFTING
lane, not an autonomous distribution channel.

Reclassified 2026-05-31: was misleadingly labeled "highest-leverage cold
distribution channel" in prior audit artifacts, but until a human posts at
least one answer and a conversion is measured, it is only a drafting pipeline.

Genuine advantages (if/when manually executed):
- Reaches developers at the exact problem-solving moment
- Not blocked by Cloudflare, captchas, or IP bans
- Question-answer format is natural and non-promotional
- Ralph Workflow's positioning maps directly onto common SO pain points

Truthful state as of 2026-05-31: 8 drafts exist, 0 have been posted by a human.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from html import unescape
from pathlib import Path
from urllib.parse import quote
from typing import Any

ROOT = Path("/home/mistlight/.openclaw/workspace")
AGENTS_DIR = ROOT / "agents" / "marketing"
LOG_DIR = AGENTS_DIR / "logs"
OUTREACH_LOG = ROOT / "outreach-log.md"
SO_LOG = LOG_DIR / "stackoverflow_answer_lane_latest.json"
DRAFT_DIR = ROOT / "drafts" / "stackoverflow"
DRAFT_DIR.mkdir(parents=True, exist_ok=True)
HANDOFF_PACKET_LATEST = ROOT / "drafts" / "stackoverflow_answer_handoff_packet_latest.md"
RECENT_DRAFT_LOOKBACK = timedelta(days=3)  # was 7 — reduced 2026-06-01: 7-day lookback blocked all results against fixed search specs
RATE_LIMIT_COOLDOWN = timedelta(hours=6)
# Hard cap on total unposted SO drafts — prevent infinite draft inflation when
# no human has ever posted one. The system owns marketing outcomes, not just
# activity; drafting more than this without a single placement is low-signal noise.
SO_DRAFT_CAP = 15

# Product truths for Ralph Workflow
RALPH_PRODUCT = {
    "name": "Ralph Workflow",
    "repo": "https://codeberg.org/RalphWorkflow/Ralph-Workflow",
    "github_mirror": "https://github.com/Ralph-Workflow/Ralph-Workflow",
    "site": "https://ralphworkflow.com",
    "tagline": "the operating system for autonomous coding",
    "what": "free and open-source composable loop framework and AI orchestrator",
    "who": "developers and technical teams doing ambitious software work",
    "why_now": "free and open source, strong default workflow, use as-is or build on top",
}

# Pain families that map to Ralph Workflow
SO_PAIN_TAGS = [
    "ai-programming",
    "artificial-intelligence",
    "autonomous",
    "claude-code",
    "openai-codex",
    "llm-coding",
    "ai-agent",
    "gpt-programming",
    "cursor-ide",
    "coding-assistant",
]

# SO_SEARCH_SPECS rewritten 2026-05-31: removed exact-phrase quotes that matched 0 real
# StackOverflow questions (verified against SE API: 9/10 old specs returned 0 results).
# New spec format: tag-filtered broad natural-language queries that match how real
# developers write on SO. Quota budget is 10 queries/day — every one must pay.
#
# Verified productive searches (live SE API test 2026-05-31):
#   [claude-code] autonomous → 1 result (280 views, real question)
#   [claude] or [opencode] or [aider] agent workflow review → 1 result (176 views)

SO_SEARCH_SPECS = [
    # ── tag-filtered: direct category targeting ──
    # q terms are kept short (1-3 keywords) to match natural SO question text.
    # Long phrase queries produce 0 results; the SE search engine matches literal text,
    # not semantic meaning. Add more terms via `tagged` and let the scoring/classify
    # functions filter for relevance downstream.
    {
        "title": "claude-code autonomous",
        "tagged": "claude-code",
        "label": "claude-code-autonomous",
        "q": "autonomous",
        "body": "autonomous agent workflow",
    },
    {
        "title": "claude-code workflow agent",
        "tagged": "claude-code",
        "label": "claude-code-workflow",
        "q": "workflow agent",
        "body": "workflow agent orchestration",
    },
    {
        "title": "ai coding agent reliable",
        "tagged": "artificial-intelligence",
        "label": "ai-coding-reliable",
        "q": "agent verification review",
        "body": "verification review reliable",
    },
    {
        "title": "ai dev workflow structure",
        "tagged": "artificial-intelligence",
        "label": "ai-dev-workflow",
        "q": "workflow structure",
        "body": "workflow structure maintainable",
    },
    {
        "title": "multi-agent testing",
        "tagged": "artificial-intelligence",
        "label": "multi-agent-testing",
        "q": "agent testing reflect",
        "body": "testing reflect iterate",
    },
    {
        "title": "coding agent checkpoint resume",
        "tagged": "artificial-intelligence",
        "label": "agent-handoff",
        "q": "checkpoint resume continue",
        "body": "checkpoint resume continue",
    },
    {
        "title": "autonomous coding complete",
        "tagged": "artificial-intelligence",
        "label": "autonomous-accept",
        "q": "autonomous complete",
        "body": "acceptance criteria complete",
    },
    {
        "title": "claude code subagent",
        "tagged": "claude-code",
        "label": "claude-subagent",
        "q": "subagent",
        "body": "subagent iterate",
    },
    {
        "title": "agent background overnight",
        "tagged": "artificial-intelligence",
        "label": "agent-background",
        "q": "overnight unattended",
        "body": "background overnight unattended",
    },
    {
        "title": "ai coding finish verify",
        "tagged": "openai-api",
        "label": "ai-finish-verify",
        "q": "agent finish verify",
        "body": "finish verify proof",
    },
    {
        "title": "codex opus agent orchestrator",
        "tagged": "openai-api",
        "label": "openai-orchestrator",
        "q": "agent orchestrator",
        "body": "orchestrator loop workflow",
    },
]

HIGH_INTENT_TERMS = [
    "acceptance criteria",
    "actually done",
    "agent",
    "artifact",
    "autonomous",
    "background",
    "checkpoint",
    "continue",
    "complete",
    "done",
    "finish",
    "handoff",
    "orchestrat",
    "overnight",
    "pipeline",
    "plan",
    "proof",
    "reconnect",
    "recover",
    "reflect",
    "reliable",
    "resume",
    "review",
    "session",
    "spec",
    "status",
    "subagent",
    "test",
    "trust",
    "unattended",
    "verification",
    "verify",
    "workflow",
    "wrapper",
    "iterate",
]

LOW_FIT_TERMS = [
    "api key",
    "billing",
    "cost",
    "cursor",
    "debug log",
    "exact billing",
    "extension",
    "javascript",
    "langchain",
    "langgraph",
    "pricing",
    "python only",
    "tokens",
    "ui",
    "vs code",
]

DISQUALIFYING_TERMS = [
    "api usage",
    "auth token",
    "billing metrics",
    "buy",
    "button does not appear",
    "credit card",
    "pricing tier",
    "quota",
    "rate limit",
    "visual studio",
]

STRONG_FIT_TERMS = [
    # Terms that real StackOverflow users write in AI-coding questions.
    # Must be a superset of the most common HIGH_INTENT_TERMS because
    # is_draft_worthy() uses this as a hard gate (strong_fit_hits > 0).
    # If a term is in HIGH_INTENT but not here, a question can score well
    # (score_question adds points) but still fail the draft-worthiness gate.
    "acceptance criteria",
    "actually done",
    "agent",
    "autonomous",
    "background",
    "checkpoint",
    "coding",
    "complete",
    "continue",
    "done",
    "finish",
    "handoff",
    "iterate",
    "orchestrat",
    "overnight",
    "pipeline",
    "plan",
    "proof",
    "recover",
    "reflect",
    "reliable",
    "resume",
    "review",
    "session",
    "spec",
    "subagent",
    "test",
    "trust",
    "unattended",
    "verification",
    "verify",
    "workflow",
    "wrapper",
]

SEARCH_RUNTIME: dict[str, Any] = {
    "rate_limited": False,
    "errors": [],
    "halted_after_rate_limit": False,
}


def _load_previous_lane_state() -> dict[str, Any]:
    if not SO_LOG.exists():
        return {}
    try:
        return json.loads(SO_LOG.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _parse_iso_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt


def _active_rate_limit_cooldown(previous: dict[str, Any], now: datetime) -> datetime | None:
    if not previous:
        return None
    next_retry_at = _parse_iso_dt(previous.get("next_retry_at"))
    if next_retry_at and next_retry_at > now:
        return next_retry_at
    return None


def _load_review_window_blocked_question_urls() -> set[str]:
    blocked: set[str] = set()
    board_path = ROOT / "drafts" / "marketing_execution_board_latest.md"
    board_text = board_path.read_text(encoding="utf-8") if board_path.exists() else ""
    manual_delivery_path = LOG_DIR / "marketing_2026-05-28_stackoverflow_manual_delivery.json"
    manual_delivery = _read_json(manual_delivery_path)

    board_blocks_current_packet = "StackOverflow demand-capture packet was already delivered for manual placement in the current review window" in board_text
    if board_blocks_current_packet:
        question_url = ((manual_delivery.get("packet") or {}).get("question_url"))
        if question_url:
            blocked.add(str(question_url).strip())

    return blocked


def _load_exhausted_question_urls() -> set[str]:
    exhausted: set[str] = _load_review_window_blocked_question_urls()
    lane_state = _load_previous_lane_state()
    audit = _read_json(LOG_DIR / "marketing_workflow_audit_latest.json")
    distribution = _read_json(LOG_DIR / "distribution_lane_latest.json")

    stackoverflow_packet_exhausted = any(
        "retire this packet" in str(reason).lower() or "post-cooldown stackoverflow slot already ran" in str(reason).lower()
        for reason in (distribution.get("reasons") or [])
    )
    stackoverflow_packet_exhausted = stackoverflow_packet_exhausted or any(
        "retire this packet" in str(reason).lower() or "post-cooldown stackoverflow slot already ran" in str(reason).lower()
        for reason in (audit.get("reasons") or [])
    )

    if not stackoverflow_packet_exhausted:
        return exhausted

    handoff_text = HANDOFF_PACKET_LATEST.read_text(encoding="utf-8") if HANDOFF_PACKET_LATEST.exists() else ""
    handoff_match = re.search(r"\*\*URL:\*\*\s+(https?://\S+)", handoff_text)
    if handoff_match:
        exhausted.add(handoff_match.group(1).strip())

    reused = lane_state.get("reused_existing_draft") or {}
    if reused.get("question_url"):
        exhausted.add(str(reused["question_url"]).strip())
    return exhausted


def gh_search_code(query: str, token: str = "") -> list[dict]:
    """Use GitHub API to search code for relevant patterns."""
    if not token:
        return []
    url = f"https://api.github.com/search/code?q={quote(query)}&per_page=5"
    req = urllib.request.Request(url, headers={
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json"
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            return data.get("items", [])
    except Exception:
        return []


def _stackexchange_get(path: str, params: dict[str, str]) -> dict:
    params = {**params, "site": "stackoverflow"}
    url = f"https://api.stackexchange.com/2.3{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; RalphWorkflowBot/1.0)",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def _search_results_from_items(items: list[dict[str, Any]], spec: dict[str, str], *, from_excerpts: bool = False) -> list[dict]:
    results = []
    for item in items:
        if from_excerpts and item.get("item_type") not in {None, "question"}:
            continue
        title = unescape(item.get("title", "")).strip()
        link = item.get("link", "")
        if not title or not link:
            continue
        excerpt = unescape(item.get("excerpt", "") or item.get("body", "") or "")
        excerpt = re.sub(r"<[^>]+>", "", excerpt).strip()
        results.append({
            "url": link,
            "title": title,
            "votes": item.get("question_score", item.get("score")),
            "answers": item.get("answer_count"),
            "query": spec.get("label") or spec.get("title", "query"),
            "question_id": item.get("question_id"),
            "tags": item.get("tags", []),
            "is_answered": item.get("is_answered", False),
            "body_snippet": excerpt[:500],
        })
    return results


def _search_excerpts_params(spec: dict[str, str]) -> dict[str, str]:
    params = {
        "pagesize": "10",
        "order": "desc",
        "sort": "relevance",
        "accepted": "False",
        "closed": "False",
    }
    q = spec.get("q", "")
    if q:
        params["q"] = q
    title = spec.get("title", "")
    if title:
        params["title"] = title
    body = spec.get("body", "")
    if body:
        params["body"] = body
    tagged = spec.get("tagged", "")
    if tagged:
        params["tagged"] = tagged
    return params


def _search_excerpts(spec: dict[str, str], label: str) -> list[dict]:
    params = _search_excerpts_params(spec)
    try:
        payload = _stackexchange_get("/search/excerpts", params)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            SEARCH_RUNTIME["rate_limited"] = True
        SEARCH_RUNTIME["errors"].append({"label": label, "code": e.code, "error": str(e), "path": "/search/excerpts"})
        print(f"  StackExchange excerpt search failed for {label}: {e}")
        return []
    except Exception as e:
        SEARCH_RUNTIME["errors"].append({"label": label, "code": getattr(e, "code", None), "error": str(e), "path": "/search/excerpts"})
        print(f"  StackExchange excerpt search failed for {label}: {e}")
        return []
    return _search_results_from_items(payload.get("items", []), spec, from_excerpts=True)


def so_search_site(spec: dict[str, str]) -> list[dict]:
    """Search StackOverflow via the official Stack Exchange API.

    Uses `q` (full-text search across title + body) as the primary search.
    `tagged` restricts to specific SO tags. `title_filter`, if present,
    additionally restricts to matching question titles.
    """
    q = spec.get("q", "").strip()
    tagged = spec.get("tagged", "").strip()
    title_filter = spec.get("title_filter", "").strip()

    params: dict[str, str] = {
        "pagesize": "10",
        "order": "desc",
        "sort": "relevance",
    }
    if q:
        params["q"] = q
    if tagged:
        params["tagged"] = tagged
    if title_filter:
        params["title"] = title_filter

    label = spec.get('label', spec.get('title', 'query'))
    try:
        payload = _stackexchange_get("/search/advanced", params)
    except urllib.error.HTTPError as e:
        if e.code == 400 and tagged:
            try:
                payload = _stackexchange_get("/search/advanced", {k: v for k, v in params.items() if k != "tagged"})
            except Exception as retry_error:
                retry_code = getattr(retry_error, "code", None)
                if retry_code == 429:
                    SEARCH_RUNTIME["rate_limited"] = True
                SEARCH_RUNTIME["errors"].append({"label": label, "code": retry_code, "error": str(retry_error), "path": "/search/advanced"})
                print(f"  StackExchange API search failed for {label}: {retry_error}")
                return []
        else:
            if e.code == 429:
                SEARCH_RUNTIME["rate_limited"] = True
            SEARCH_RUNTIME["errors"].append({"label": label, "code": e.code, "error": str(e), "path": "/search/advanced"})
            print(f"  StackExchange API search failed for {label}: {e}")
            return []
    except Exception as e:
        SEARCH_RUNTIME["errors"].append({"label": label, "code": getattr(e, "code", None), "error": str(e), "path": "/search/advanced"})
        print(f"  StackExchange API search failed for {label}: {e}")
        return []

    results = _search_results_from_items(payload.get("items", []), spec)
    if results and _results_include_live_candidate(results):
        return results

    fallback_results = _search_excerpts(spec, label)
    if fallback_results:
        print(f"  StackExchange excerpt fallback found {len(fallback_results)} result(s) for {label}")
    if not results:
        return fallback_results

    merged: dict[str, dict] = {result["url"]: result for result in results}
    for result in fallback_results:
        merged.setdefault(result["url"], result)
    return list(merged.values())


def fetch_question_detail(url: str) -> dict | None:
    """Fetch a single question's detail via the Stack Exchange API."""
    try:
        match = re.search(r'/questions/(\d+)', url)
        if not match:
            return None
        question_id = match.group(1)
        payload = _stackexchange_get(f"/questions/{question_id}", {
            "filter": "withbody",
        })
        items = payload.get("items", [])
        if not items:
            return None
        item = items[0]
        body_html = item.get("body", "")
        body = re.sub(r'<[^>]+>', '', unescape(body_html))[:500].strip()

        return {
            "url": url,
            "votes": item.get("score"),
            "answers": item.get("answer_count"),
            "body_snippet": body,
            "accepted_answer": item.get("accepted_answer_id") or "",
            "tags": item.get("tags", []),
            "fetched_at": datetime.now().isoformat(),
        }
    except Exception as e:
        print(f"  Failed to fetch {url}: {e}")
        return None


def classify_pain_family(question: dict, detail: dict | None = None) -> str:
    title = question.get("title", "")
    body = (detail.get("body_snippet", "") if detail else question.get("body_snippet", ""))[:300]
    text = (title + " " + body).lower()

    if "claude code" in text and any(k in text for k in ["wrapper", "continue", "subagent", "iterate", "reflect"]):
        return "workflow-orchestration"
    if any(k in text for k in ["review", "verify", "verification", "test", "check"]):
        return "verification-review"
    if any(k in text for k in ["autonomous", "unattended", "overnight", "background"]):
        return "unattended-runs"
    if any(k in text for k in ["workflow", "orchestrat", "pipeline", "chain"]):
        return "workflow-orchestration"
    if any(k in text for k in ["checkpoint", "resume", "reconnect", "recover", "session"]):
        return "session-recovery"
    if any(k in text for k in ["how do i know", "actually done", "finished", "complete", "finish"]):
        return "how-do-you-know-when-done"
    return "general"


def score_question(question: dict, detail: dict | None = None) -> float:
    """
    Score how well a question fits Ralph Workflow for answering.
    Higher = better fit for a Ralph Workflow answer.
    """
    score = 0.0
    title_lower = question.get("title", "").lower()
    body = (detail.get("body_snippet", "") if detail else "").lower()
    tags = [str(tag).lower() for tag in (question.get("tags") or detail.get("tags") or [])]
    text = title_lower + " " + body

    for term in HIGH_INTENT_TERMS:
        if term in text:
            score += 0.55

    for term in LOW_FIT_TERMS:
        if term in text:
            score -= 0.75

    for term in DISQUALIFYING_TERMS:
        if term in text:
            score -= 2.0

    for tag in tags:
        if tag in {"github-copilot", "openai-api", "openai-agents", "model-context-protocol", "langgraph", "langchain"}:
            score += 0.25

    pain_family = classify_pain_family(question, detail)
    if pain_family != "general":
        score += 1.0
    if pain_family in {"workflow-orchestration", "verification-review", "unattended-runs", "session-recovery"}:
        score += 0.6

    if "claude code" in text and any(term in text for term in ["wrapper", "continue", "iterate", "reflect", "subagent"]):
        score += 1.4

    # Questions with existing answers are HIGHER-SIGNAL: they're getting
    # traffic and the current answers are opportunities for better ones.
    # Only penalize if the answer is already accepted (harder to displace).
    if detail and detail.get("accepted_answer"):
        score -= 0.5
    if detail and detail.get("view_count", 0) > 50:
        score += 0.3
    if detail and detail.get("view_count", 0) > 200:
        score += 0.5
    if detail and detail.get("view_count", 0) > 1000:
        score += 0.8

    if detail and detail.get("votes") and detail["votes"] > 3:
        score += 0.5
    if detail and detail.get("votes") and detail["votes"] > 8:
        score += 0.75

    if any(term in title_lower for term in ["billing", "token counts", "price", "quota"]):
        score -= 2.5

    return round(score, 2)


def is_draft_worthy(question: dict) -> bool:
    score = float(question.get("score", 0) or 0)
    title = question.get("title", "").lower()
    body = question.get("body_snippet", "").lower()
    text = title + " " + body
    pain_family = question.get("pain_family") or classify_pain_family(question, question)
    strong_fit_hits = sum(1 for term in STRONG_FIT_TERMS if term in text)

    if any(term in text for term in DISQUALIFYING_TERMS):
        return False
    if pain_family == "general":
        return False
    if strong_fit_hits == 0:
        return False
    if question.get("accepted_answer") and question.get("answers", 0) > 0:
        return score >= 2.8 and strong_fit_hits >= 2
    if score >= 3.0 and strong_fit_hits >= 1:
        return True
    return score >= 2.0 and strong_fit_hits >= 1


def _results_include_live_candidate(results: list[dict[str, Any]], *, preview_limit: int = 3) -> bool:
    """Only skip excerpt fallback when the primary search already surfaced a live-fit lead."""
    for result in results[:preview_limit]:
        candidate = dict(result)
        detail = fetch_question_detail(candidate.get("url", "")) if candidate.get("url") else None
        if detail:
            candidate.update(detail)
        candidate["pain_family"] = classify_pain_family(candidate, candidate)
        candidate["score"] = score_question(candidate, candidate)
        if _is_live_lane_candidate(candidate):
            return True
    return False


def _is_live_lane_candidate(question: dict) -> bool:
    """Stop searching once we already have a strong unanswered candidate."""
    if not is_draft_worthy(question):
        return False
    if question.get("accepted_answer"):
        return False
    if int(question.get("answers", 0) or 0) > 1:
        return False
    return float(question.get("score", 0) or 0) >= 2.4


def draft_answer(question: dict, detail: dict) -> str:
    """
    Draft a genuinely helpful StackOverflow answer for this question.
    Ralph Workflow is mentioned naturally if it genuinely fits.
    """
    pain_family = classify_pain_family(question, detail)
    question["pain_family"] = pain_family
    title = question.get("title", "")
    body = (detail.get("body_snippet", "") or question.get("body_snippet", "")).lower()
    title_lower = title.lower()
    text = f"{title_lower} {body}"

    if "claude code" in text and "autonomous" in text and any(term in text for term in ["wrapper", "continue", "iterate", "reflect", "subagent"]):
        return """If the goal is "give it a high-level task and let it keep going until there is something real to review," I would stop looking for a single Claude Code flag and put it inside an outer workflow instead.

The pattern that tends to work is:

1. **Bound the task first.** Give it one repo-scoped objective with explicit acceptance criteria and non-goals.
2. **Run in phases, not one endless session.** Planning -> implementation -> verification -> review packet.
3. **Auto-continue only between phases.** Let the wrapper continue when the next step is mechanical, but stop if verification fails or the task leaves scope.
4. **Persist artifacts between loops.** Keep the spec, diff, test output, and finish state on disk so a timeout or interruption does not throw away the run.
5. **Treat "should I continue?" as a control-plane problem.** The model is surfacing uncertainty; the wrapper should decide whether the next move is safe based on the phase and evidence, not just blindly say yes forever.

So I would not optimize for "maximum uninterrupted runtime." I would optimize for "can it keep making bounded progress and end in something reviewable?"

Concretely, the useful ingredients are:

- a budget for retries / loop count
- a persisted task spec
- a verification gate (tests, build, lint, or whatever matches the task)
- a finish contract that produces a diff + check results instead of only a summary
- resume/checkpoint support so a long run can recover cleanly

That is basically the difference between an agent session and an unattended coding workflow.

If you want an open-source example of the outer-wrapper approach, Ralph Workflow is built around exactly that shape: explicit loops, checkpoints, verification, and a reviewable finish state rather than one monolithic chat run. Primary repo: [Codeberg](https://codeberg.org/RalphWorkflow/Ralph-Workflow)."""

    if "production reliability" in text and ("fintech" in text or "webhook" in text or "next.js" in text):
        return """For a TypeScript/Next.js fintech workflow, I would avoid agent-to-agent freeform handoffs and make the system event-driven with explicit contracts.

A practical production shape is:

1. **One orchestrator, many workers.** Keep planning/routing in one service, but execute work through queue-backed workers so retries and back-pressure are controlled instead of cascading.
2. **Per-step idempotency keys.** Every webhook, tool call, and downstream write should carry an idempotency key so retries are safe.
3. **State machine per job.** Persist states like `planned -> executing -> verifying -> awaiting-review -> done/failed` in the database instead of inferring state from logs or chat history.
4. **Outbox + audit trail.** Write domain changes and emitted events atomically, then fan out from the outbox. That prevents "business write succeeded but event publish failed" drift.
5. **Separate verification from execution.** The worker that changes code or data should not be the only thing deciding the result is correct. Run tests, schema checks, policy checks, and risk checks as a distinct phase.
6. **Human-readable review packet.** The terminal artifact should be a diff/change summary, checks that ran, failed retries, and any operator decisions still needed.

For your specific concerns:

- **Prevent cascading failures:** isolate agents behind queues and timeouts; never let one agent call another synchronously in a chain for critical paths.
- **Agent communication:** pass structured job payloads and artifacts, not conversational state.
- **Retries/idempotency:** retry transport failures automatically, but require explicit compensating actions for side-effecting fintech operations.
- **Observability:** log one correlation ID across webhook receipt, orchestration, tool execution, and verification.
- **Safe rollout:** ship prompt/workflow changes behind versioned configs and canary them on a small traffic slice before promoting.

If you want a concrete open-source reference for the "spec -> execution -> verification -> reviewable finish state" part of this pattern, Ralph Workflow is a useful example: [Codeberg](https://codeberg.org/RalphWorkflow/Ralph-Workflow)."""

    answers = {
        "how-do-you-know-when-done": """Treat \"done\" as a release gate, not a model statement.

The pattern that works best is:

1. **Write acceptance checks up front.** Keep them concrete: tests passed, migration applied, docs updated, build green, diff limited to agreed files.
2. **Split execution from verification.** The agent that wrote the code should not be the only thing deciding whether it is correct.
3. **Require inspectable artifacts.** A diff, test output, build logs, screenshots, or benchmark output are stronger completion signals than a summary.
4. **Fail closed.** If verification is missing or ambiguous, the run is incomplete.

In practice, most false positives come from letting the loop end on \"I finished\" instead of \"here is the evidence that the task passed its checks.\"""",

        "verification-review": """For production use, make verification a separate phase with its own inputs and outputs.

Practical structure:

1. **Planner step** defines scope and acceptance criteria.
2. **Execution step** changes code only within that scope.
3. **Verification step** runs tests/build/lint/integration checks and compares the result to the original acceptance criteria.
4. **Review step** packages the evidence: diff, commands run, outputs, and any unresolved risks.

That separation matters because self-verification is weak. If the same loop writes code and grades it, you tend to get optimistic results. A better contract is: no passing verification output, no completion.""",

        "unattended-runs": """For unattended runs in a production codebase, reliability usually comes from narrowing the contract rather than making the agent more autonomous.

The architecture I would use is:

1. **Small task envelope** — one ticket-sized change, clear file boundaries, explicit non-goals.
2. **Checkpointed phases** — spec -> implementation -> verification -> review package.
3. **Idempotent recovery** — if a session dies, resume from the last artifact, not from memory.
4. **Independent verification** — run tests/build/lint after implementation and block completion if any required check fails.
5. **Human-readable finish state** — when you wake up you should see: what changed, what passed, what failed, and whether it is safe to merge.

For a TypeScript/Next.js fintech stack, I would also add strict guardrails: no schema or payment-flow changes without targeted tests, no secret/config changes outside allowlisted files, and a hard stop on flaky or skipped checks.

One open-source example of this pattern is Ralph Workflow, which keeps Codeberg as the primary repo: [Codeberg](https://codeberg.org/RalphWorkflow/Ralph-Workflow).""",

        "workflow-orchestration": """The main reliability mistake is treating planning, coding, and verification as one continuous chat loop.

For production work, separate them into explicit stages:

- **Planning:** turn the request into a bounded spec with acceptance criteria.
- **Execution:** make the code changes against that spec.
- **Verification:** run the required checks and collect artifacts.
- **Review packaging:** produce a concise handoff with the diff, commands run, outputs, and known risks.

That gives you three benefits:

1. Scope stays stable during execution.
2. Verification has a clear contract.
3. Recovery is easier because each stage leaves an artifact behind.

If you keep everything in one loop, failures blur together and you end up babysitting. If you make each phase explicit, the system becomes much easier to trust and debug.""",

        "session-recovery": """Session recovery gets easier if you persist artifacts, not just conversation state.

What to persist after each phase:

- the current spec / acceptance criteria
- the latest diff or patch
- test/build output
- the exact phase the run reached
- any blockers or failed checks

Then resumption becomes deterministic: reload the last artifact, rerun verification if needed, and continue from the failed phase instead of reconstructing the whole session.

That is usually more robust than trying to preserve one giant agent conversation indefinitely.""",

        "general": """A good default is to treat AI work like any other production workflow:

- define acceptance criteria before execution
- keep the task small enough to verify
- run verification separately from generation
- require visible artifacts instead of trusting summaries

Most reliability problems show up when those boundaries are missing.""",
    }

    answer = answers.get(pain_family, answers["general"])

    return answer


def append_outreach_log(text: str) -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    block = f"\n### StackOverflow answer lane\n- **When:** {stamp}\n- **Note:** {text}\n"
    existing = OUTREACH_LOG.read_text(encoding="utf-8") if OUTREACH_LOG.exists() else "# Outreach Log\n"
    OUTREACH_LOG.write_text(existing.rstrip() + "\n" + block, encoding="utf-8")


def _meaningful_outreach_activity(*, drafts_created: int, reused_existing_draft: dict[str, Any] | None = None) -> bool:
    return drafts_created > 0 or reused_existing_draft is not None


def load_recent_drafted_question_urls(now: datetime | None = None) -> set[str]:
    now = now or datetime.now()
    recent_urls: set[str] = set()
    for draft_file in DRAFT_DIR.glob('so_answer_*.md'):
        try:
            modified_at = datetime.fromtimestamp(draft_file.stat().st_mtime)
        except OSError:
            continue
        if now - modified_at > RECENT_DRAFT_LOOKBACK:
            continue
        try:
            text = draft_file.read_text(encoding='utf-8')
        except OSError:
            continue
        match = re.search(r'^\*\*URL:\*\*\s+(https?://\S+)$', text, re.MULTILINE)
        if match:
            recent_urls.add(match.group(1).strip())
    return recent_urls


def find_recent_draft_for_url(url: str, now: datetime | None = None) -> Path | None:
    now = now or datetime.now()
    for draft_file in sorted(DRAFT_DIR.glob('so_answer_*.md'), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            modified_at = datetime.fromtimestamp(draft_file.stat().st_mtime)
        except OSError:
            continue
        if now - modified_at > RECENT_DRAFT_LOOKBACK:
            continue
        try:
            text = draft_file.read_text(encoding='utf-8')
        except OSError:
            continue
        match = re.search(r'^\*\*URL:\*\*\s+(https?://\S+)$', text, re.MULTILINE)
        if match and match.group(1).strip() == url:
            return draft_file
    return None


def refresh_handoff_packet_from_draft(question: dict, draft_path: Path, now: datetime | None = None) -> Path:
    now = now or datetime.now()
    text = draft_path.read_text(encoding='utf-8')
    answer = text.split('\n---\n\n', 1)[1].strip() if '\n---\n\n' in text else text.strip()
    packet = f"""# Ralph Workflow StackOverflow Answer Handoff Packet
Generated: {now.isoformat()}

## Why this is still the live answer lane
- The same high-intent question is still the strongest qualified StackOverflow target in the current window.
- A recent polished answer already exists, so the right move is to reuse the proven asset instead of generating duplicate draft churn.
- Codeberg remains the primary repo CTA.

## Target
- **Question:** {question.get('title', 'Unknown question')}
- **URL:** {question.get('url', '')}
- **Current score:** {question.get('score', '?')}
- **Current answers:** {question.get('answers', '?')}
- **Reused draft:** `{draft_path}`

## Final answer text
```md
{answer}
```

## Outcome contract
- Expected outcome: one live StackOverflow-compatible placement or manual reuse that sends qualified evaluators to Codeberg first.
- Replacement condition: if this exact packet still has no placement path by the next review window, switch the lane instead of regenerating the same answer again.
"""
    HANDOFF_PACKET_LATEST.write_text(packet, encoding='utf-8')
    return HANDOFF_PACKET_LATEST


def main() -> int:
    started_at = datetime.now()
    print(f"[SO Answer Lane] Starting at {started_at.isoformat()}")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    SEARCH_RUNTIME["rate_limited"] = False
    SEARCH_RUNTIME["errors"] = []
    SEARCH_RUNTIME["halted_after_rate_limit"] = False

    previous = _load_previous_lane_state()

    # ── Search-space exhaustion pre-check (2026-06-06) ──
    # If the previous run already detected that every draft-worthy question was
    # recently drafted (search_space_exhausted=true) AND the previous run was
    # within RECENT_DRAFT_LOOKBACK, the question pool has not refreshed.  Skip
    # the full search pipeline to avoid burning SE API quota on a guaranteed
    # zero-output run.  The cron fires daily but the search space refreshes
    # only when questions leave the lookback window (currently 3 days).
    prev_exhausted = previous.get("search_space_exhausted", False)
    prev_generated_at = _parse_iso_dt(previous.get("generated_at"))
    if prev_exhausted and prev_generated_at is not None:
        exhaustion_age = started_at - prev_generated_at
        if exhaustion_age < RECENT_DRAFT_LOOKBACK:
            preserved = dict(previous)
            preserved["generated_at"] = started_at.isoformat()
            preserved["status"] = "search_space_exhausted_reused_previous"
            preserved["search_space_exhausted"] = True
            preserved["exhaustion_bypassed_reason"] = (
                f"Search space was exhausted {exhaustion_age.total_seconds()/3600:.1f}h ago "
                f"(< {RECENT_DRAFT_LOOKBACK.days}d lookback). Skipping full SE API search "
                f"to preserve quota — no new questions can appear until existing drafts "
                f"age out of the {RECENT_DRAFT_LOOKBACK.days}-day window."
            )
            SO_LOG.write_text(json.dumps(preserved, indent=2), encoding="utf-8")
            print(
                f"[SO Answer Lane] Search space still exhausted (last run {exhaustion_age.total_seconds()/3600:.1f}h ago "
                f">< {RECENT_DRAFT_LOOKBACK.days}d lookback). Skipping full pipeline to preserve SE API quota."
            )
            return 0

    # Draft cap guard (2026-05-31): halt drafting when unposted draft count ≥ SO_DRAFT_CAP.
    # Prevents infinite draft inflation when no human has ever placed a single answer.
    draft_file_paths = sorted(DRAFT_DIR.glob('so_answer_*.md'), key=lambda p: p.stat().st_mtime)
    if len(draft_file_paths) >= SO_DRAFT_CAP:
        preserved = dict(previous)
        preserved['generated_at'] = started_at.isoformat()
        preserved['status'] = 'draft_cap_halted'
        preserved['total_unposted_drafts'] = len(draft_file_paths)
        preserved['draft_cap'] = SO_DRAFT_CAP
        preserved['draft_cap_reason'] = (
            f'{len(draft_file_paths)} unposted drafts exist (cap={SO_DRAFT_CAP}) '
            'and 0 have ever been posted by a human. Drafting more answers without '
            'a single placement is low-signal activity — halt until a human posts at '
            'least one draft or the cap is raised with placement proof.'
        )
        SO_LOG.write_text(json.dumps(preserved, indent=2), encoding='utf-8')
        print(
            f'[SO Answer Lane] Halted: {len(draft_file_paths)} unposted drafts hit the '
            f'SO_DRAFT_CAP ({SO_DRAFT_CAP}). Post at least one existing draft before '
            'drafting more. See stackoverflow_answer_lane_latest.json for the full state.'
        )
        return 0

    cooldown_until = _active_rate_limit_cooldown(previous, started_at)
    if cooldown_until:
        preserved = dict(previous)
        preserved["generated_at"] = started_at.isoformat()
        preserved["status"] = "rate_limit_cooldown_reused_previous"
        preserved["rate_limited"] = True
        preserved["cooldown_active"] = True
        preserved["next_retry_at"] = cooldown_until.isoformat()
        SO_LOG.write_text(json.dumps(preserved, indent=2), encoding="utf-8")
        print(
            "[SO Answer Lane] Active Stack Exchange cooldown until "
            f"{cooldown_until.isoformat()}; preserving previous lane state instead of burning the quota window."
        )
        return 0

    all_questions: list[dict] = []
    recent_draft_urls = load_recent_drafted_question_urls(now=started_at)
    exhausted_question_urls = _load_exhausted_question_urls()

    if exhausted_question_urls:
        print(
            "[SO Answer Lane] Retiring exhausted StackOverflow candidate(s) for this review window: "
            + ", ".join(sorted(exhausted_question_urls))
        )

    print("[SO Answer Lane] Searching StackOverflow...")
    for spec in SO_SEARCH_SPECS:
        results = so_search_site(spec)
        all_questions.extend(results)
        print(f"  Query '{spec.get('label', spec.get('title', 'query'))}': found {len(results)} results")
        if SEARCH_RUNTIME["rate_limited"]:
            SEARCH_RUNTIME["halted_after_rate_limit"] = True
            print("[SO Answer Lane] Hit Stack Exchange rate limiting; stopping further queries to preserve the quota window.")
            break

        strong_candidate_found = False
        for result in results[:3]:
            if result["url"] in exhausted_question_urls:
                continue
            detail = fetch_question_detail(result["url"])
            if detail:
                result.update(detail)
            result["pain_family"] = classify_pain_family(result, result)
            result["score"] = score_question(result, result)
            if _is_live_lane_candidate(result):
                strong_candidate_found = True
                break
            time.sleep(0.5)

        if strong_candidate_found:
            print("[SO Answer Lane] Found a strong reusable candidate early; preserving remaining API quota for the live attempt.")
            break
        time.sleep(1)

    # Dedupe by URL
    seen: dict[str, dict] = {}
    for q in all_questions:
        if q["url"] not in seen:
            seen[q["url"]] = q
    questions = [q for q in seen.values() if q["url"] not in exhausted_question_urls]
    print(f"[SO Answer Lane] Total unique questions: {len(questions)}")

    if not questions and SEARCH_RUNTIME["rate_limited"]:
        preserved = dict(previous)
        preserved["generated_at"] = started_at.isoformat()
        preserved["status"] = "rate_limited_reused_previous"
        preserved["rate_limited"] = True
        preserved["cooldown_active"] = True
        preserved["next_retry_at"] = (started_at + RATE_LIMIT_COOLDOWN).isoformat()
        preserved["search_errors"] = SEARCH_RUNTIME["errors"][-5:]
        SO_LOG.write_text(json.dumps(preserved, indent=2), encoding="utf-8")
        append_outreach_log(
            "StackOverflow answer lane hit Stack Exchange rate limiting; preserved the prior lane state instead of overwriting it with a fake zero-opportunity result."
        )
        print(f"[SO Answer Lane] Preserved previous lane state at {SO_LOG} after rate limiting")
        return 0

    # Score and rank
    scored: list[tuple[float, dict]] = []
    for q in questions:
        detail = fetch_question_detail(q["url"])
        if detail:
            q.update(detail)
        score = score_question(q, detail)
        q["score"] = score
        scored.append((score, q))
        time.sleep(0.5)

    scored.sort(key=lambda x: x[0], reverse=True)
    top_questions = [q for _, q in scored[:8]]

    print(f"[SO Answer Lane] Top scored questions:")
    for i, q in enumerate(top_questions):
        detail_str = ""
        if q.get("votes"):
            detail_str = f" | votes={q['votes']} answers={q.get('answers', '?')}"
        print(f"  {i+1}. [{q['score']:.1f}] {q['title'][:80]}{detail_str}")

    # Draft answers only for genuinely high-intent questions
    drafts: list[dict] = []
    reused_existing_draft: dict[str, Any] | None = None
    skipped_existing_drafts = 0
    search_space_exhausted = False  # 2026-06-01: detect when all high-fit results are re-drafts
    for q in top_questions:
        q["pain_family"] = classify_pain_family(q, q)
        if not is_draft_worthy(q):
            print(f"  Skipping {q['url']} — low-fit for Ralph Workflow despite search match")
            continue
        if q["url"] in recent_draft_urls:
            skipped_existing_drafts += 1
            if reused_existing_draft is None:
                existing_draft = find_recent_draft_for_url(q["url"], now=started_at)
                if existing_draft is not None:
                    packet_path = refresh_handoff_packet_from_draft(q, existing_draft, now=started_at)
                    reused_existing_draft = {
                        "question_title": q["title"],
                        "question_url": q["url"],
                        "draft_file": str(existing_draft),
                        "packet_file": str(packet_path),
                        "question_score": q.get("score"),
                        "pain_family": q.get("pain_family", "general"),
                    }
            print(f"  Skipping {q['url']} — already drafted recently; do not count duplicate draft churn as progress")
            continue

        answer = draft_answer(q, q)
        slug = q["url"].split("/")[-1][:50]
        draft_file = DRAFT_DIR / f"so_answer_{datetime.now().strftime('%Y-%m-%d')}_{slug}.md"
        draft_file.write_text(f"# StackOverflow Answer Draft\n\n**Question:** {q['title']}\n**URL:** {q['url']}\n**Score:** {q.get('score', '?')}\n**Answers:** {q.get('answers', '?')}\n\n---\n\n{answer}", encoding="utf-8")

        drafts.append({
            "question_title": q["title"],
            "question_url": q["url"],
            "question_score": q.get("score"),
            "draft_file": str(draft_file),
            "answer_length": len(answer),
            "pain_family": q.get("pain_family", "general"),
        })
        print(f"  Drafted: {draft_file.name}")

    # 2026-06-01: Detect search-space exhaustion — if every draft-worthy question was skipped
    # as already-drafted, the fixed search specs have exhausted the current question pool.
    # Signal so cron cadence can adapt.
    draft_worthy_count = sum(1 for q in top_questions if is_draft_worthy(q))
    if draft_worthy_count > 0 and draft_worthy_count == skipped_existing_drafts:
        search_space_exhausted = True
        print("[SO Answer Lane] Search space exhausted — all draft-worthy questions are recently drafted. Broadening search may help.")

    # Save results
    result = {
        "generated_at": datetime.now().isoformat(),
        "status": (
            "ok_with_rate_limit_warnings"
            if SEARCH_RUNTIME["rate_limited"]
            else "manual_ready_follow_through" if reused_existing_draft and not drafts else "ok"
        ),
        "rate_limited": SEARCH_RUNTIME["rate_limited"],
        "cooldown_active": SEARCH_RUNTIME["rate_limited"],
        "next_retry_at": (started_at + RATE_LIMIT_COOLDOWN).isoformat() if SEARCH_RUNTIME["rate_limited"] else None,
        "search_errors": SEARCH_RUNTIME["errors"][-5:],
        "halted_after_rate_limit": SEARCH_RUNTIME["halted_after_rate_limit"],
        "total_questions_found": len(questions),
        "questions_scored": len(scored),
        "drafts_created": len(drafts),
        "skipped_existing_drafts": skipped_existing_drafts,
        "search_space_exhausted": search_space_exhausted,
        "reused_existing_draft": reused_existing_draft,
        "manual_follow_through": bool(reused_existing_draft and not drafts),
        "top_questions": [
            {
                "title": q["title"],
                "url": q["url"],
                "score": q.get("score", 0),
                "votes": q.get("votes"),
                "answers": q.get("answers"),
            }
            for q in top_questions
        ],
        "drafts": drafts,
        "exhausted_question_urls": sorted(exhausted_question_urls),
    }

    SO_LOG.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"[SO Answer Lane] Results: {SO_LOG}")
    print(f"[SO Answer Lane] Drafts: {len(drafts)} answer drafts created")

    if _meaningful_outreach_activity(drafts_created=len(drafts), reused_existing_draft=reused_existing_draft):
        append_outreach_log(
            f"StackOverflow answer lane ran: found {len(questions)} questions, "
            f"scored {len(scored)}, drafted {len(drafts)} answers, "
            f"skipped {skipped_existing_drafts} recent duplicate candidates"
            f"{'; refreshed canonical handoff packet from the best existing draft for manual-ready follow-through' if reused_existing_draft else ''}. "
            f"Top question: {top_questions[0]['title'][:80] if top_questions else 'none'}."
        )
    else:
        print('[SO Answer Lane] No fresh answer draft created and no reusable manual-ready packet was refreshed; skipping outreach-log append to avoid fake progress')

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
