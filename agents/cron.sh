#!/bin/bash
# Master cron setup — all paths use correct agent directories
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

(crontab -l 2>/dev/null | grep -v "openclaw.*agents"; cat << 'EOF'
# ===== MONEY MACHINE CRON =====
# Revenue monitor — every 6h
0 */6 * * * /usr/bin/python3 /home/mistlight/.openclaw/workspace/agents/revenue/run.py >> /home/mistlight/.openclaw/workspace/agents/revenue/logs/cron.log 2>&1

# Content engine — every 24h at midnight
0 0 * * * /usr/bin/python3 /home/mistlight/.openclaw/workspace/agents/content/run.py >> /home/mistlight/.openclaw/workspace/agents/content/logs/cron.log 2>&1

# Community outreach — every 24h at 6am
0 6 * * * /usr/bin/python3 /home/mistlight/.openclaw/workspace/agents/community/run.py >> /home/mistlight/.openclaw/workspace/agents/community/logs/cron.log 2>&1

# SEO agent — every 12h
0 */12 * * * /usr/bin/python3 /home/mistlight/.openclaw/workspace/agents/seo/run.py >> /home/mistlight/.openclaw/workspace/agents/seo/logs/cron.log 2>&1

# Product improvement — every 24h at 9am
0 9 * * * /usr/bin/python3 /home/mistlight/.openclaw/workspace/agents/product/run.py >> /home/mistlight/.openclaw/workspace/agents/product/logs/cron.log 2>&1
EOF
) | crontab -

echo "Cron installed:"
crontab -l
