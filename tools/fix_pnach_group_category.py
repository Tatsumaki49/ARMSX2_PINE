from pathlib import Path

path = Path("search.py")
text = path.read_text()
old = '''    return {
        "name": section["name"],
        "category": category,
        "enabled": False,
        "addresses": addresses,
    }
'''
new = '''    return {
        "name": section["name"],
        "enabled": False,
        "addresses": addresses,
    }
'''
if old not in text:
    raise SystemExit("remaining PNACH group category block not found")
text = text.replace(old, new, 1)
path.write_text(text)
