#!/usr/bin/env python3
"""
LemonSqueezy Revenue Monitor for HireAegis
Fetches MRR, subscriptions, orders, and detects new subscriptions.
"""

import json
import os
import sys
import requests
from datetime import date, datetime

# Config
API_KEY = "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.eyJhdWQiOiI5NGQ1OWNlZi1kYmI4LTRlYTUtYjE3OC1kMjU0MGZjZDY5MTkiLCJqdGkiOiI1YmY3NGUyOGJkMWM2MGZlOGNmYzI1ZWU0OGZmZWUzMzM0ZjdlYTg2ZDk0N2QxYmRmNWIwNjRmMjBiYjBhZDE1ZWVhYTg2YTU4OTM4YjMzNSIsImlhdCI6MTc3ODI5ODYxNS44MjY2OTIsIm5iZiI6MTc3ODI5ODYxNS44MjY2OTUsImV4cCI6MTc5NDA5NjAwMC4wMzYyODgsInN1YiI6IjYxOTg2MTYiLCJzY29wZXMiOltdfQ.r2wHkfnoL5dVwaStngOHBT-W4S4b_kBvusQrDQ4d3dUyfIVbHODTwXiq4njfs5kBYWrDou_OogIsHHKS2GLt1lxJB8vvFORdTJSXag2BXkMlsLHbnQOkHcWbv9KdFhcb1RrHeJv_jZFf_qdmezHWHPRGZAzklSTfF1dUuT9CyA_p3I3bVjjhxQqgTY3LSpdUtIDhuvgi9e8cXI6c56lUNHOKIUjZ5smLoRw-AZ6vaIkmUI2Yu11NFxVsPd16eDY_eP7_D6bi2GdYGxNpX6v-HVp6Ofke62yTEsoDd73CgxQcSma5P37vW2Zo4xclInXhgO-pXZsCSgCqp7Te4yqBaEHitHkKWmtl6v_fqXZMIVKZhWI4jl--v3d4jGbtfwo7RBjGKYisGL6TKR2x7zPZ7pAnbHv_qw61JGM6cDyJ8uYHfYbwutrWtHvRzHSMBZle7Cb2iWXU_kyPH9PtCLw5knv5hJPtFR6zkVrTidwPkY_h81pjYx807HYtJyMGqeQNIL8uLdrsSvqkV7Ox-zstyFYs-YVurNneqBy0EQFvpOYV073WReyjShYY5Hu6s_9bxYsJHqpPn_U45L8hVzcPl_Z9IEeccc6RycLXRrscHNdqHkIe612VA1ZTSkNqbEOEKl-xhOr5d349bwZDmsBQ7rbU_gBdVZ5Q2uoIFASbgI"
STORE_ID = "944d59ce-dbb8-4ea5-b178-d2540fcd6919"
BASE_URL = "https://api.lemonsqueezy.com/v1"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(SCRIPT_DIR, "state.json")
TODAY = date.today().isoformat()
LOG_FILE = os.path.join(SCRIPT_DIR, "logs", f"{TODAY}.json")


def get_headers():
    return {
        "Authorization": f"Bearer {API_KEY}",
        "Accept": "application/vnd.api+json",
        "Content-Type": "application/vnd.api+json",
    }


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"known_subscription_ids": [], "last_check": None}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def fetch_json(path, params=None):
    url = f"{BASE_URL}{path}"
    resp = requests.get(url, headers=get_headers(), params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def get_subscriptions():
    """Fetch all active subscriptions for the store."""
    all_subs = []
    page = 1
    while True:
        data = fetch_json("/subscriptions", {"filter[store]": STORE_ID, "filter[status]": "active", "page": page, "per_page": 100})
        subs = data.get("data", [])
        all_subs.extend(subs)
        meta = data.get("meta", {}).get("page", {})
        if page >= meta.get("last_page", 1):
            break
        page += 1
    return all_subs


def get_orders():
    """Fetch recent orders."""
    data = fetch_json("/orders", {"filter[store]": STORE_ID, "page": 1, "per_page": 5})
    return data.get("data", [])


def parse_first_name(attrs):
    """Try to extract a plan name from first_name or product_name."""
    return attrs.get("first_name") or attrs.get("product_name") or "Unknown"


def compute_mrr(subs):
    """Sum up monthly recurring revenue from active subscriptions."""
    total = 0.0
    for sub in subs:
        attrs = sub.get("attributes", {})
        status = attrs.get("status")
        if status != "active":
            continue
        # Unit price is in cents
        unit_price = attrs.get("unit_price", 0)
        # Check if variant is monthly (renews monthly)
        renews_at = attrs.get("renews_at", "")
        # Simple heuristic: if renews_at is set and unit_price > 0, add it
        if unit_price and renews_at:
            total += unit_price / 100.0
    return total


def main():
    state = load_state()
    known_ids = set(state.get("known_subscription_ids", []))
    today_new_ids = []

    try:
        subs = get_subscriptions()
        orders = get_orders()
    except Exception as e:
        print(f"ERROR: Failed to fetch from LemonSqueezy API: {e}")
        sys.exit(1)

    # Detect new subscriptions
    current_ids = set()
    plan_counts = {}
    for sub in subs:
        sid = str(sub.get("id"))
        current_ids.add(sid)
        plan = parse_first_name(sub.get("attributes", {}))
        plan_counts[plan] = plan_counts.get(plan, 0) + 1

        if sid not in known_ids:
            today_new_ids.append(sid)

    mrr = compute_mrr(subs)
    total_subs = len(subs)

    # Build plan summary string
    plan_str = " ".join(f"{plan}:{count}" for plan, count in sorted(plan_counts.items()))

    # Update state
    all_known = known_ids | current_ids
    save_state({
        "known_subscription_ids": list(all_known),
        "last_check": datetime.utcnow().isoformat() + "Z"
    })

    # Log to daily file
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    log_entry = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "mrr": mrr,
        "total_subscriptions": total_subs,
        "plan_counts": plan_counts,
        "new_subscription_ids": today_new_ids,
        "recent_orders": [
            {
                "id": o.get("id"),
                "status": o.get("attributes", {}).get("status"),
                "total": o.get("attributes", {}).get("total"),
                "created_at": o.get("attributes", {}).get("created_at"),
            }
            for o in orders
        ]
    }

    # Append to log (read existing if present, append entry)
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE) as f:
                log_data = json.load(f)
        except Exception:
            log_data = []
    else:
        log_data = []

    log_data.append(log_entry)

    with open(LOG_FILE, "w") as f:
        json.dump(log_data, f, indent=2)

    # One-line stdout summary
    summary = f"MRR: ${mrr:.2f} | Subs: {total_subs} ({plan_str}) | New today: {len(today_new_ids)}"
    print(summary)


if __name__ == "__main__":
    main()
