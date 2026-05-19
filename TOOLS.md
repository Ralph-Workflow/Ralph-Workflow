# TOOLS.md - Local Notes

Skills define _how_ tools work. This file is for _your_ specifics — the stuff that's unique to your setup.

## What Goes Here

Things like:

- Camera names and locations
- SSH hosts and aliases
- Preferred voices for TTS
- Speaker/room names
- Device nicknames
- Anything environment-specific

## Examples

```markdown
### Cameras

- living-room → Main area, 180° wide angle
- front-door → Entrance, motion-triggered

### SSH

- home-server → 192.168.1.100, user: admin

### TTS

- Preferred voice: "Nova" (warm, slightly British)
- Default speaker: Kitchen HomePod
```

## Why Separate?

Skills are shared. Your setup is yours. Keeping them apart means you can update skills without losing your notes, and share skills without leaking your infrastructure.

---

Add whatever helps you do your job. This is your cheat sheet.

## SSH

- **Personal key:** `ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAINv9IIpPvDKDDnq4z8bI/rlEaMxwi8LT/YxETu4dkKaa mistlight@Debian-1300-trixie-amd64-base`
  - Used for: git repos, Codeberg, GitHub, etc.

## External APIs

- **Minimax API (OpenCode):** `sk-cp-cZhzgD75zcZiuQ42wsiiDM_EzFxbuKUV94M_vvYhE88ohNKfe9udkXXIrqBv6BNxRzasB_vqHBa6Ubtd6JOr4Y7rnbPjwvLC2yG5QapmSIcsudIyuEipodY`
  - Used for: AI model access via OpenClaw
- **Browserless:** `2UWbL11RUlO4quE8238557491eab7d21b44da3db127e3d5e4`
  - Used for: headless browser automation

## Social Accounts

### Reddit
- **Primary allowed account:** `ken.li156@gmail.com`
- **Password:** `EX_3r=yC4&2z!G=`
  - Rule: This is the only Reddit account allowed for future RalphWorkflow marketing activity.
- **Historical/old account (do not use for future posting):** `ken@ralphworkflow.com`
- **Historical password:** `V9%)/A@=sXX-ebn`
  - Note: keep only for historical recovery context; do not post from this account.

### Apollo.io
- **Login username:** `ken@hireaegis.com`
- **Password:** `ngzcz!tS*jWo4dY1QjlZ@cxAd2r2c$Tf`
- **Verification mailbox:** `ken@ralphworkflow.com`
  - Rule: Apollo login uses `ken@hireaegis.com`, but device/email verification codes are expected in `ken@ralphworkflow.com`.

### IONOS Webmail
- **Login username:** `ken@hireaegis.com`
- **Password:** `GV%@iwClD4vetq`
- **IMAP host:** `imap.ionos.com:993`
  - Note: user provided on 2026-05-19 as a possible path to recover Apollo verification access; IMAP login was confirmed working.

## Git Hosting

- **Codeberg SSH:** `git@codeberg.org:RalphWorkflow/Ralph-Workflow.git`
  - Key: `~/.ssh/git_access` (ed25519)
- **GitHub SSH:** `git@github.com:Ralph-Workflow/Ralph-Workflow.git`
  - Key: `~/.ssh/id_ed25519` (ed25519)

## Model Quick Policy

- Default: `openai-codex/gpt-5.4`
- Use `minimax/MiniMax-M2.7-highspeed` only for clearly straightforward, low-judgment, easy-to-verify tasks
- If unsure, use `openai-codex/gpt-5.4`

## Related

- [Agent workspace](/concepts/agent-workspace)
