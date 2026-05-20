#!/usr/bin/env python3
"""
Channel Discovery — Each week, try 3 new platforms
Track what works, what fails, why.
"""
import json
import re
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime
from urllib.parse import urljoin, urlparse

AGENTS_DIR = "/home/mistlight/.openclaw/workspace/agents/marketing"
LOG_DIR = f"{AGENTS_DIR}/logs"
CHANNEL_LOG = f"{LOG_DIR}/channel_discovery.json"

CHANNELS_TO_TRY = [
    # Name, URL check, submission method, difficulty
    ("dev.to", "https://dev.to", "api", "hard"),  # needs API key
    ("stackoverflow", "https://stackoverflow.com", "answer", "medium"),  # answer questions
    ("quora", "https://quora.com", "answer", "medium"),  # answer questions
    ("reddit-r-programming", "https://reddit.com/r/rprogramming", "post", "hard"),  # needs karma
    ("reddit-programming", "https://reddit.com/r/programming", "post", "hard"),
    ("hackernews", "https://news.ycombinator.com", "submit", "impossible"),  # needs account
    ("lobsters", "https://lobste.rs", "submit", "impossible"),  # needs invite
    ("producthunt", "https://producthunt.com", "submit", "hard"),  # needs real product
    ("indiehackers", "https://indiehackers.com", "post", "medium"),  # web form
    ("medium", "https://medium.com", "article", "hard"),  # needs account
    ("dev.to-gist", "https://gist.github.com", "gist", "blocked"),  # PAT read-only
    ("slashdot", "https://slashdot.org/submission", "submit", "medium"),
    ("newsbrew", "https://newsbrew.io", "submit", "unknown"),
    ("toolshelf", "https://toolshelf.dev/submit", "submit", "easy"),
    ("toolwise", "https://toolwise.ai/submit-tool", "submit", "easy"),
    ("aitoolsindex", "https://aitoolsindex.org/submit", "submit", "easy"),
    ("codaone", "https://www.codaone.ai/submit/", "submit", "easy"),
    ("thenextai", "https://www.thenextai.com/submit-ai-tool/", "submit", "easy"),
    ("tools-ai-online", "https://www.tools-ai.online/submit-tool", "submit", "medium"),
    ("agentdepot", "https://agentdepot.dev/submit", "submit", "medium"),
    ("theresanaiforthat", "https://theresanaiforthat.com", "submit", "easy"),
    ("aisotools", "https://aisotools.com/submit", "submit", "easy"),
    ("comeai", "https://www.iatool.online/submit-tool/", "submit", "easy"),
    ("alternativeTo", "https://alternativeto.net", "submit", "blocked"),  # 403
    ("saashub", "https://saashub.com", "submit", "easy"),  # already listed
    ("productpapa", "https://productpapa.com", "submit", "unknown"),
    ("stackshare", "https://stackshare.io", "submit", "unknown"),
    ("github-readme", "https://github.com/Ralph-Workflow/Ralph-Workflow", "update", "blocked"),  # PAT read-only
    ("RSS directories", "https://blogsearch.google.com", "submit", "medium"),
    ("dmoz", "https://dmoz-odp.org", "submit", "blocked"),  # shut down
    ("dirwell", "https://dirwell.com", "submit", "unknown"),
    ("smashingmagazine", "https://smashingmagazine.com", "contribute", "hard"),
    ("css-tricks", "https://css-tricks.com", "article", "hard"),
]

RETIRED_CHANNELS = {
    "toolhunt": "parked domain / for sale",
    "toolhunter": "submit page is marketing copy with no usable form",
    "devpages": "submit flow is client-side success-only with no real submission",
}
ACTIVE_CHANNEL_NAMES = {name for name, *_ in CHANNELS_TO_TRY}

VALIDATED_AUTONOMOUS_SUBMIT_HOSTS = {
    "codaone.ai": {
        "submit_url": "https://www.codaone.ai/api/submit-tool",
        "note": "validated JSON submit endpoint accepts autonomous submissions from this environment",
    },
}

BROKEN_AUTONOMOUS_SUBMIT_HOSTS = {
    "aisotools.com": "real form submission with valid payload returns server misconfigured / 500 from this environment",
    "iatool.online": "real form submission with valid payload returns server error / 500 from this environment",
}


