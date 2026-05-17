"""Fix RUF001 ambiguous ❯ character in test_claude_interactive_pty.py."""

path = "ralph-workflow/tests/test_claude_interactive_pty.py"

with open(path, "rb") as f:
    content = f.read()

# The ❯ character (U+276F) is 3 bytes in UTF-8: 0xE2 0x9D 0xAF
arrow_utf8 = "❯".encode("utf-8")
# Replace with the Python escape sequence ❯ (6 ASCII bytes)
escape_seq = b"\\u276f"

print(f"Arrow UTF-8 bytes: {repr(arrow_utf8)}")
print(f"Count of arrow in file: {content.count(arrow_utf8)}")

new_content = content.replace(arrow_utf8, escape_seq)
print(f"Changed: {new_content != content}")

with open(path, "wb") as f:
    f.write(new_content)
print("Written!")
