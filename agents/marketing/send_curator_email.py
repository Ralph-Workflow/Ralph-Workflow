#!/usr/bin/env python3
"""Send a one-off curator outreach email and log the result.

Usage:
  SMTP_USER=... SMTP_PASS=... python3 send_curator_email.py \
    --to info@example.com --subject "..." --body-file /path/to/body.txt
"""
from __future__ import annotations

import argparse
import json
import os
import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path

ROOT = Path('/home/mistlight/.openclaw/workspace')
LOG_DIR = ROOT / 'agents' / 'marketing' / 'logs'
LOG_DIR.mkdir(parents=True, exist_ok=True)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    p.add_argument('--to', required=True)
    p.add_argument('--subject', required=True)
    p.add_argument('--body-file', required=True)
    p.add_argument('--from-name', default='Ralph Workflow')
    p.add_argument('--from-email', default='')
    p.add_argument('--log-name', default='curator_email')
    p.add_argument('--dry-run', action='store_true')
    return p


def main() -> int:
    args = build_parser().parse_args()
    smtp_host = os.environ.get('SMTP_HOST', 'smtp.ionos.com')
    smtp_port = int(os.environ.get('SMTP_PORT', '587'))
    smtp_user = os.environ.get('SMTP_USER', '')
    smtp_pass = os.environ.get('SMTP_PASS', '')

    body = Path(args.body_file).read_text()
    now = datetime.now(timezone.utc)
    from_email = args.from_email or smtp_user

    if not from_email:
        raise SystemExit('from-email missing and SMTP_USER not set')

    msg = EmailMessage()
    msg['From'] = formataddr((args.from_name, from_email))
    msg['To'] = args.to
    msg['Subject'] = args.subject
    msg.set_content(body)

    result = {
        'timestamp_utc': now.isoformat(),
        'action': 'curator_email_outreach',
        'status': 'dry_run' if args.dry_run else 'pending',
        'channel': {
            'recipient': args.to,
            'subject': args.subject,
            'smtp_host': smtp_host,
            'smtp_port': smtp_port,
        },
        'body_file': str(Path(args.body_file)),
    }

    if not args.dry_run:
        if not smtp_user or not smtp_pass:
            raise SystemExit('SMTP_USER/SMTP_PASS are required')
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
            smtp.login(smtp_user, smtp_pass)
            smtp.send_message(msg)
        result['status'] = 'sent'

    stamp = now.strftime('%Y-%m-%d_%H%M%S')
    out = LOG_DIR / f'marketing_{stamp}_{args.log_name}.json'
    out.write_text(json.dumps(result, indent=2) + '\n')
    print(out)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
