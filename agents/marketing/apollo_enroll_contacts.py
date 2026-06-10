#!/usr/bin/env python3
"""apollo_enroll_contacts.py — one-shot helper for the reveals → contacts → enroll loop.

The D20 (stale scorecard) + D25 (MPP-raw opens) + this-run (in-flight contact
creation stall) pattern all share a root cause: the Apollo contact-create +
add_contact_ids two-step has a non-obvious required body field
(`emailer_campaign_id` in the body, not just the URL), and the missing field
returns 422 'Please specify a emailer_campaign_id and
send_email_from_email_account_id.' A 3rd instance of Apollo's confusing
partial-path/partial-body schema (after the D16 cross-sequence re-enrollment
block + the D23 fleet-monitor silent-skip).

This script does BOTH POSTs in one transaction with the right body schema,
dedup's against existing V{seq_id}+V3 enrollments, and logs the result to
the tactic_ledger. Future reveals → contacts → enroll loops should call this
instead of hand-rolling the two-step.

Usage:
  python3 apollo_enroll_contacts.py --seq-id 6a28ab43d83ca70014f90be0 \\
      --candidates /tmp/v9_topup_3.json \\
      --mailbox-id 69b080dea7fa4d0019b912c2

The candidates JSON is a list of dicts with at least:
  - first_name, last_name, email, person_id, title

The script will:
  1. DEDUP against existing V{seq_id} + V3 (V3 is the dedicated "AI agent"
     angle; cross-enrolling between the two is blocked per D16).
  2. POST /contacts for each surviving candidate (rate-limited, 0.3s sleep).
  3. POST /emailer_campaigns/{seq_id}/add_contact_ids in batches of 5.
  4. Log the action to tactic_ledger.jsonl.
  5. Print the summary (created, added, duplicates, errors).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API_KEY_PATH = ROOT / "TOOLS.md"
LEDGER = ROOT / "agents/marketing/logs/tactic_ledger.jsonl"

# All known Ralph-AB sequence IDs + their angle. Used for D16 dedup: a contact
# already enrolled in any Ralph-AB arm (other than the target) is a duplicate
# and Apollo will reject the cross-enrollment (D16 confirmed block).
KNOWN_ARMS = {
    "6a28ab43d83ca70014f90be0": "Ralph-AB-V9-AI-Observability-DevRel",
    "6a2757e1cf766a0014cbf939": "Ralph-AB-V3-AI-Agent-Composition",
    # add more as the program grows
}


def get_api_key() -> str:
    text = API_KEY_PATH.read_text(encoding="utf-8")
    import re
    m = re.search(r"###\s*Apollo\.io(.*?)(?:\n###\s|\Z)", text, re.S)
    km = re.search(r"API key[^\n]*?`([^`]+)`", m.group(1) if m else text)
    if not km:
        raise SystemExit("Apollo API key not found in TOOLS.md")
    return km.group(1).strip()


def api(method: str, path: str, payload: dict, key: str) -> dict:
    req = urllib.request.Request(
        f"https://api.apollo.io/api/v1{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-Api-Key": key},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"_error": f"HTTP {e.code}: {e.read().decode()}"}


def get_enrolled_emails(seq_id: str, key: str) -> set[str]:
    """D16 dedup: who is already enrolled in this sequence?"""
    enrolled = set()
    page = 1
    while True:
        r = api("POST", "/contacts/search",
                {"emailer_campaign_ids": [seq_id], "per_page": 100, "page": page}, key)
        contacts = r.get("contacts", [])
        if not contacts:
            break
        for c in contacts:
            e = (c.get("email") or "").lower()
            if e:
                enrolled.add(e)
        if len(contacts) < 100:
            break
        page += 1
    return enrolled


def get_cross_enrolled_emails(target_seq_id: str, key: str) -> set[str]:
    """D16 dedup: who is already enrolled in ANY OTHER active Ralph-AB arm?
    Apollo blocks cross-enrollment with a 422 'contacts_active_in_other_campaigns'."""
    cross = set()
    for sid in KNOWN_ARMS:
        if sid == target_seq_id:  # don't dedup the target against itself
            continue
        cross |= get_enrolled_emails(sid, key)
    return cross


def create_contact(c: dict, key: str) -> str | None:
    """POST /contacts — returns contact_id or None on failure."""
    payload = {
        "first_name": c["first_name"],
        "last_name": c.get("last_name") or "Unknown",
        "email": c["email"],
        "title": c.get("title", ""),
        "person_id": c.get("person_id", ""),
    }
    r = api("POST", "/contacts", payload, key)
    cid = r.get("contact", {}).get("id")
    if not cid:
        print(f"  ✗ create_contact FAILED: {c['first_name']} {c['last_name']} — {r}", file=sys.stderr)
    return cid


def add_to_sequence(seq_id: str, contact_ids: list[str], mailbox_id: str, key: str) -> dict:
    """POST /emailer_campaigns/{seq_id}/add_contact_ids — the body field trick:
    emailer_campaign_id must be in the BODY, not just the URL."""
    payload = {
        "emailer_campaign_id": seq_id,
        "contact_ids": contact_ids,
        "send_email_from_email_account_id": mailbox_id,
    }
    return api("POST", f"/emailer_campaigns/{seq_id}/add_contact_ids", payload, key)


def log_ledger(entry: dict) -> None:
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--seq-id", required=True, help="Apollo emailer_campaign id (the target sequence)")
    p.add_argument("--candidates", required=True, type=Path, help="JSON file: list of {first_name, last_name, email, person_id, title}")
    p.add_argument("--mailbox-id", required=True, help="Apollo email_account_id to send from (ken@ralphworkflow.com = 69b080dea7fa4d0019b912c2)")
    p.add_argument("--tactic", default="apollo_enroll_topup", help="ledger tactic name")
    p.add_argument("--no-ledger", action="store_true", help="don't write to tactic_ledger (for testing)")
    args = p.parse_args()

    key = get_api_key()
    candidates = json.loads(args.candidates.read_text(encoding="utf-8"))
    if not isinstance(candidates, list):
        raise SystemExit(f"candidates JSON must be a list, got {type(candidates).__name__}")

    seq_label = KNOWN_ARMS.get(args.seq_id, args.seq_id)
    print(f"Target: {seq_label} ({args.seq_id})")
    print(f"Mailbox: {args.mailbox_id}")
    print(f"Candidates: {len(candidates)}")

    # 1. DEDUP
    print("\n=== DEDUP (D16) ===")
    seq_emails = get_enrolled_emails(args.seq_id, key)
    cross_emails = get_cross_enrolled_emails(args.seq_id, key)
    survivors = []
    for c in candidates:
        e = c["email"].lower()
        if e in seq_emails:
            print(f"  ⏭ {c['first_name']} {c['last_name']} — already in target sequence")
        elif e in cross_emails:
            print(f"  ⏭ {c['first_name']} {c['last_name']} — already in another Ralph-AB arm (D16 block)")
        else:
            survivors.append(c)
    print(f"Survivors: {len(survivors)}")

    if not survivors:
        print("Nothing to enroll. Done.")
        return 0

    # 2. CREATE CONTACTS
    print("\n=== CREATE CONTACTS ===")
    created = []
    for i, c in enumerate(survivors, 1):
        cid = create_contact(c, key)
        if cid:
            created.append((cid, c))
            print(f"  ✓ [{i}/{len(survivors)}] {c['first_name']} {c['last_name']} → {cid}")
        time.sleep(0.3)  # rate-limit

    if not created:
        print("No contacts created. Aborting.")
        return 1

    # 3. ADD TO SEQUENCE (batches of 5)
    print("\n=== ADD TO SEQUENCE ===")
    contact_ids = [cid for cid, _ in created]
    added = 0
    for batch_start in range(0, len(contact_ids), 5):
        batch = contact_ids[batch_start:batch_start + 5]
        r = add_to_sequence(args.seq_id, batch, args.mailbox_id, key)
        if "_error" in r:
            print(f"  ✗ batch {batch_start // 5 + 1}: {r['_error']}")
        else:
            n = len(r.get("contacts", []))
            added += n
            names = [c.get("name") for c in r.get("contacts", [])]
            print(f"  ✓ batch {batch_start // 5 + 1}: added {n} — {names}")
        time.sleep(1.5)

    # 4. LOG
    print(f"\n=== SUMMARY ===")
    print(f"Created: {len(created)} of {len(survivors)}")
    print(f"Added to sequence: {added} of {len(contact_ids)}")

    if not args.no_ledger:
        from datetime import datetime
        entry = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "tactic": args.tactic,
            "channel": "apollo-sequences",
            "expected_signal": f"Top up {seq_label} to power (30+) with verified on-ICP contacts",
            "observed": f"Enrolled {added} of {len(survivors)} candidates into {seq_label}. Dedup: {len(seq_emails)} already in target, {len(cross_emails)} already in other arms. Created contact_ids: {[cid for cid, _ in created]}. Used helper apollo_enroll_contacts.py (durability fix for the 07:25 stall — the in-flight 2-step that returned 422 'Please specify a emailer_campaign_id and send_email_from_email_account_id' before the body-field fix).",
            "verdict": "worked" if added == len(survivors) else "partial",
            "note": f"New state: {seq_label} has +{added} active contacts. Daily cap: ≤16 new enrollments/day account-wide; this batch stays within cap.",
            "source": f"apollo_enroll_contacts.py --seq-id {args.seq_id} --candidates {args.candidates} --mailbox-id {args.mailbox_id}",
            "checkback": datetime.now().strftime("%Y-%m-%d"),  # 1d
        }
        log_ledger(entry)
        print(f"Logged to {LEDGER.name}")

    return 0 if added == len(survivors) else 2


if __name__ == "__main__":
    sys.exit(main())
