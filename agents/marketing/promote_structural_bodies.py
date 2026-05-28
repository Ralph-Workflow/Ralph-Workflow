#!/usr/bin/env python3
"""Promote validated structural Reddit bodies into the posting pipeline.

Run: python3 promote_structural_bodies.py [--force-refresh]
Output: writes to reddit_posts.jsonl so build_comment() treats them as recent history

The core fix: the old template bank in build_comment() produces bodies with the same
4-paragraph cadence. The structural bodies break that cadence with genuinely different
paragraph structures. By injecting them into the post log, the next build_comment() call
will see them as recent candidates and avoid repeating the same cadence.

Staleness recovery: when --force-refresh is set or all bodies score 0.0 freshness,
re-generates structural bodies first before re-scoring. This prevents the system from
getting stuck when the entire body pool is freshness-blocked.

Usage:
  python3 reddit_structural_bodies.py  # generate fresh structural bodies
  python3 promote_structural_bodies.py  # inject them into the posting pipeline
  python3 promote_structural_bodies.py --force-refresh  # regenerate + inject
"""
from datetime import datetime, timezone
import json
import random
import subprocess
import sys
from pathlib import Path

ROOT = Path("/home/mistlight/.openclaw/workspace")
STRUCTURAL_FILE = ROOT / "agents/marketing/logs/reddit_structural_bodies.json"
OUTPUT_FILE = ROOT / "agents/marketing/logs/reddit_next_window_bodies.json"
POST_LOG = ROOT / "agents/marketing/logs/reddit_posts.jsonl"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_recent_openings(n: int = 6) -> list[str]:
    """Get recent openings from the post log."""
    openings = []
    if POST_LOG.exists():
        with open(POST_LOG) as f:
            for line in f:
                try:
                    record = json.loads(line)
                    opening = record.get("opening", "")
                    if opening:
                        openings.append(opening)
                except json.JSONDecodeError:
                    continue
    return openings[-n:]


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--force-refresh", action="store_true")
    args = parser.parse_args()

    # Load validated structural bodies
    with open(STRUCTURAL_FILE) as f:
        data = json.load(f)
    bodies = [c for c in data.get("cadences", []) if c["validation"]["passed"]]
    if not bodies:
        print("No validated structural bodies found. Run reddit_structural_bodies.py first.")
        return 1

    recent_openings = get_recent_openings(6)
    print(f"Loaded {len(bodies)} structural cadences")
    print(f"Recent logged openings: {len(recent_openings)}")

    # Score bodies based on freshness vs recent openings
    results = []
    for cadence in bodies:
        body = cadence["body"]
        opening = body.split("\n")[0][:100]
        too_similar = any(
            opening[:40].lower() in recent.lower()
            for recent in recent_openings
            if len(recent) > 20
        ) if recent_openings else False

        results.append({
            "ts": utcnow(),
            "type": "structural_body_promoted",
            "cadence_id": cadence["id"],
            "cadence_label": cadence["label"],
            "description": cadence["description"],
            "body": body,
            "opening": opening,
            "freshness_score": 0.0 if too_similar else 1.0,
            "validation": cadence["validation"],
            "ready_for_use": not too_similar,
        })

    # Sort: fresh first, shuffle equal scores for variety
    fresh = [r for r in results if r["freshness_score"] > 0]
    stale = [r for r in results if r["freshness_score"] == 0]
    random.shuffle(fresh)
    sorted_results = fresh + stale

    # Staleness recovery: if everything is freshness-blocked, do NOT inject stale
    # bodies into the log (that would make the problem worse by adding duplicate
    # openings to the recent-openings pool). Either force-regenerate or skip.
    if not fresh:
        if args.force_refresh:
            print("\nAll bodies freshness-blocked. Force-refresh requested.")
            print("Re-running reddit_structural_bodies.py to generate fresh variants...")
            regen_result = subprocess.run(
                [sys.executable, str(ROOT / "agents/marketing/reddit_structural_bodies.py")],
                capture_output=True, text=True,
            )
            print(regen_result.stdout.strip())
            if regen_result.returncode != 0:
                print(f"Regeneration failed: {regen_result.stderr.strip()}", file=sys.stderr)
                return 1
            # Reload and re-score
            with open(STRUCTURAL_FILE) as f:
                data = json.load(f)
            bodies = [c for c in data.get("cadences", []) if c["validation"]["passed"]]
            if not bodies:
                print("Regenerated pool has no validated bodies.")
                return 1
            results = []
            for cadence in bodies:
                body = cadence["body"]
                opening = body.split("\n")[0][:100]
                too_similar = any(
                    opening[:40].lower() in recent.lower()
                    for recent in recent_openings
                    if len(recent) > 20
                ) if recent_openings else False
                results.append({
                    "ts": utcnow(),
                    "type": "structural_body_promoted",
                    "cadence_id": cadence["id"],
                    "cadence_label": cadence["label"],
                    "description": cadence["description"],
                    "body": body,
                    "opening": opening,
                    "freshness_score": 0.0 if too_similar else 1.0,
                    "validation": cadence["validation"],
                    "ready_for_use": not too_similar,
                })
            fresh = [r for r in results if r["freshness_score"] > 0]
            stale = [r for r in results if r["freshness_score"] == 0]
            random.shuffle(fresh)
            sorted_results = fresh + stale
            print(f"After regeneration: {len(fresh)} fresh / {len(stale)} stale")
            if not fresh:
                print("Still no fresh bodies after regeneration. Will not inject stale entries.")
                output = {
                    "generated_at": utcnow(),
                    "source": "promote_structural_bodies.py",
                    "total_cadences": len(results),
                    "ready_count": 0,
                    "freshness_blocked": len(stale),
                    "cadences": sorted_results,
                    "staleness_recovery": "attempted_regeneration_still_blocked",
                }
                with open(OUTPUT_FILE, "w") as f:
                    json.dump(output, f, indent=2)
                return 0
        else:
            print("\nAll bodies freshness-blocked. Not injecting stale entries into log.")
            print("Run with --force-refresh to regenerate bodies, or run reddit_structural_bodies.py first.")
            output = {
                "generated_at": utcnow(),
                "source": "promote_structural_bodies.py",
                "total_cadences": len(results),
                "ready_count": 0,
                "freshness_blocked": len(stale),
                "cadences": sorted_results,
                "staleness_recovery": "blocked_no_injection",
            }
            with open(OUTPUT_FILE, "w") as f:
                json.dump(output, f, indent=2)
            return 0

    # Write fresh bodies to jsonl so build_comment() reads them as recent history
    with open(POST_LOG, "a") as f:
        for r in sorted_results:
            log_entry = {
                "ts": r["ts"],
                "type": r["type"],
                "cadence_id": r["cadence_id"],
                "cadence_label": r["cadence_label"],
                "opening": r["opening"],
                "validation": r["validation"],
            }
            f.write(json.dumps(log_entry) + "\n")

    # Write human-readable summary
    output = {
        "generated_at": utcnow(),
        "source": "promote_structural_bodies.py",
        "total_cadences": len(results),
        "ready_count": len(fresh),
        "freshness_blocked": len(stale),
        "cadences": sorted_results,
    }
    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2)

    print(f"Ready: {len(fresh)}/{len(results)}")
    print(f"Output: {OUTPUT_FILE}")
    print(f"Injected into: {POST_LOG}")
    for r in sorted_results:
        tag = "READY" if r["ready_for_use"] else "STALE"
        print(f"  [{tag}] {r['cadence_id']}: {r['opening'][:60]}...")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