def _normalized_host(value):
    host = (urlparse(value or "").netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host



def classify_platform_response(url, final_url, code, body):
    body_l = (body or "").lower()
    final_url_l = (final_url or url or "").lower()
    original_host = _normalized_host(url)
    final_host = _normalized_host(final_url or url)
    final_path = (urlparse(final_url or url).path or "").lower()

    if code in {301, 302, 307, 308}:
        return "redirects", "redirect response"
    if code == 403:
        return "blocked", "Cloudflare or bot protection"
    if code == 401:
        return "login_required", "authentication required"
    if code == 404:
        return "missing", "page not found"
    if code == 0:
        return "error", "no HTTP response"

    if any(phrase in body_l for phrase in [
        "you must be logged in to submit",
        "logged in to submit",
        "log in to submit",
    ]) or ("login here" in body_l and "submit" in body_l):
        return "login_required", "submission requires login"
    if any(phrase in body_l for phrase in ["is for sale", "secure checkout", "buy this domain"]):
        return "parked", "domain is parked / for sale"
    if "login" in final_url_l and "submit" in (url or "").lower():
        return "login_required", "submission page redirected to login"
    if code == 200 and original_host and final_host and original_host != final_host and final_path in {"", "/"}:
        return "redirects", f"redirected away from original host to {final_host}"

    if code == 200:
        return "accessible", None
    return f"http_{code}", None


def submission_surface_needs_form_probe(url, method, body):
    if method != "submit":
        return False
    url_l = (url or "").lower()
    body_l = (body or "").lower()
    if "/submit" in url_l or "submit" in url_l:
        return True
    return any(phrase in body_l for phrase in [
        "submit a tool",
        "submit your tool",
        "fill out the form below",
        "know a great developer tool",
        "know a developer tool",
    ])


def fetch_page_source(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read(300000).decode("utf-8", errors="ignore")


def inspect_submission_client_code(url, html=None, max_scripts=12):
    try:
        html = html or fetch_page_source(url)
    except Exception as exc:
        return {"probe_status": "error", "note": str(exc)[:160]}

    script_paths = re.findall(r'<script[^>]+src="([^"]+)"', html, re.I)
    same_origin_scripts = []
    page_origin = urlparse(url).netloc.lower()
    for path in script_paths:
        absolute = urljoin(url, path)
        if urlparse(absolute).netloc.lower() == page_origin:
            same_origin_scripts.append(absolute)

    fetched_scripts = 0
    combined = html
    script_bodies = []
    for script_url in same_origin_scripts[:max_scripts]:
        try:
            script_body = fetch_page_source(script_url)
            script_bodies.append(script_body)
            combined += "\n" + script_body
            fetched_scripts += 1
        except Exception:
            continue

    combined_l = combined.lower()
    network_markers = [
        "fetch(",
        "axios",
        "xmlhttprequest",
        "sendbeacon",
        "formdata",
        "supabase",
        "/api/",
        "graphql",
        "mutation",
        "webhook",
        "formspree",
    ]
    success_markers = [
        "thank you!",
        "submission has been received",
        "we'll review it",
        "our team will review it",
        "tool submission has been received",
    ]

    handler_windows = []
    for body in script_bodies:
        body_l = body.lower()
        if "onsubmit" in body_l and "preventdefault" in body_l and ("submit a tool" in body_l or "tool name" in body_l):
            idx = body_l.find("onsubmit")
            handler_windows.append(body_l[max(0, idx - 1000): idx + 6000])
        elif "preventdefault" in body_l and ("submit a tool" in body_l or "tool name" in body_l):
            idx = body_l.find("preventdefault")
            handler_windows.append(body_l[max(0, idx - 1000): idx + 6000])

    handler_text = "\n".join(handler_windows)
    auth_markers = [
        "you need to be signed in to access this page",
        "already have an account? sign in",
        "don't have an account? sign up",
        "continue with github",
        "auth.signinwithoauth",
        "auth.signinwithpassword",
        "auth.signup",
    ]

    captcha_markers = [
        "recaptcha",
        "grecaptcha",
        "hcaptcha",
        "turnstile",
        "executeasync",
        "sitekey",
    ]

    api_candidates = []
    candidate_buckets = [handler_text, combined]
    for candidate_source in candidate_buckets:
        for pattern in [
            r'fetch\(["\']([^"\']+/api/[^"\']*)',
            r'fetch\(["\'](/api/[^"\']*)',
            r'axios\.(?:post|get|put)\(["\']([^"\']+/api/[^"\']*)',
            r'axios\.(?:post|get|put)\(["\'](/api/[^"\']*)',
            r'["\'](/api/[^"\']+)["\']',
        ]:
            for match in re.findall(pattern, candidate_source, re.I):
                candidate = urljoin(url, match)
                if candidate not in api_candidates:
                    api_candidates.append(candidate)

    api_candidates.sort(key=lambda candidate: (
        0 if any(token in candidate.lower() for token in ["submit", "tool", "listing"]) else 1,
        0 if "newsletter" not in candidate.lower() and "subscribe" not in candidate.lower() else 1,
        candidate,
    ))

    return {
        "probe_status": "ok",
        "script_count": len(script_paths),
        "same_origin_script_count": len(same_origin_scripts),
        "scripts_fetched": fetched_scripts,
        "has_network_submission_markers": any(marker in combined_l for marker in network_markers),
        "has_prevent_default": "preventdefault" in combined_l,
        "has_success_markers": any(marker in combined_l for marker in success_markers),
        "submit_handler_window_count": len(handler_windows),
        "submit_handler_has_network_markers": any(marker in handler_text for marker in network_markers),
        "submit_handler_has_success_markers": any(marker in handler_text for marker in success_markers),
        "has_auth_markers": any(marker in combined_l for marker in auth_markers),
        "has_captcha_markers": any(marker in combined_l for marker in captcha_markers),
        "has_mailto_submission_markers": "mailto:" in combined_l or "your email client should have opened" in combined_l,
        "has_issue_handoff_markers": "issues/new?template=" in combined_l or "open an issue, we'll merge it" in combined_l,
        "api_candidates": api_candidates,
    }


def probe_public_submit_api(url, candidate_urls=None):
    headers = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}
    submit_urls = []
    for candidate in (candidate_urls or []):
        absolute = urljoin(url, candidate)
        if absolute not in submit_urls:
            submit_urls.append(absolute)
    default_submit = urljoin(url, "/api/submit")
    if default_submit not in submit_urls:
        submit_urls.append(default_submit)

    def _request(method, target_url, payload=None):
        req = urllib.request.Request(target_url, data=payload, headers=headers if method == "POST" else {"User-Agent": "Mozilla/5.0"}, method=method)
        try:
            resp = urllib.request.urlopen(req, timeout=10)
            return resp.status, resp.read(2000).decode("utf-8", errors="ignore"), resp.getheader("Location")
        except urllib.error.HTTPError as e:
            return e.code, e.read(2000).decode("utf-8", errors="ignore"), e.headers.get("Location")

    best_result = None
    for submit_url in submit_urls:
        try:
            get_code, get_body, _ = _request("GET", submit_url)
        except Exception as exc:
            best_result = best_result or {"probe_status": "error", "note": str(exc)[:160]}
            continue

        try:
            payload = json.dumps({}).encode("utf-8")
            post_code, post_body, post_location = _request("POST", submit_url, payload=payload)
            final_submit_url = submit_url
            if post_code in {301, 302, 307, 308} and post_location:
                redirected_url = urljoin(submit_url, post_location)
                post_code, post_body, _ = _request("POST", redirected_url, payload=payload)
                final_submit_url = redirected_url
        except Exception as exc:
            candidate_result = {
                "probe_status": "partial",
                "submit_url": submit_url,
                "get_code": get_code,
                "get_body": get_body[:200],
                "note": str(exc)[:160],
            }
            best_result = best_result or candidate_result
            continue

        post_body_l = (post_body or "").lower()
        url_l = final_submit_url.lower()
        submit_like = any(token in url_l for token in ["submit", "tool", "listing"])
        public_validation_markers = [
            "required",
            "invalid",
            "error",
            "tool name",
            "description",
            "email",
            "website",
        ]
        is_public = submit_like and post_code in {200, 400, 422} and any(marker in post_body_l for marker in public_validation_markers)
        candidate_result = {
            "probe_status": "ok",
            "submit_url": final_submit_url,
            "get_code": get_code,
            "post_code": post_code,
            "post_body": post_body[:200],
            "public_submit_detected": is_public,
            "server_error_detected": submit_like and post_code >= 500,
        }
        if is_public:
            return candidate_result
        if submit_like and post_code >= 500:
            return candidate_result
        if best_result is None:
            best_result = candidate_result

    return best_result or {"probe_status": "error", "note": "no submit endpoint candidates responded"}


def probe_submission_surface(url):
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        return {"probe_status": "unavailable", "note": f"playwright unavailable: {exc}"}

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1600})
            page.goto(url, wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(1500)
            body_text = page.locator("body").inner_text(timeout=5000)
            forms = page.evaluate("""() => {
                return [...document.forms].map((form, index) => {
                    const controls = [...form.querySelectorAll('input, textarea, select')];
                    return {
                        index,
                        action: form.getAttribute('action'),
                        method: form.getAttribute('method'),
                        text: (form.innerText || '').slice(0, 300),
                        control_count: controls.length,
                        named_control_count: controls.filter((el) => el.getAttribute('name')).length,
                    };
                });
            }""")
            result = {
                "probe_status": "ok",
                "form_count": page.locator("form").count(),
                "input_count": page.locator("input").count(),
                "textarea_count": page.locator("textarea").count(),
                "select_count": page.locator("select").count(),
                "body_excerpt": body_text[:800],
                "forms": forms,
            }
            browser.close()
            return result
    except Exception as exc:
        return {"probe_status": "error", "note": str(exc)[:160]}


def classify_submission_surface_probe(probe, source_probe=None, api_probe=None, page_url=None):
    if not probe or probe.get("probe_status") != "ok":
        return None, None

    host = _normalized_host(page_url)
    validated_host = VALIDATED_AUTONOMOUS_SUBMIT_HOSTS.get(host)
    if validated_host:
        return "accessible", validated_host["note"]

    broken_host_note = BROKEN_AUTONOMOUS_SUBMIT_HOSTS.get(host)
    if broken_host_note:
        return "broken_submit_surface", broken_host_note

    control_count = (
        probe.get("form_count", 0)
        + probe.get("input_count", 0)
        + probe.get("textarea_count", 0)
        + probe.get("select_count", 0)
    )
    body_l = (probe.get("body_excerpt") or "").lower()
    if api_probe and api_probe.get("public_submit_detected"):
        return "accessible", f"public submission API detected at {api_probe.get('submit_url')}"

    if (
        api_probe
        and api_probe.get("server_error_detected")
        and control_count >= 4
    ):
        return "broken_submit_surface", (
            f"public submit endpoint detected at {api_probe.get('submit_url')} but it returned server error "
            f"{api_probe.get('post_code')} during autonomous submission probe"
        )

    if (
        source_probe
        and source_probe.get("has_mailto_submission_markers")
        and source_probe.get("has_issue_handoff_markers")
        and not (api_probe and api_probe.get("public_submit_detected"))
    ):
        return "manual_handoff_required", "submission falls back to email or GitHub issue handoff, not autonomous API/form submission"

    if (
        source_probe
        and source_probe.get("has_auth_markers")
        and control_count < 4
        and any(phrase in body_l for phrase in [
            "sign in",
            "sign up",
            "continue with github",
            "access this page",
        ])
    ):
        return "login_required", "submission requires account authentication"

    if (
        source_probe
        and source_probe.get("has_captcha_markers")
        and not (api_probe and api_probe.get("public_submit_detected"))
    ):
        return "captcha_blocked", "submission requires a CAPTCHA-backed browser flow, not an autonomous public API"

    if control_count == 0 and any(phrase in body_l for phrase in [
        "fill out the form below",
        "submit a tool",
        "submit your tool",
        "review it within 48 hours",
    ]):
        return "broken_submit_surface", "submission page loads marketing copy but no usable form controls"

    forms = probe.get("forms") or []
    primary_form = max(forms, key=lambda item: item.get("control_count", 0), default=None)
    if primary_form and source_probe and source_probe.get("probe_status") == "ok":
        if (
            primary_form.get("control_count", 0) >= 4
            and not primary_form.get("action")
            and primary_form.get("named_control_count", 0) == 0
            and source_probe.get("has_prevent_default")
            and source_probe.get("submit_handler_has_success_markers", source_probe.get("has_success_markers"))
            and not source_probe.get("submit_handler_has_network_markers", source_probe.get("has_network_submission_markers"))
        ):
            return "noop_submit_surface", (
                "submission UI appears client-side only: form has no action, controls have no names, "
                "success copy is embedded, and no network submission markers were found"
            )
    return None, None


def fetch_platform(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = resp.read(50000).decode("utf-8", errors="ignore")
        return resp.status, resp.geturl(), body

def check_platform(name, url, method, difficulty):
    """Test if a platform is accessible and what it would take to post."""
    result = {"name": name, "url": url, "method": method, "difficulty": difficulty, "timestamp": datetime.now().isoformat()}

    try:
        code, final_url, body = fetch_platform(url)
        status, note = classify_platform_response(url, final_url, code, body)
        result["http_code"] = str(code)
        result["final_url"] = final_url
        result["status"] = status
        if note:
            result["note"] = note
        if status == "accessible" and submission_surface_needs_form_probe(url, method, body):
            probe = probe_submission_surface(url)
            result["surface_probe"] = probe
            source_probe = inspect_submission_client_code(url, html=body)
            result["source_probe"] = source_probe
            api_probe = probe_public_submit_api(url, candidate_urls=source_probe.get("api_candidates") if source_probe else None)
            result["api_probe"] = api_probe
            probe_status, probe_note = classify_submission_surface_probe(probe, source_probe, api_probe, page_url=url)
            if probe_status:
                result["status"] = probe_status
                result["note"] = probe_note
    except urllib.error.HTTPError as e:
        body = e.read(4096).decode("utf-8", errors="ignore")
        status, note = classify_platform_response(url, e.geturl() or url, e.code, body)
        result["http_code"] = str(e.code)
        result["final_url"] = e.geturl() or url
        result["status"] = status
        if note:
            result["note"] = note
    except Exception as e:
        result["status"] = "error"
        result["note"] = str(e)[:80]

    return result

def try_stackoverflow_answer():
    """Try to find and answer a relevant Stack Overflow question."""
    # Search for questions about AI agents, unattended workflows
    search_url = "https://api.stackexchange.com/2.3/search/excerpts?order=desc&sort=relevance&q=AI%20agent%20workflow&site=stackoverflow&filter=withbody"
    
    try:
        r = subprocess.run(["curl", "-s", "--max-time", "10", search_url], capture_output=True, text=True, timeout=15)
        data = json.loads(r.stdout)
        if "items" in data and len(data["items"]) > 0:
            question = data["items"][0]
            return {
                "action": "answer",
                "question_id": question.get("question_id"),
                "title": question.get("title"),
                "link": question.get("link"),
                "score": question.get("score"),
                "status": "found_question"
            }
    except Exception as e:
        return {"action": "answer", "status": "error", "note": str(e)[:50]}
    
    return {"action": "answer", "status": "no_questions_found"}

def try_devto_api():
    """Check if we can post to dev.to via API (needs key)."""
    # Try reading published articles to see if account exists
    try:
        r = subprocess.run(["curl", "-s", "https://dev.to/api/articles?username=ralphworkflow"],
                         capture_output=True, text=True, timeout=10)
        data = json.loads(r.stdout)
        if isinstance(data, list) and len(data) > 0:
            return {"status": "has_articles", "count": len(data)}
        elif isinstance(data, dict) and data.get("error"):
            return {"status": "no_account", "error": data.get("error")}
    except Exception as e:
        return {"status": "error", "note": str(e)[:50]}
    return {"status": "unknown"}

def load_discovery_log():
    if os.path.exists(CHANNEL_LOG):
        with open(CHANNEL_LOG) as f:
            log = json.load(f)
    else:
        log = {"tried": [], "results": [], "working": []}

    log["tried"] = [name for name in log.get("tried", []) if name in ACTIVE_CHANNEL_NAMES]
    log["results"] = [entry for entry in log.get("results", []) if entry.get("name") in ACTIVE_CHANNEL_NAMES]
    log["working"] = [entry for entry in log.get("working", []) if entry.get("name") in ACTIVE_CHANNEL_NAMES]
    return log

import os

def save_discovery_log(log):
    with open(CHANNEL_LOG, "w") as f:
        json.dump(log, f, indent=2)


def latest_results_by_name(log):
    latest = {}
    for result in log.get("results", []):
        latest[result["name"]] = result
    return latest


def should_recheck_channel(channel_tuple, latest_result):
    name, _url, method, difficulty = channel_tuple
    if name in RETIRED_CHANNELS or not latest_result:
        return False
    if method != "submit" or difficulty not in {"easy", "medium"}:
        return False

    status = latest_result.get("status")
    note = (latest_result.get("note") or "").lower()
    if status in {"login_required", "broken_submit_surface", "noop_submit_surface", "captcha_blocked", "missing", "error"}:
        return True
    if any(marker in note for marker in ["captcha", "hcaptcha", "recaptcha", "turnstile", "authentication"]):
        return True
    return False


def build_working_channels(results):
    working = []
    for r in results:
        status = r.get("status")
        difficulty = r.get("difficulty")
        note = (r.get("note") or "").lower()
        final_url = (r.get("final_url") or "").lower()

        if status != "accessible" or difficulty not in ["easy", "medium"]:
            continue

        if any(marker in note for marker in ["captcha", "hcaptcha", "recaptcha", "turnstile", "login"]):
            continue
        if any(marker in final_url for marker in ["/login", "/register"]):
            continue

        working.append({
            "name": r["name"],
            "url": r["url"],
            "method": r["method"],
            "difficulty": r["difficulty"]
        })
    return working


def main():
    log = load_discovery_log()
    latest = latest_results_by_name(log)

    print(f"[Discovery] Running at {datetime.now().isoformat()}")
    print(f"[Discovery] Previously tried: {len(log['tried'])} channels")
    print(f"[Discovery] Working channels: {len(log.get('working', []))}")

    # Revalidate currently-working channels first so stale positives do not linger.
    working_recheck_names = [entry["name"] for entry in log.get("working", [])]
    working_recheck = [c for c in CHANNELS_TO_TRY if c[0] in working_recheck_names]

    # Also recheck previously-blocked easy/medium submit surfaces when detection improves.
    stale_recheck = [
        c for c in CHANNELS_TO_TRY
        if c[0] not in working_recheck_names and should_recheck_channel(c, latest.get(c[0]))
    ]

    # Then sample new channels.
    remaining = [
        c for c in CHANNELS_TO_TRY
        if c[0] not in log["tried"] and c[0] not in working_recheck_names and c[0] not in {x[0] for x in stale_recheck}
    ]
    to_try = (working_recheck + stale_recheck + remaining)[:5]

    print(f"\n[Discovery] Testing {len(to_try)} channels ({len(working_recheck) + len(stale_recheck)} rechecks):")

    for name, url, method, difficulty in to_try:
        print(f"\n  Testing {name}...", flush=True)

        result = check_platform(name, url, method, difficulty)
        print(f"    HTTP: {result.get('http_code', 'N/A')} | Status: {result.get('status', 'unknown')}")

        # Special checks
        if name == "stackoverflow":
            so_result = try_stackoverflow_answer()
            result["stackoverflow"] = so_result
            print(f"    SO: {so_result.get('status')}")

        elif name == "dev.to":
            dt_result = try_devto_api()
            result["devto"] = dt_result
            print(f"    dev.to: {dt_result.get('status')}")

        if name not in log["tried"]:
            log["tried"].append(name)
        latest[name] = result

        time.sleep(0.5)

    log["results"] = list(latest.values())
    log["working"] = build_working_channels(log["results"])
    
    # Save
    save_discovery_log(log)
    
    print(f"\n=== Discovery Results ===")
    print(f"Accessible: {[r['name'] for r in log['results'] if r.get('status') == 'accessible']}")
    print(f"Blocked: {[r['name'] for r in log['results'] if r.get('status') == 'blocked']}")
    print(f"Redirects: {[r['name'] for r in log['results'] if r.get('status') == 'redirects']}")

    if log['working']:
        print(f"\n✅ Actionable channels found:")
        for w in log['working']:
            print(f"  - {w['name']} ({w['method']}) — {w['difficulty']}")

if __name__ == "__main__":
    main()
