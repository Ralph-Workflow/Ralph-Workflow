with open('/home/mistlight/.openclaw/workspace/agents/marketing/seo_daily.py') as f:
    content = f.read()

# Find the broken f-string area
idx = content.find('delta_section_lines.append(f"')
if idx >= 0:
    print(f"Found at idx {idx}")
    print(repr(content[idx:idx+300]))
else:
    print("Not found by that string")

# Try to find it by the literal text
idx2 = content.find('_Comparing to previous report')
if idx2 >= 0:
    print(f"\n_Comparing found at idx {idx2}")
    print(repr(content[idx2-50:idx2+200]))
