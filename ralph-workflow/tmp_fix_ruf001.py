"""Fix RUF001 ambiguous character issues."""
import re

path_pty = "ralph-workflow/ralph/agents/invoke/_pty_helpers.py"
path_test = "ralph-workflow/tests/test_claude_interactive_pty.py"

# Fix _pty_helpers.py
with open(path_pty, "rb") as f:
    content = f.read()

for line in content.split(b"\n"):
    if b"CHOICE_MENU_OPTION_RE" in line and b"compile" in line:
        print("Found line bytes:", repr(line))
        # Split on the ❯ escape sequence (6 ASCII bytes: ❯)
        parts = line.split(b"\\u276f")
        print(f"Parts: {[repr(p) for p in parts]}")
        fixed_parts = []
        for i, part in enumerate(parts):
            if i > 0:
                # After the ❯ sequence, fix remaining single-backslash regex escapes
                fixed_part = re.sub(rb"\\([sd.])", rb"\\\\\1", part)
                fixed_parts.append(fixed_part)
            else:
                fixed_parts.append(part)
        fixed_line = b"\\u276f".join(fixed_parts)
        print("Fixed line bytes:", repr(fixed_line))
        new_content = content.replace(line, fixed_line)
        print("Changed:", new_content != content)
        if new_content != content:
            with open(path_pty, "wb") as f:
                f.write(new_content)
            print("Written pty_helpers.py!")
        break
