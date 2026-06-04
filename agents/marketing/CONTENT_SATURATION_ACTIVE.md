# Content Saturation Guardrail — ACTIVE

- **Activated:** 2026-06-04 06:20 CEST (audit #25)
- **Triggered by:** Content saturation gate wired in run.py and generate_content.py
- **Saturation threshold:** 40 live blog posts
- **Current live posts (2026-06-04):** ~47

## Enforcement

The saturation gate (`can_publish_now()`) checks live blog post count against the threshold.
When saturated:
- `run.py` redirects to SEO retrofit lane instead of generating new posts
- `generate_content.py` redirects to `seo_retrofit_lane.py` instead of creating new content
- No new blog posts will be generated until live count drops below 40

## Audit Trail

- **Audit #25 (2026-06-04 06:20 CEST):** Gate originally wired into run.py lines ~2424-2431 and generate_content.py lines ~432-441
- **Audit #26 (2026-06-04 10:39 CEST):** Verified gate is live code (not dead code). CONTENT_SATURATION_ACTIVE.md guardrail file CREATED — this was documented as created in audit #25 but was missing (fake-green). Repaired now.
