#!/usr/bin/env python3
"""Cross-link the start-here guide from all blog posts missing it."""
import os, sys

BLOG_DIR = "/home/mistlight/.openclaw/workspace/Ralph-Site/content/blog"
STARTHERE_SLUG = "your-first-overnight-task-start-here-guide"
STARTHERE_LINK = "[Start here: your first overnight task →](/blog/your-first-overnight-task-start-here-guide)"
# Shorter alt for posts that already have a standalone CTA line
START_HERE_SHORT = "[Start here: your first overnight task →](https://ralphworkflow.com/blog/your-first-overnight-task-start-here-guide)"

SKIP_PATTERNS = ["noindex", STARTHERE_SLUG]
SKIP_CONTAINS = [STARTHERE_SLUG, "/start)"]

def should_skip(filename, content):
    base = filename.lower()
    for pat in SKIP_PATTERNS:
        if pat in base:
            return True
    for pat in SKIP_CONTAINS:
        if pat in content:
            return True
    return False

def insert_starthere_link(content):
    """
    Insert the start-here guide link before the '## Related Posts' section.
    If no '## Related Posts' exists, insert at end.
    """
    link_block = f"\n{START_HERE_SHORT}\n"
    
    if "## Related Posts" in content:
        # Insert right before ## Related Posts, with a blank line separator
        return content.replace(
            "\n## Related Posts",
            f"{link_block}\n## Related Posts"
        )
    elif "## Related" in content:
        return content.replace(
            "\n## Related",
            f"{link_block}\n## Related"
        )
    else:
        # Append at end
        return content.rstrip() + "\n\n" + link_block.strip() + "\n"

modified = 0
skipped = 0
errors = []

for fname in sorted(os.listdir(BLOG_DIR)):
    if not fname.endswith(".md"):
        continue
    
    fpath = os.path.join(BLOG_DIR, fname)
    with open(fpath) as f:
        content = f.read()
    
    if should_skip(fname, content):
        skipped += 1
        continue
    
    # Double-check: is the link already in there?
    new_content = insert_starthere_link(content)
    
    if new_content == content:
        errors.append(f"No insertion point found in {fname}")
        continue
    
    with open(fpath, "w") as f:
        f.write(new_content)
    
    modified += 1
    print(f"  ✅ {fname}")

print(f"\nModified: {modified}, Skipped: {skipped}, Errors: {len(errors)}")
for e in errors:
    print(f"  ❌ {e}")
