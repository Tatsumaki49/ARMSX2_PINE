from pathlib import Path
import ast

path = Path("search.py")
text = path.read_text()

tree = ast.parse(text)
node = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "parse_scan_number"), None)
if node is None:
    raise SystemExit("parse_scan_number not found")

replacement = '''def parse_scan_number(text, typ):
    text = str(text).strip()

    if typ in ("float", "double"):
        return float(text)

    base = 16 if text.lower().startswith("0x") else 10
    value = int(text, base)

    limits = {
        "byte": (0, 0xFF),
        "word": (0, 0xFFFF),
        "dword": (0, 0xFFFFFFFF),
        "qword": (0, 0xFFFFFFFFFFFFFFFF),
    }

    if typ in limits:
        lo, hi = limits[typ]
        if not lo <= value <= hi:
            raise ValueError(f"{typ} value must be {lo}..{hi}.")

    return value

'''

lines = text.splitlines(True)
text = "".join(lines[:node.lineno - 1]) + replacement + "".join(lines[node.end_lineno:])

old = '''    item["address"] = int(item["address"])
    item["type"] = str(item.get("type", "dword")).lower()
    item["value"] = item.get("value", 0)

    if item["type"] in ("float", "double"):
        item["value"] = float(item["value"])
    else:
        item["value"] = int(item["value"])
'''
new = '''    item["address"] = parse_scan_number(item["address"], "qword")
    item["type"] = str(item.get("type", "dword")).lower()
    item["value"] = item.get("value", 0)

    if item["type"] in ("float", "double"):
        item["value"] = float(item["value"])
    else:
        item["value"] = parse_scan_number(item["value"], item["type"])
'''

if old not in text:
    raise SystemExit("normalize_address body did not match expected current version")

text = text.replace(old, new, 1)
ast.parse(text)
path.write_text(text)
