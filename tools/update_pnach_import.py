from pathlib import Path

path = Path("search.py")
text = path.read_text()

marker = "\ncurrent_toggle_identity = None\n"
if "def import_pnach_to_toggle(" in text:
    raise SystemExit("PNACH importer already present")

insert = r'''

PNACH_CHEATS_DIR = Path(
    "~/storage/shared/PPSSPP-PS2-GameHub/PS2/Armsx/cheats"
).expanduser()


def pnach_strip_name(name):
    name = str(name).strip()
    if len(name) >= 2 and name.startswith("[") and name.endswith("]"):
        return name[1:-1].strip()
    return name


def pnach_find_file(identity):
    directory = PNACH_CHEATS_DIR
    if not directory.is_dir():
        print()
        print(f"PNACH directory not found: {directory}")
        return None

    serial = str(identity["serial"]).strip().upper()
    crc = str(identity["crc"]).strip().upper()
    expected = f"{serial}_{crc}.pnach"

    for path in directory.iterdir():
        if path.is_file() and path.name.upper() == expected.upper():
            return path

    print()
    print(f"No PNACH found for current game:")
    print(f"  {expected}")
    return None


def pnach_parse_file(path):
    sections = []
    current = None

    for raw_line in path.read_text(errors="replace").splitlines():
        line = raw_line.strip()

        if not line or line.startswith("//"):
            continue

        if line.startswith("[") and line.endswith("]"):
            name = pnach_strip_name(line)
            current = {
                "name": name or "Unnamed Patch",
                "author": "",
                "description": "",
                "patches": [],
            }
            sections.append(current)
            continue

        if current is None:
            continue

        if line.lower().startswith("author="):
            current["author"] = line.split("=", 1)[1].strip()
            continue

        if line.lower().startswith("description="):
            current["description"] = line.split("=", 1)[1].strip()
            continue

        if not line.lower().startswith("patch="):
            continue

        parts = [part.strip() for part in line.split("=", 1)[1].split(",")]
        if len(parts) < 5:
            continue

        address_text = parts[2]
        patch_type = parts[3].lower()
        value_text = parts[4]

        type_map = {
            "extended": "dword",
            "dword": "dword",
            "word": "word",
            "byte": "byte",
            "qword": "qword",
        }
        typ = type_map.get(patch_type)
        if typ is None:
            continue

        try:
            address = int(address_text, 16)
            value = int(value_text, 16)
            parse_scan_number(str(address), "qword")
            parse_scan_number(str(value), typ)
        except Exception:
            continue

        current["patches"].append({
            "address": address,
            "type": typ,
            "value": value,
        })

    return [section for section in sections if section["patches"]]


def pnach_show_sections(sections):
    print()
    print("Available active cheats:")
    print()

    for i, section in enumerate(sections, 1):
        count = len(section["patches"])
        suffix = "patch" if count == 1 else "patches"
        print(f"[{i}] {section['name']} ({count} {suffix})")

        if section["author"]:
            print(f"    Author: {section['author']}")
        if section["description"]:
            print(f"    Description: {section['description']}")

    print()
    print("[a] Import all")
    print("[x] Cancel")


def pnach_select_sections(sections):
    pnach_show_sections(sections)
    choice = input("> ").strip().lower()

    if choice == "x":
        return []

    if choice == "a":
        return sections

    selected = []
    try:
        choices = [int(value.strip()) for value in choice.split(",")]
        for number in choices:
            if number < 1 or number > len(sections):
                raise ValueError
            section = sections[number - 1]
            if section not in selected:
                selected.append(section)
    except Exception:
        print("Invalid selection.")
        return None

    return selected


def pnach_category_choice(data):
    ensure_categories()

    print()
    print("Category:")
    print("[1] Uncategorized")
    print("[2] Create new category")
    print()

    choice = input("> ").strip().lower()

    if choice == "1" or not choice:
        return "Uncategorized"

    if choice == "2":
        name = input("Category name: ").strip()
        if not name:
            print("Using Uncategorized.")
            return "Uncategorized"
        if name == "Uncategorized":
            return "Uncategorized"
        if name in data["categories"]:
            return name
        data["categories"].append(name)
        return name

    print("Invalid category. Using Uncategorized.")
    return "Uncategorized"


def pnach_make_toggle_item(pine, section, category):
    addresses = []

    for patch in section["patches"]:
        item = {
            "address": patch["address"],
            "type": patch["type"],
            "value": patch["value"],
        }
        item["original_value"] = patch_read(pine, item)
        addresses.append(item)

    if len(addresses) == 1:
        return {
            "name": section["name"],
            "category": category,
            "address": addresses[0]["address"],
            "type": addresses[0]["type"],
            "value": addresses[0]["value"],
            "original_value": addresses[0]["original_value"],
            "enabled": False,
        }

    return {
        "name": section["name"],
        "category": category,
        "enabled": False,
        "addresses": addresses,
    }


def pnach_patch_signature(item):
    return tuple(
        (int(address["address"]), str(address.get("type", "dword")), int(address["value"]))
        for address in patch_addresses(item)
    )


def import_pnach_to_toggle(pine, identity, data):
    path = pnach_find_file(identity)
    if path is None:
        return

    try:
        sections = pnach_parse_file(path)
    except Exception as e:
        print(f"Could not read PNACH: {e}")
        return

    if not sections:
        print("PNACH contains no active patch sections.")
        return

    selected = pnach_select_sections(sections)
    if selected is None or not selected:
        return

    category = pnach_category_choice(data)
    existing = {
        (str(item.get("name", "")), pnach_patch_signature(item))
        for item in data.get("patches", [])
    }

    imported = 0
    skipped = 0

    for section in selected:
        try:
            item = pnach_make_toggle_item(
                pine,
                section,
                category
            )
            signature = (
                str(item.get("name", "")),
                pnach_patch_signature(item)
            )

            if signature in existing:
                skipped += 1
                continue

            data["patches"].append(item)
            existing.add(signature)
            imported += 1
        except Exception as e:
            print(f"Could not import {section['name']}: {e}")

    save_toggle_file(identity, data)

    print()
    print(f"PNACH: {path.name}")
    print(f"Imported: {imported}")
    if skipped:
        print(f"Already existed: {skipped}")
    print("PNACH import complete.")
'''

