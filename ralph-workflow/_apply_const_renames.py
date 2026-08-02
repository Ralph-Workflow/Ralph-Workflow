"""Patch consolidate_any.py to apply helper/class renames to constants block."""
from pathlib import Path

path = Path('.agent/tmp/consolidate_any.py')
src = path.read_text()

old = '''    constants_block_parts = []
    seen_const = set()
    sorted_constants = topo_sort_constants(all_top_constants)
    for name, const_source in sorted_constants:
        if name in seen_const:
            continue
        seen_const.add(name)
        constants_block_parts.append(const_source)'''

new = '''    constants_block_parts = []
    seen_const = set()
    sorted_constants = topo_sort_constants(all_top_constants)
    # Build a name -> filename map so we can apply per-file helper and
    # class renames to each constant's source. The constants block
    # emits ALL constants globally; renames are scoped to the file
    # that originally defined the constant.
    const_name_to_file = {}
    for f in files:
        _doc, _imports, helpers, _fixtures, _classes, top_constants, _tfs = parse_file(f)
        for cs in top_constants:
            try:
                node = ast.parse(cs).body[0]
                cname = None
                if isinstance(node, ast.Assign) and node.targets:
                    if isinstance(node.targets[0], ast.Name):
                        cname = node.targets[0].id
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    cname = node.target.id
                if cname and cname not in const_name_to_file:
                    const_name_to_file[cname] = f.name
            except Exception:
                pass
    for name, const_source in sorted_constants:
        if name in seen_const:
            continue
        seen_const.add(name)
        owner = const_name_to_file.get(name, "")
        new_const_source = const_source
        for old_name, new_name in file_to_helper_renames.get(owner, {}).items():
            new_const_source = re.sub(
                r"\\b{}\\b".format(re.escape(old_name)),
                new_name,
                new_const_source,
            )
        for old_name, new_name in file_to_class_renames.get(owner, {}).items():
            new_const_source = re.sub(
                r"\\b{}\\b".format(re.escape(old_name)),
                new_name,
                new_const_source,
            )
        constants_block_parts.append(new_const_source)'''

if old not in src:
    print('OLD NOT FOUND')
else:
    src = src.replace(old, new)
    path.write_text(src)
    print('OK')

import ast
ast.parse(open(path).read())
print('Parses OK')
