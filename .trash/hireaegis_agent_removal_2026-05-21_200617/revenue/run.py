#!/usr/bin/env python3
"""
Revenue Agent — Runs every 6h
Checks LemonSqueezy API for new subscriptions, tracks MRR.
"""
import os, json, sys
from datetime import datetime

# Add the revenue-monitor directory to path for the check_revenue module
sys.path.insert(0, '/home/mistlight/.openclaw/workspace/agents/revenue-monitor')
LOG_DIR = "/home/mistlight/.openclaw/workspace/agents/revenue/logs"
os.makedirs(LOG_DIR, exist_ok=True)

API_KEY = "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.eyJhdWQiOiI5NGQ1OWNlZi1kYmI4LTRlYTUtYjE3OC1kMjU0MGZjZDY5MTkiLCJqdGkiOiI1YmY3NGUyOGJkMWM2MGZlOGNmYzI1ZWU0OGZmZWUzMzM0ZjdlYTg2ZDk0N2QxYmRmNWIwNjRmMjBiYjBhZDE1ZWVhYTg2YTU4OTM4YjMzNSIsImlhdCI6MTc3ODI5ODYxNS44MjY2OTIsIm5iZiI6MTc3ODI5ODYxNS44MjY2OTUsImV4cCI6MTc5NDA5NjAwMC4wMzYyODgsInN1YiI6IjYxOTg2MTYiLCJzY29wZXMiOltdfQ.r2wHkfnoL5dVwaStngOHBT-W4S4b_kBvusQrDQ4d3dUyfIVbHODTwXiq4njfs5kBYWrDou_OogIsHHKS2GLt1lxJB8vvFORdTJSXag2BXkMlsLHbnQOkHcWbv9KdFhcb1RrHeJv_jZFf_qdmezHWHPRGZAzklSTfF1dUuT9CyA_p3I3bVjjhxQqgTY3LSpdUtIDhuvgi9e8cXI6c56lUNHOKIUjZ5smLoRw-AZ6vaIkmUI2Yu11NFxVsPd16eDY_eP7_D6bi2GdYGxNpX6v-HVp6Ofke62yTEsoDd73CgxQcSma5P37vW2Zo4xclInXhgO-pXZsCSgCqp7Te4yqBaEHitHkKWmtl6v_fqXZMIVKZhWI4jl--v3d4jGbtfwo7RBjGKYisGL6TKR2x7zPZ7pAnbHv_qw61JGM6cDyJ8uYHfYbwutrWtHvRzHSMBZle7Cb2iWXU_kyPH9PtCLw5knv5hJPtFR6zkVrTidwPkY_h81pjYx807HYtJyMGqeQNIL8uLdrsSvqkV7Ox-zstyFYs-YVurNneqBy0EQFvpOYV073WReyjShYY5Hu6s_9bxYsJHqpPn_U45L8hVzcPl_Z9IEeccc6RycLXRrscHNdqHkIe612VA1ZTSkNqbEOEKl-xhOr5d349bwZDmsBQ7rbU_gBdVZ5Q2uoIFASbgI"
STORE_ID = "944d59ce-dbb8-4ea5-b178-d2540fcd6919"
STATE_FILE = f"{LOG_DIR}/last_state.json"

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"known_subscription_ids": [], "last_mrr": 0}

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)

def fetch_json(endpoint):
    import urllib.request, urllib.error
    url = f"https://api.lemonsqueezy.com/v1/{endpoint}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {API_KEY}",
        "Accept": "application/vnd.api+json"
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.reason}", "body": e.read().decode()[:200]}
    except Exception as e:
        return {"error": str(e)}

def calc_mrr(subs):
    total = 0
    for s in subs:
        attrs = s.get("attributes", {})
        status = attrs.get("status", "")
        if status in ("active", "on_trial"):
            variants = attrs.get("variant", {})
            price = variants.get("price", 0) if isinstance(variants, dict) else 0
            total += price
    return int(total)

def main():
    print(f"[Revenue] Starting at {datetime.now().isoformat()}")
    
    state = load_state()
    known_ids = set(state.get("known_subscription_ids", []))
    
    # Fetch subscriptions
    data = fetch_json(f"subscriptions?filter[store_id]={STORE_ID}&page[size]=50")
    
    if "error" in data:
        print(f"[Revenue] API error: {data['error']}")
        log = {"timestamp": datetime.now().isoformat(), "error": data["error"], "mrr": 0, "total_subs": 0}
    else:
        subs = data.get("data", [])
        attrs_list = [s.get("attributes", {}) for s in subs]
        active_subs = [a for a in attrs_list if a.get("status") in ("active", "on_trial")]
        
        mrr = calc_mrr(active_subs)
        current_ids = set(s.get("id") for s in subs)
        new_ids = current_ids - known_ids
        gone_ids = known_ids - current_ids
        
        log = {
            "timestamp": datetime.now().isoformat(),
            "mrr": mrr,
            "total_subs": len(active_subs),
            "new_this_check": len(new_ids),
            "churned_this_check": len(gone_ids),
            "last_mrr": state.get("last_mrr", 0),
            "mrr_change": mrr - state.get("last_mrr", 0),
        }
        
        # Update state
        state["known_subscription_ids"] = list(current_ids)
        state["last_mrr"] = mrr
        save_state(state)
        
        print(f"[Revenue] MRR: ${mrr} | Subs: {len(active_subs)} | New: {len(new_ids)} | Churned: {len(gone_ids)}")
    
    # Save log
    log_file = f"{LOG_DIR}/{datetime.now().strftime('%Y-%m-%d')}.json"
    with open(log_file, 'w') as f:
        json.dump(log, f, indent=2)
    
    print(f"[Revenue] Done.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