text = text.replace(marker, insert + marker, 1)

old_menu = '''        print("[1] Open file")
        print("[2] Edit file")
        print("[3] Create new file")
        print("[4] Single address")
        print("[5] View active patches")
        print("[6] Restore all")
        print("[x] Back")'''

new_menu = '''        print("[1] Open file")
        print("[2] Edit file")
        print("[3] Create new file")
        print("[4] Import PNACH to Toggle")
        print("[5] Single address")
        print("[6] View active patches")
        print("[7] Restore all")
        print("[x] Back")'''

if old_menu not in text:
    raise SystemExit("Toggle menu block not found")
text = text.replace(old_menu, new_menu, 1)

old_handlers = '''        elif choice == "3":
            create_toggle_file(
                pine,
                identity
            )

        elif choice == "4":
            single_address_toggle(
                pine,
                identity
            )

        elif choice == "5":
            if data is None:
                print("No toggle patch file.")
            else:
                toggle_patch_menu(
                    pine,
                    data
                )

        elif choice == "6":
            if data is None:
                print("No toggle patch file.")
            else:
                failed = 0
                for item in data.get("patches", []):
                    if item.get("enabled"):
                        try:
                            if not toggle_patch_off(
                                pine,
                                item
                            ):
                                failed += 1
                        except Exception as e:
                            failed += 1
                            print(
                                f"Restore error: {e}"
                            )

                save_toggle_file(
                    identity,
                    data
                )

                if failed:
                    print(
                        f"Restore completed with "
                        f"{failed} failed patch(es)."
                    )
                else:
                    print("All patches restored.")'''

new_handlers = '''        elif choice == "3":
            create_toggle_file(
                pine,
                identity
            )

        elif choice == "4":
            if data is None:
                print("No toggle patch file. Use [3] first.")
            else:
                import_pnach_to_toggle(
                    pine,
                    identity,
                    data
                )

        elif choice == "5":
            single_address_toggle(
                pine,
                identity
            )

        elif choice == "6":
            if data is None:
                print("No toggle patch file.")
            else:
                toggle_patch_menu(
                    pine,
                    data
                )

        elif choice == "7":
            if data is None:
                print("No toggle patch file.")
            else:
                failed = 0
                for item in data.get("patches", []):
                    if item.get("enabled"):
                        try:
                            if not toggle_patch_off(
                                pine,
                                item
                            ):
                                failed += 1
                        except Exception as e:
                            failed += 1
                            print(
                                f"Restore error: {e}"
                            )

                save_toggle_file(
                    identity,
                    data
                )

                if failed:
                    print(
                        f"Restore completed with "
                        f"{failed} failed patch(es)."
                    )
                else:
                    print("All patches restored.")'''

if old_handlers not in text:
    raise SystemExit("Toggle handler block not found")
text = text.replace(old_handlers, new_handlers, 1)

path.write_text(text)
print("search.py updated")
