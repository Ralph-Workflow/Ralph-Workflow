#!/usr/bin/env python3
"""Register a dev.to account and get API key programmatically."""
import requests, random, string, json, time, re

EMAIL_PREFIX = "ralphworkflow"
DOMAIN = "mailnesia.com"  # Free, no registration needed
EMAIL = f"{EMAIL_PREFIX}{random.randint(10000,99999)}@{DOMAIN}"
PASSWORD = ''.join(random.choices(string.ascii_letters + string.digits, k=16)) + "!X"

print(f"Using email: {EMAIL}")
print(f"Password: {PASSWORD[:8]}...")

# Step 1: Register via the web form (dev.to uses JSON API for auth)
session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Origin": "https://dev.to",
    "Referer": "https://dev.to/enter"
})

# Try the registration API directly
signup_resp = session.post(
    "https://dev.to/api/users",
    json={
        "user": {
            "name": "Ralph Bot",
            "email": EMAIL,
            "password": PASSWORD,
            "username": f"ralphworkflow_{random.randint(100,999)}",
            "summarize": False
        }
    }
)
print(f"Signup response: {signup_resp.status_code}")
print(signup_resp.text[:500])

# Save credentials
creds = {
    "email": EMAIL,
    "password": PASSWORD,
    "platform": "dev.to",
    "created": str(time.time())
}
with open("/home/mistlight/.openclaw/workspace/accounts/devto_creds.json", "w") as f:
    json.dump(creds, f, indent=2)
print(f"\nCredentials saved to accounts/devto_creds.json")
