#!/usr/bin/env python3
"""Reliable Apollo login using stored creds + fresh email OTP.

Flow:
- opens Apollo login
- submits username/password
- waits for verify-email step
- triggers resend to guarantee a fresh OTP
- fetches the newest Apollo OTP from IONOS mailbox
- submits code immediately
- verifies landing on Apollo home
"""

from __future__ import annotations

import email
import email.utils
import imaplib
import os
import re
import sys
import time
from dataclasses import dataclass
from typing import Optional

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

APOLLO_EMAIL = os.environ.get("APOLLO_EMAIL", "ken@hireaegis.com")
APOLLO_PASSWORD = os.environ.get("APOLLO_PASSWORD", "ngzcz!tS*jWo4dY1QjlZ@cxAd2r2c$Tf")
IMAP_HOST = os.environ.get("APOLLO_IMAP_HOST", "imap.ionos.com")
IMAP_PORT = int(os.environ.get("APOLLO_IMAP_PORT", "993"))
IMAP_USER = os.environ.get("APOLLO_IMAP_USER", "ken@hireaegis.com")
IMAP_PASSWORD = os.environ.get("APOLLO_IMAP_PASSWORD", "GV%@iwClD4vetq")
HEADLESS = os.environ.get("APOLLO_HEADLESS", "1") != "0"
CHROMIUM_PATH = os.environ.get("APOLLO_CHROMIUM", "/usr/bin/chromium")
OTP_SUBJECT = "Your Apollo.io verification code"
OTP_SENDER = "support@apollo.io"


@dataclass
class OTPMessage:
    code: str
    date_header: str
    timestamp: float


def log(*parts: object) -> None:
    print("[apollo-login]", *parts, flush=True)


def extract_text(msg: email.message.Message) -> str:
    chunks: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() in ("text/plain", "text/html"):
                try:
                    payload = part.get_payload(decode=True)
                    if payload:
                        chunks.append(payload.decode(errors="replace"))
                except Exception:
                    pass
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            chunks.append(payload.decode(errors="replace"))
    return "\n".join(chunks)


def find_latest_otp(after_ts: float, timeout_seconds: int = 120) -> OTPMessage:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        mbox = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        try:
            mbox.login(IMAP_USER, IMAP_PASSWORD)
            mbox.select("INBOX")
            status, data = mbox.search(None, f'(FROM "{OTP_SENDER}" SUBJECT "{OTP_SUBJECT}")')
            if status != "OK":
                raise RuntimeError(f"IMAP search failed: {status}")
            ids = data[0].split()
            candidates: list[OTPMessage] = []
            for num in ids[-12:]:
                status, msg_data = mbox.fetch(num, "(RFC822)")
                if status != "OK":
                    continue
                raw = msg_data[0][1]
                msg = email.message_from_bytes(raw)
                date_header = msg.get("Date", "")
                try:
                    ts = email.utils.parsedate_to_datetime(date_header).timestamp()
                except Exception:
                    ts = 0
                if ts < after_ts:
                    continue
                body = extract_text(msg)
                match = re.search(r"\b(\d{6})\b", body)
                if match:
                    candidates.append(OTPMessage(match.group(1), date_header, ts))
            if candidates:
                candidates.sort(key=lambda x: x.timestamp)
                return candidates[-1]
        finally:
            try:
                mbox.logout()
            except Exception:
                pass
        time.sleep(4)
    raise RuntimeError("No fresh Apollo OTP found after resend")


def visible_text(page) -> str:
    try:
        return page.locator("body").inner_text(timeout=5000)
    except Exception:
        return ""


def submit_login(page) -> None:
    page.goto("https://app.apollo.io/#/login", wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(2500)
    page.locator('input[type="email"]').first.fill(APOLLO_EMAIL)
    page.locator('input[type="password"]').first.fill(APOLLO_PASSWORD)
    page.get_by_role("button", name=re.compile(r"^Log in$", re.I)).click()


def reach_verification(page) -> None:
    try:
        page.wait_for_url(re.compile(r".*/verify-email"), timeout=30000)
        return
    except PlaywrightTimeoutError:
        pass
    body = visible_text(page)
    if "Welcome, Ken" in body or page.url.endswith("#/home"):
        log("Already logged in")
        return
    raise RuntimeError(f"Did not reach verification/home. Current URL: {page.url}\n{body[:1000]}")


def submit_otp(page, code: str) -> None:
    loc = page.locator('input[type="text"]').first
    loc.click()
    try:
        loc.fill("")
    except Exception:
        pass
    page.keyboard.type(code, delay=35)
    page.get_by_role("button", name=re.compile(r"^Continue$", re.I)).click(timeout=10000)


def verify_success(page) -> None:
    page.wait_for_timeout(8000)
    if page.url.endswith("#/home"):
        return
    body = visible_text(page)
    if "Welcome, Ken" in body:
        return
    raise RuntimeError(f"Apollo OTP submit did not reach home. URL: {page.url}\n{body[:1200]}")


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=HEADLESS,
            executable_path=CHROMIUM_PATH,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(viewport={"width": 1440, "height": 1200})
        page = context.new_page()
        try:
            submit_login(page)
            reach_verification(page)
            if page.url.endswith("#/home"):
                log("Login succeeded without OTP step")
                return 0
            resend_started_at = time.time()
            page.get_by_role("button", name=re.compile(r"^Resend code$", re.I)).click(timeout=10000)
            log("Requested fresh OTP")
            otp = find_latest_otp(after_ts=resend_started_at, timeout_seconds=120)
            log("Using OTP from", otp.date_header)
            submit_otp(page, otp.code)
            verify_success(page)
            log("Login succeeded; landed on", page.url)
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        log("ERROR:", exc)
        raise
