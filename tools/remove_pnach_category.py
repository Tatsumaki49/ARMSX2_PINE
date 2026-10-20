from pathlib import Path

path = Path("search.py")
text = path.read_text()

start = text.index("def pnach_category_choice(")
end = text.index("\ndef pnach_make_toggle_item(", start)
text = text[:start] + text[end + 1:]

old = '''def pnach_make_toggle_item(pine, section, category):
    addresses = []
'''
new = '''def pnach_make_toggle_item(pine, section):
    addresses = []
'''
if old not in text:
    raise SystemExit("pnach_make_toggle_item signature not found")
text = text.replace(old, new, 1)

text = text.replace('''            "category": category,\n''', '', 1)
text = text.replace('''            "category": category,\n''', '', 1)

old_call = '''    category = pnach_category_choice(data)
    existing = {
'''
new_call = '''    existing = {
'''
if old_call not in text:
    raise SystemExit("category call block not found")
text = text.replace(old_call, new_call, 1)

old_make = '''            item = pnach_make_toggle_item(
                pine,
                section,
                category
            )
'''
new_make = '''            item = pnach_make_toggle_item(
                pine,
                section
            )
'''
if old_make not in text:
    raise SystemExit("pnach_make_toggle_item call not found")
text = text.replace(old_make, new_make, 1)

path.write_text(text)
print("PNACH category support removed")
