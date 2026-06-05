# SEO Cannibalization Report
Generated: 2026-06-05 04:28:14 UTC

## Status: OK

**GitHub README is mirror notice — Codeberg has unique canonical content**

## GitHub README check
- Status: HTTP 200
- Characters: 830
- Has mirror marker ('This is a mirror'): ✅
- Has full content ('## What it does'): ✅
- Mirror template exists locally: ✅

## Fix status
- **Audit #33 (2026-06-05):** GitHub mirror README stripped to short mirror notice
- **Mechanism:** sync_to_github.sh post-sync hook overheads GitHub README after each push
- **Expected:** GitHub README ≈ 833 chars (mirror notice), Codeberg README ≈ 9,725 chars (full rich content)
- **Search provider monitoring:** Suspended (DDG HTTP 202, Brave 0 results). Resume when provider recovers.

## History
- 2026-06-05 04:27 — **ok** — CB wins:0 GH wins:0 neither:3 errors:0
- 2026-06-05 04:28 — **ok** — GitHub README is mirror notice — Codeberg has unique canonical content
