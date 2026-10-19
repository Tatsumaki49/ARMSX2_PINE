import os
import sys
import json
import struct
import math
import threading
from pathlib import Path
from pine_client import Pine


DEFAULT_START = 0x00600000
DEFAULT_END = 0x00900000

EXTENDED_START = 0x20000000
EXTENDED_END = 0x22000000
CHUNK_WORDS = 16384

CACHE_FILE = Path("search_cache.json")
SAVE_FILE = Path("search_saved.json")

FREEZE_INTERVAL = 0.01

freezes = {}
freeze_lock = threading.Lock()
freeze_stop = threading.Event()
freeze_thread = None

def clear():
    os.system("clear")


def load_json(path, default):
    if not path.exists():
        return default

    try:
        with path.open() as f:
            return json.load(f)
    except:
        return default


def save_json(path, data):
    with path.open("w") as f:
        json.dump(data, f, indent=2)


# ==================== FREEZE ====================

def freeze_worker(pine):
    while not freeze_stop.is_set():

        with freeze_lock:
            active = list(freezes.values())

        for item in active:
            try:
                current = read_value(
                    pine,
                    item["address"],
                    item["type"]
                )

                if not values_match(
                    item["value"],
                    current,
                    item["type"]
                ):
                    write_value(
                        pine,
                        item,
                        item["value"],
                        verify=False
                    )
            except:
                pass

        freeze_stop.wait(FREEZE_INTERVAL)


def start_freeze(pine, item, value):
    global freeze_thread

    key = (
        item["address"],
        item["type"]
    )

    freeze_item = {
        "address": item["address"],
        "type": item["type"],
        "value": value
    }

    with freeze_lock:
        freezes[key] = freeze_item

    if (
        freeze_thread is None
        or not freeze_thread.is_alive()
    ):
        freeze_stop.clear()

        freeze_thread = threading.Thread(
            target=freeze_worker,
            args=(pine,),
            daemon=True
        )

        freeze_thread.start()


def stop_freeze(item):
    key = (
        item["address"],
        item["type"]
    )

    with freeze_lock:
        freezes.pop(key, None)


def stop_all_freezes():
    global freeze_thread

    freeze_stop.set()

    if (
        freeze_thread is not None
        and freeze_thread.is_alive()
    ):
        freeze_thread.join(timeout=1)

    with freeze_lock:
        freezes.clear()

    freeze_thread = None


def view_freezes(pine):
    while True:

        with freeze_lock:
            active = list(freezes.values())

        print()
        print("=" * 65)
        print("ACTIVE FREEZES")
        print("=" * 65)

        if not active:
            print("No active freezes.")
            return

        for i, item in enumerate(active, 1):

            try:
                current = read_value(
                    pine,
                    item["address"],
                    item["type"]
                )
            except:
                current = "?"

            print(
                f"[{i:5}] "
                f"0x{item['address']:08X} "
                f"{item['type']:6} "
                f"frozen={item['value']} "
                f"current={current}"
            )

        print()
        print("[u] Unfreeze one")
        print("[a] Unfreeze all")
        print("[x] Back")

        choice = input("> ").strip().lower()

        if choice == "x":
            return

        elif choice == "a":
            stop_all_freezes()
            print("All freezes removed.")

        elif choice == "u":

            try:
                n = int(
                    input("Pick freeze #: ")
                )

                if n < 1 or n > len(active):
                    print("Invalid selection.")
                    continue

                item = active[n - 1]

                stop_freeze(item)

                print(
                    f"Unfrozen "
                    f"0x{item['address']:08X} "
                    f"{item['type']}"
                )

            except:
                print("Invalid selection.")

        else:
            print("Invalid option.")


def freeze_prompt(pine, items, values):
    print()
    print("Freeze?")
    print("[1] Freeze")
    print("[2] No freeze")

    choice = input("> ").strip()

    if choice != "1":
        return

    for item in items:
        key = (
            item["address"],
            item["type"]
        )

        start_freeze(
            pine,
            item,
            values[key]
        )

    print(
        f"Frozen {len(items)} address(es)."
    )



# ==================== TERMINAL COLORS ====================

BOLD = "\033[1m"
RED = "\033[31m"
GREEN = "\033[32m"
RESET = "\033[0m"


def status_text(enabled):
    if enabled:
        return f"{BOLD}{GREEN}[ON]{RESET}"
    return f"{BOLD}{RED}[OFF]{RESET}"


# ==================== TOGGLE PATCHES ====================

TOGGLE_ROOT = Path("Toggle Patches")
toggle_runtime_originals = {}
toggle_lock = threading.Lock()


def sanitize_name(text):
    text = str(text).strip()
    if not text:
        return "Unknown Game"
    safe = "".join(
        c if c.isalnum() or c in " ._-[]" else "_"
        for c in text
    ).strip()
    return safe or "Unknown Game"


def get_game_title(pine):
    for name in ("title", "get_title", "game_title"):
        fn = getattr(pine, name, None)
        if callable(fn):
            try:
                value = fn()
                if value:
                    return str(value).strip()
            except:
                pass
    return "Unknown Game"


def get_pine_identity_value(pine, names):
    for name in names:
        fn = getattr(pine, name, None)
        if callable(fn):
            try:
                value = fn()
                if value:
                    return str(value).strip()
            except:
                pass
        elif fn:
            return str(fn).strip()
    return ""


def get_toggle_identity(pine):
    title = get_game_title(pine)

    serial = get_pine_identity_value(
        pine,
        ("serial", "get_serial", "game_serial")
    )
    crc = get_pine_identity_value(
        pine,
        ("crc", "get_crc", "game_crc")
    )

    identity_file = TOGGLE_ROOT / sanitize_name(title) / ".identity.json"

    saved = load_json(identity_file, {})
    if isinstance(saved, dict):
        serial = serial or str(saved.get("serial", "")).strip()
        crc = crc or str(saved.get("crc", "")).strip()

    if not serial or not crc:
        print()
        print("PINE did not provide the game serial/CRC.")
        print("Enter them once; they will be remembered for this game.")
        print(f"Game: {title}")

        if not serial:
            serial = input("Serial (example: SLUS-21351): ").strip()

        if not crc:
            crc = input("CRC (example: 0518D274): ").strip()

        if not serial or not crc:
            return None

        identity_file.parent.mkdir(parents=True, exist_ok=True)
        save_json(
            identity_file,
            {
                "game": title,
                "serial": serial,
                "crc": crc
            }
        )

    return {
        "game": title,
        "serial": serial,
        "crc": crc,
        "folder": TOGGLE_ROOT / sanitize_name(title),
        "file": (
            TOGGLE_ROOT
            / sanitize_name(title)
            / f"{sanitize_name(serial)}_{sanitize_name(crc)}.json"
        )
    }


def patch_addresses(item):
    if isinstance(item.get("addresses"), list):
        return item["addresses"]
    return [item]


def is_patch_group(item):
    return isinstance(item.get("addresses"), list)


def patch_address_count(item):
    return len(patch_addresses(item))


def patch_key(item):
    return (
        int(item["address"]),
        str(item.get("type", "dword"))
    )


def patch_read(pine, item):
    return read_value(
        pine,
        int(item["address"]),
        item.get("type", "dword")
    )


def patch_write(pine, item, value):
    return write_value(
        pine,
        {
            "address": int(item["address"]),
            "type": item.get("type", "dword")
        },
        value,
        verify=True
    )


def patch_label(item):
    if item.get("name"):
        return str(item["name"])

    if is_patch_group(item) and item["addresses"]:
        first = item["addresses"][0]
        return f"0x{int(first['address']):08X}"

    return f"0x{int(item['address']):08X}"


def normalize_address(item):
    if not isinstance(item, dict):
        raise ValueError("Patch address entry must be an object")

    item["address"] = int(item["address"])
    item["type"] = str(item.get("type", "dword")).lower()
    item["value"] = item.get("value", 0)

    if item["type"] in ("float", "double"):
        item["value"] = float(item["value"])
    else:
        item["value"] = int(item["value"])

    return item


def normalize_patch(item):
    if not isinstance(item, dict):
        raise ValueError("Patch must be an object")

    item["enabled"] = bool(item.get("enabled", False))

    if isinstance(item.get("addresses"), list):
        if not item["addresses"]:
            raise ValueError("Patch group must contain at least one address")

        item["addresses"] = [
            normalize_address(address)
            for address in item["addresses"]
        ]

        return item

    normalize_address(item)
    return item


def load_toggle_file(pine, identity):
    path = identity["file"]
    data = load_json(path, None)

    if data is None:
        return None

    if not isinstance(data, dict):
        print("Invalid toggle patch file.")
        return None

    patches = data.get("patches", [])
    if not isinstance(patches, list):
        print("Invalid patch list.")
        return None

    try:
        for item in patches:
            normalize_patch(item)
    except Exception as e:
        print(f"Invalid patch data: {e}")
        return None

    data["game"] = identity["game"]
    data["serial"] = identity["serial"]
    data["crc"] = identity["crc"]
    data["patches"] = patches
    return data


def save_toggle_file(identity, data):
    identity["folder"].mkdir(parents=True, exist_ok=True)
    save_json(identity["file"], data)


def capture_original_if_needed(pine, item):
    key = patch_key(item)

    with toggle_lock:
        if key in toggle_runtime_originals:
            return toggle_runtime_originals[key]

    if "original_value" in item:
        original = item["original_value"]
    else:
        original = patch_read(pine, item)
        item["original_value"] = original

    with toggle_lock:
        toggle_runtime_originals[key] = original

    return original


def capture_patch_originals(pine, item):
    for address in patch_addresses(item):
        capture_original_if_needed(pine, address)


def toggle_patch_on(pine, item):
    if is_patch_group(item):
        return toggle_patch_group_on(pine, item)

    capture_original_if_needed(pine, item)

    success, actual = patch_write(
        pine,
        item,
        item["value"]
    )

    if not success:
        print(
            f"FAILED: 0x{item['address']:08X} "
            f"requested={item['value']} readback={actual}"
        )
        return False

    start_freeze(
        pine,
        item,
        item["value"]
    )

    item["enabled"] = True
    return True


def toggle_patch_group_on(pine, item):
    addresses = patch_addresses(item)

    try:
        for address in addresses:
            capture_original_if_needed(pine, address)
    except Exception as e:
        print(f"Could not capture patch originals: {e}")
        return False

    written = []

    try:
        for address in addresses:
            success, actual = patch_write(
                pine,
                address,
                address["value"]
            )

            if not success:
                raise RuntimeError(
                    f"FAILED: 0x{address['address']:08X} "
                    f"requested={address['value']} readback={actual}"
                )

            written.append(address)

    except Exception as e:
        print(f"Patch group ON failed: {e}")

        for address in reversed(written):
            try:
                stop_freeze(address)
                original = capture_original_if_needed(
                    pine,
                    address
                )
                patch_write(
                    pine,
                    address,
                    original
                )
            except Exception:
                pass

        item["enabled"] = False
        return False

    for address in addresses:
        start_freeze(
            pine,
            address,
            address["value"]
        )

    item["enabled"] = True
    return True


def toggle_patch_off(pine, item):
    if is_patch_group(item):
        return toggle_patch_group_off(pine, item)

    original = capture_original_if_needed(
        pine,
        item
    )

    stop_freeze(item)

    success, actual = patch_write(
        pine,
        item,
        original
    )

    if not success:
        print(
            f"RESTORE FAILED: "
            f"0x{item['address']:08X} "
            f"requested={original} readback={actual}"
        )
        return False

    item["enabled"] = False
    return True


def toggle_patch_group_off(pine, item):
    addresses = patch_addresses(item)
    failed = []

    for address in addresses:
        try:
            original = capture_original_if_needed(
                pine,
                address
            )
            stop_freeze(address)

            success, actual = patch_write(
                pine,
                address,
                original
            )

            if not success:
                failed.append(
                    (
                        address,
                        original,
                        actual
                    )
                )
                start_freeze(
                    pine,
                    address,
                    address["value"]
                )

        except Exception as e:
            failed.append(
                (
                    address,
                    None,
                    str(e)
                )
            )
            start_freeze(
                pine,
                address,
                address["value"]
            )

    if failed:
        item["enabled"] = True
        print(
            f"RESTORE PARTIAL: {len(failed)} "
            f"of {len(addresses)} address(es) failed."
        )

        for address, original, actual in failed:
            print(
                f"  0x{address['address']:08X}: "
                f"requested={original} readback={actual}"
            )

        return False

    item["enabled"] = False
    return True


def toggle_patch_item(pine, item):
    if item.get("enabled"):
        return toggle_patch_off(pine, item)
    return toggle_patch_on(pine, item)


def auto_restore_or_activate(pine, data):
    patches = data.get("patches", [])
    changed = False

    for item in patches:
        try:
            normalize_patch(item)

            if item.get("enabled"):
                if toggle_patch_on(pine, item):
                    changed = True
        except Exception as e:
            print(
                f"Could not activate "
                f"{patch_label(item)}: {e}"
            )

    return changed


def show_toggle_patches(pine, data):
    patches = data.get("patches", [])
    
    print()
    print("=" * 65)
    print("TOGGLE PATCHES")
    print("=" * 65)

    if not patches:
        print("No patches in this file.")
        return

    for i, item in enumerate(patches, 1):
        count = patch_address_count(item)
        suffix = (
            f"{count} address" if count == 1
            else f"{count} addresses"
        )

        print(
            f"[{i}] {BOLD}{patch_label(item)}{RESET} "
            f"{status_text(item.get('enabled'))} "
            f"({suffix})"
        )

        for address in patch_addresses(item):
            print(
                f"    0x{address['address']:08X} "
                f"{address['type']} "
                f"-> {address['value']}"
            )

    print()
    print("[x] Back")


def toggle_patch_menu(pine, data):
    while True:
        show_toggle_patches(pine, data)

        choice = input("> ").strip().lower()

        if choice == "x":
            return

        try:
            n = int(choice)
            if n < 1 or n > len(data.get("patches", [])):
                raise ValueError

            item = data["patches"][n - 1]

            if toggle_patch_item(pine, item):
                clear()
                save_toggle_file(
                    current_toggle_identity,
                    data
                )

                print(
                    f"{BOLD}{patch_label(item)}{RESET} -> "
                    f"{status_text(item.get('enabled'))}"
                )
        except ValueError:
            print("Invalid selection.")
        except Exception as e:
            print(f"Toggle error: {e}")


def choose_patch_type(default="dword"):
    print()
    print("[1] byte")
    print("[2] word")
    print("[3] dword")
    print("[4] qword")
    print("[5] float")
    print("[6] double")

    choice = input("Type [3]: ").strip()

    if not choice:
        return default

    return {
        "1": "byte",
        "2": "word",
        "3": "dword",
        "4": "qword",
        "5": "float",
        "6": "double"
    }.get(choice, default)


def parse_patch_value(text, typ):
    if typ in ("float", "double"):
        return float(text)
    return int(text, 0)


def input_patch_address(pine, address_text, typ, value):
    address = int(address_text, 0)
    item = {
        "address": address,
        "type": typ,
        "value": value
    }
    original = patch_read(pine, item)
    item["original_value"] = original
    return item


def create_toggle_file(pine, identity):
    path = identity["file"]

    if path.exists():
        print()
        print(f"File already exists: {path}")
        return

    data = {
        "game": identity["game"],
        "serial": identity["serial"],
        "crc": identity["crc"],
        "patches": []
    }

    save_toggle_file(identity, data)

    print()
    print(f"Created: {path}")


def add_patch_interactive(pine, data):
    name = input("Patch name: ").strip()

    addresses = []

    while True:
        address_text = input(
            "Address (blank to finish): "
        ).strip()

        if not address_text:
            break

        typ = choose_patch_type()
        value_text = input("Target value: ").strip()

        try:
            value = parse_patch_value(value_text, typ)
            item = input_patch_address(
                pine,
                address_text,
                typ,
                value
            )
            addresses.append(item)
            print(
                f"Added address 0x{item['address']:08X}; "
                f"current={item['original_value']}"
            )
        except Exception as e:
            print(f"Invalid address/value: {e}")

    if not addresses:
        print("No addresses added.")
        return False

    if len(addresses) == 1:
        item = addresses[0]
        item["name"] = name or f"0x{item['address']:08X}"
        item["enabled"] = False
        data["patches"].append(item)
    else:
        data["patches"].append({
            "name": name or "Unnamed Patch",
            "enabled": False,
            "addresses": addresses
        })

    return True


def edit_toggle_file(pine, identity, data):
    while True:
        print()
        print("=" * 65)
        print("EDIT TOGGLE PATCH FILE")
        print("=" * 65)
        print(f"File: {identity['file']}")
        print()

        patches = data.get("patches", [])

        for i, item in enumerate(patches, 1):
            count = patch_address_count(item)
            suffix = (
                f"{count} address" if count == 1
                else f"{count} addresses"
            )
            print(
                f"[{i}] {patch_label(item)} "
                f"{status_text(item.get('enabled'))} "
                f"({suffix})"
            )

        print()
        print("[1] Add patch")
        print("[2] Rename patch")
        print("[3] Change target value")
        print("[4] Delete patch")
        print("[x] Back")

        choice = input("> ").strip().lower()

        if choice == "x":
            save_toggle_file(identity, data)
            return

        if choice == "1":
            try:
                if add_patch_interactive(pine, data):
                    save_toggle_file(identity, data)
                    print("Patch added.")
            except Exception as e:
                print(f"Could not add patch: {e}")
            continue

        if choice not in ("2", "3", "4"):
            print("Invalid option.")
            continue

        if not patches:
            print("No patches.")
            continue

        try:
            n = int(input("Patch #: "))
            if n < 1 or n > len(patches):
                raise ValueError
            item = patches[n - 1]
        except:
            print("Invalid selection.")
            continue

        if choice == "2":
            name = input("New name: ").strip()
            if name:
                item["name"] = name

        elif choice == "3":
            addresses = patch_addresses(item)
            for address in addresses:
                print(
                    f"0x{address['address']:08X} "
                    f"{address['type']} "
                    f"current target={address['value']}"
                )

            try:
                n2 = int(input("Address #: "))
                if n2 < 1 or n2 > len(addresses):
                    raise ValueError
                address = addresses[n2 - 1]
            except:
                print("Invalid selection.")
                continue

            value_text = input("New target value: ").strip()
            try:
                address["value"] = parse_patch_value(
                    value_text,
                    address["type"]
                )
            except:
                print("Invalid value.")
                continue

        elif choice == "4":
            if item.get("enabled"):
                if not toggle_patch_off(pine, item):
                    print("Patch was not deleted because restore failed.")
                    continue

            patches.pop(n - 1)

        save_toggle_file(identity, data)


def single_toggle_cache_clear():
    single_toggle_cache.clear()


single_toggle_cache = {}


def single_address_toggle(pine, identity):
    while True:
        print()
        print("=" * 65)
        print("SINGLE ADDRESS TOGGLES")
        print("=" * 65)

        items = list(single_toggle_cache.values())

        if items:
            for i, item in enumerate(items, 1):
                state = "ON" if item.get("enabled") else "Off"
                print(
                    f"[{i}] {patch_label(item)} "
                    f"({state})"
                )
                print(
                    f"    0x{item['address']:08X} "
                    f"{item['type']} "
                    f"target={item['value']} "
                    f"restore={item['original_value']}"
                )
        else:
            print("No cached single-address patches.")

        print()
        print("[a] Add address")
        print("[r] Remove cached address")
        print("[x] Back")

        choice = input("> ").strip().lower()

        if choice == "x":
            return

        if choice == "a":
            address_text = input(
                "Address (example: 0x0088D70B): "
            ).strip()

            try:
                address = int(address_text, 0)
            except:
                print("Invalid address.")
                continue

            typ = choose_patch_type()

            probe = {
                "address": address,
                "type": typ
            }

            try:
                original = patch_read(
                    pine,
                    probe
                )
            except Exception as e:
                print(f"Could not read address: {e}")
                continue

            print(f"Current value: {original}")

            value_text = input(
                "Target value: "
            ).strip()

            try:
                value = parse_patch_value(
                    value_text,
                    typ
                )
            except:
                print("Invalid value.")
                continue

            item = {
                "name": f"0x{address:08X}",
                "address": address,
                "type": typ,
                "value": value,
                "original_value": original,
                "enabled": False
            }

            key = patch_key(item)
            single_toggle_cache[key] = item

            print(
                "Cached. Select it from the list to toggle."
            )
            continue

        if choice == "r":
            if not items:
                print("Nothing cached.")
                continue

            try:
                n = int(
                    input("Cached address #: ")
                )
                item = items[n - 1]
            except:
                print("Invalid selection.")
                continue

            if item.get("enabled"):
                if not toggle_patch_off(pine, item):
                    continue

            single_toggle_cache.pop(
                patch_key(item),
                None
            )

            print("Removed from single-address cache.")
            continue

        try:
            n = int(choice)
            if n < 1 or n > len(items):
                raise ValueError

            item = items[n - 1]

            if toggle_patch_item(pine, item):
                print(
                    f"{BOLD}{patch_label(item)}{RESET} -> "
                    f"{status_text(item.get('enabled'))}"
                )

        except ValueError:
            print("Invalid option.")
        except Exception as e:
            print(f"Toggle error: {e}")


def open_toggle_file(pine, identity):
    data = load_toggle_file(
        pine,
        identity
    )

    if data is None:
        print()
        print("No toggle patch file exists yet.")
        print(
            f"Use [3] Create new file first:\n"
            f"{identity['file']}"
        )
        return

    for item in data.get("patches", []):
        try:
            capture_patch_originals(
                pine,
                item
            )
        except Exception as e:
            print(
                f"Could not read "
                f"{patch_label(item)}: {e}"
            )

    auto_restore_or_activate(
        pine,
        data
    )

    save_toggle_file(
        identity,
        data
    )

    toggle_patch_menu(
        pine,
        data
    )


current_toggle_identity = None


def toggle_patches(pine):
    global current_toggle_identity

    identity = get_toggle_identity(pine)

    if not identity:
        return

    current_toggle_identity = identity

    while True:
        data = load_toggle_file(
            pine,
            identity
        )

        print()
        print("=" * 65)
        print("TOGGLE PATCHES")
        print("=" * 65)
        print(f"Game:   {identity['game']}")
        print(f"Serial: {identity['serial']}")
        print(f"CRC:    {identity['crc']}")
        print(f"File:   {identity['file']}")
        print()
        print("[1] Open file")
        print("[2] Edit file")
        print("[3] Create new file")
        print("[4] Single address")
        print("[5] View active patches")
        print("[6] Restore all")
        print("[x] Back")

        choice = input("> ").strip().lower()

        if choice == "1":
            open_toggle_file(
                pine,
                identity
            )

        elif choice == "2":
            if data is None:
                print("No file yet. Use [3] first.")
            else:
                edit_toggle_file(
                    pine,
                    identity,
                    data
                )

        elif choice == "3":
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
                    print("All patches restored.")

        elif choice == "x":
            return

        else:
            print("Invalid option.")


# ==================== SEARCH ====================

SCAN_TYPES = (
    "byte",
    "word",
    "dword",
    "qword",
    "float",
    "double",
)

SCAN_MODES = (
    "exact",
    "increased",
    "decreased",
    "changed",
    "unchanged",
)


def choose_scan_type(default="all"):
    print()
    print("[1] All / Unknown Type")
    print("[2] byte")
    print("[3] word")
    print("[4] dword")
    print("[5] qword")
    print("[6] float")
    print("[7] double")
    choice = input(f"Type [{default}]: ").strip().lower()
    if not choice:
        return default
    return {"1":"all","2":"byte","3":"word","4":"dword","5":"qword","6":"float","7":"double"}.get(choice, default)


def parse_scan_number(text, typ):
    text = str(text).strip()
    if typ in ("float", "double"):
        return float(text)
    return int(text, 0)


def get_scan_tolerance(typ):
    if typ not in ("all", "float", "double"):
        return 0.0
    text = input("Float tolerance [0]: ").strip()
    try:
        value = float(text) if text else 0.0
        if not math.isfinite(value) or value < 0:
            raise ValueError
        return value
    except Exception:
        print("Invalid tolerance.")
        return None


def choose_scan_ranges():
    while True:
        print()
        print("Scan range:")
        print("[1] Default RAM")
        print(f"    0x{DEFAULT_START:08X} - 0x{DEFAULT_END:08X}")
        print()
        print("[2] Extended 0x20 region")
        print(f"    0x{EXTENDED_START:08X} - 0x{EXTENDED_END:08X}")
        print()
        print("[3] Custom range")
        print("[4] All configured ranges")
        print("[x] Back")
        choice = input("> ").strip().lower()
        if choice == "1":
            return [("Default RAM", DEFAULT_START, DEFAULT_END)]
        if choice == "2":
            return [("Extended 0x20 region", EXTENDED_START, EXTENDED_END)]
        if choice == "3":
            try:
                start = int(input("Start address (example: 0x20A00000): ").strip(), 0)
                end = int(input("End address (example: 0x20B00000): ").strip(), 0)
                if start >= end:
                    raise ValueError
                return [("Custom range", start, end)]
            except Exception:
                print("Invalid address range.")
        elif choice == "4":
            return [
                ("Default RAM", DEFAULT_START, DEFAULT_END),
                ("Extended 0x20 region", EXTENDED_START, EXTENDED_END),
            ]
        elif choice == "x":
            return None
        else:
            print("Invalid option.")


def unpack_scan_values(data, typ):
    if typ == "byte":
        return data, 1, "<B"
    if typ == "word":
        return data, 2, "<H"
    if typ == "dword":
        return data, 4, "<I"
    if typ == "qword":
        return data, 8, "<Q"
    if typ == "float":
        return data, 4, "<f"
    if typ == "double":
        return data, 8, "<d"
    raise ValueError(f"Unknown scan type: {typ}")


def scan_range(pine, start, end, typ, target, tolerance, results):
    addr = start
    total = max(end - start, 1)
    scanned = 0
    size = {"byte": 1, "word": 2, "dword": 4, "qword": 8, "float": 4, "double": 8}[typ]

    while addr < end:
        remaining = end - addr
        words = min(CHUNK_WORDS, remaining // 4)
        if words <= 0:
            break
        try:
            data = pine.read_block(addr, words)
        except Exception:
            addr += words * 4
            scanned += words * 4
            percent = min(scanned / total * 100, 100)
            print(f"Scanning {percent:6.2f}% | results: {len(results):,}", end="\r", flush=True)
            continue

        limit = len(data) - size + 1
        step = size
        if typ == "byte":
            step = 1
        for o in range(0, max(limit, 0), step):
            try:
                value = struct.unpack_from({
                    "byte": "<B",
                    "word": "<H",
                    "dword": "<I",
                    "qword": "<Q",
                    "float": "<f",
                    "double": "<d",
                }[typ], data, o)[0]
            except struct.error:
                break
            if typ in ("float", "double") and not math.isfinite(value):
                continue
            if typ in ("float", "double"):
                match = abs(value - float(target)) <= tolerance
            else:
                match = value == target
            if match:
                results.append({
                    "address": addr + o,
                    "type": typ,
                    "value": value,
                })

        scanned += len(data)
        percent = min(scanned / total * 100, 100)
        print(f"Scanning {percent:6.2f}% | results: {len(results):,}", end="\r", flush=True)
        addr += len(data)


def find_value(pine):
    ranges = choose_scan_ranges()
    if not ranges:
        return
    typ = choose_scan_type()
    print()
    text = input("Find exact value: ").strip()
    if typ == "all":
        try:
            float(text)
        except Exception:
            print("Invalid value.")
            return
    else:
        try:
            target = parse_scan_number(text, typ)
        except Exception:
            print("Invalid value.")
            return
    tolerance = get_scan_tolerance(typ)
    if tolerance is None:
        return
    results = []
    scan_types = SCAN_TYPES if typ == "all" else (typ,)
    print(f"First scan: {typ} == {text}")
    for scan_typ in scan_types:
        try:
            scan_target = parse_scan_number(text, scan_typ)
        except Exception:
            continue
        print()
        print(f"Type: {scan_typ}")
        for name, start, end in ranges:
            print()
            print(f"{name}")
            print(f"0x{start:08X} - 0x{end:08X}")
            scan_range(pine, start, end, scan_typ, scan_target, tolerance if scan_typ in ("float", "double") else 0.0, results)
    print(f"Found {len(results):,} results.")
    save_json(CACHE_FILE, {
        "scan_type": typ,
        "scan_mode": "exact",
        "target": target if typ != "all" else text,
        "target_text": text,
        "tolerance": tolerance,
        "ranges": [{"name": n, "start": s, "end": e} for n, s, e in ranges],
        "results": results,
    })


def read_value(pine, addr, typ):
    if typ == "byte":
        return pine.read8(addr)
    if typ == "word":
        return pine.read16(addr)
    if typ == "dword":
        return pine.read32(addr)
    if typ == "qword":
        return pine.read64(addr)
    if typ == "float":
        fn = getattr(pine, "readfloat", None)
        return fn(addr) if callable(fn) else struct.unpack("<f", struct.pack("<I", pine.read32(addr)))[0]
    if typ == "double":
        fn = getattr(pine, "readdouble", None)
        return fn(addr) if callable(fn) else struct.unpack("<d", struct.pack("<Q", pine.read64(addr)))[0]
    raise ValueError(f"Unknown value type: {typ}")


def compare_scan(current, previous, mode, target, typ, tolerance):
    if mode == "exact":
        if typ in ("float", "double"):
            return abs(current - float(target)) <= tolerance
        return current == target
    if mode == "increased":
        return current > previous + (tolerance if typ in ("float", "double") else 0)
    if mode == "decreased":
        return current < previous - (tolerance if typ in ("float", "double") else 0)
    if mode == "changed":
        return abs(current - previous) > tolerance if typ in ("float", "double") else current != previous
    if mode == "unchanged":
        return abs(current - previous) <= tolerance if typ in ("float", "double") else current == previous
    return False


def get_next_mode():
    print()
    print("[1] Exact value")
    print("[2] Increased")
    print("[3] Decreased")
    print("[4] Changed")
    print("[5] Unchanged")
    choice = input("> ").strip()
    return {"1": "exact", "2": "increased", "3": "decreased", "4": "changed", "5": "unchanged"}.get(choice)


def run_next_scan(pine, mode=None):
    cache = load_json(CACHE_FILE, None)
    if not cache or not cache.get("results"):
        print("No current search. Use [f] first.")
        return
    scan_typ = cache.get("scan_type", "dword")
    if scan_typ != "all" and scan_typ not in SCAN_TYPES:
        print("Unsupported cached scan type.")
        return
    if mode is None:
        mode = get_next_mode()
        if not mode:
            print("Invalid scan mode.")
            return
    tolerance = float(cache.get("tolerance", 0.0) or 0.0)
    exact_text = None
    exact_target = cache.get("target")
    if mode == "exact":
        exact_text = input("Find exact value: ").strip()
        if not exact_text:
            print("Invalid value.")
            return
        if scan_typ == "all":
            try:
                float(exact_text)
            except Exception:
                print("Invalid value.")
                return
            tolerance = get_scan_tolerance("all")
        else:
            try:
                exact_target = parse_scan_number(exact_text, scan_typ)
            except Exception:
                print("Invalid value.")
                return
            tolerance = get_scan_tolerance(scan_typ)
        if tolerance is None:
            return
    new_results = []
    print()
    print(f"Next scan: {mode} ({scan_typ})")
    for item in cache["results"]:
        item_typ = item.get("type", scan_typ)
        if item_typ not in SCAN_TYPES:
            continue
        try:
            current = read_value(pine, item["address"], item_typ)
        except Exception:
            continue
        previous = item.get("value", current)
        if item_typ in ("float", "double") and not math.isfinite(current):
            continue
        item_target = exact_target
        item_tolerance = tolerance if item_typ in ("float", "double") else 0.0
        if mode == "exact":
            try:
                item_target = parse_scan_number(exact_text, item_typ)
            except Exception:
                continue
        if compare_scan(current, previous, mode, item_target, item_typ, item_tolerance):
            new_results.append({"address": item["address"], "type": item_typ, "value": current, "previous": previous})
    cache["scan_mode"] = mode
    if mode == "exact":
        cache["target"] = exact_target if scan_typ != "all" else exact_text
        cache["target_text"] = exact_text
    cache["tolerance"] = tolerance
    cache["results"] = new_results
    save_json(CACHE_FILE, cache)
    show_results(new_results)
    print(f"Remaining: {len(new_results):,}")


def next_scan(pine):
    while True:
        print()
        print("=" * 65)
        print("NEXT SCAN")
        print("=" * 65)
        print("[1] Exact value")
        print("[2] Increased")
        print("[3] Decreased")
        print("[4] Changed")
        print("[5] Unchanged")
        print("[x] Exit")
        choice = input("> ").strip().lower()
        if choice == "x":
            return
        mode = {"1": "exact", "2": "increased", "3": "decreased", "4": "changed", "5": "unchanged"}.get(choice)
        if mode:
            run_next_scan(pine, mode)
        else:
            print("Invalid option.")


def show_results(results):
    if not results:
        print("No current addresses.")
        return
    print()
    print("=" * 65)
    print("CURRENT ADDRESSES")
    print("=" * 65)
    for i, item in enumerate(results, 1):
        previous = item.get("previous")
        if previous is None:
            print(f"[{i:5}] 0x{item['address']:08X} {item['type']:7} {item['value']}")
        else:
            print(f"[{i:5}] 0x{item['address']:08X} {item['type']:7} {previous} -> {item['value']}")


# ==================== EDIT ====================

def edit_one(pine, results):
    show_results(results)

    try:
        n = int(
            input("\nPick address #: ")
        )

        item = results[n - 1]

    except:
        print(
            "Invalid selection."
        )
        return

    print(
        f"\nSelected: "
        f"0x{item['address']:08X} "
        f"{item['type']} "
        f"current={item['value']}"
    )

    new_text = input(
        "New value: "
    ).strip()

    try:
        new_value = (
            float(new_text)
            if item["type"] == "float"
            else int(new_text, 0)
        )

    except:
        print(
            "Invalid value."
        )
        return

    try:
        success, actual = write_value(
            pine,
            item,
            new_value,
            verify=True
        )
    except Exception as e:
        print(f"Write error: {e}")
        return

    if not success:
        print(
            f"WRITE FAILED: requested={new_value} "
            f"but read back={actual}"
        )
        print("Freeze was not started because the write was not verified.")
        return

    item["value"] = actual
    item["edited_value"] = new_value

    print(f"Edited and verified: {actual}")

    freeze_prompt(
        pine,
        [item],
        {
            (
                item["address"],
                item["type"]
            ): new_value
        }
    )


def edit_batch(pine, results):
    show_results(results)

    text = input(
        "\nPick addresses "
        "(example: 1,3,5-8): "
    ).strip()

    selected = set()

    try:

        for part in text.split(","):

            part = part.strip()

            if "-" in part:

                a, b = part.split(
                    "-",
                    1
                )

                for n in range(
                    int(a),
                    int(b) + 1
                ):
                    selected.add(n)

            else:
                selected.add(
                    int(part)
                )

    except:
        print(
            "Invalid selection."
        )
        return

    selected = sorted(
        selected
    )

    if not selected:
        return

    new_text = input(
        "New value: "
    ).strip()

    try:

        new_value = (
            float(new_text)
            if results[
                selected[0] - 1
            ]["type"] == "float"
            else int(new_text, 0)
        )

    except:
        print(
            "Invalid value."
        )
        return

    values = {}
    valid_items = []

    for n in selected:

        if n < 1 or n > len(results):
            continue

        item = results[n - 1]

        try:
            success, actual = write_value(
                pine,
                item,
                new_value,
                verify=True
            )
        except Exception as e:
            print(
                f"Write error at "
                f"0x{item['address']:08X}: {e}"
            )
            continue

        if not success:
            print(
                f"WRITE FAILED at "
                f"0x{item['address']:08X} "
                f"({item['type']}): "
                f"requested={new_value}, "
                f"read back={actual}"
            )
            continue

        item["value"] = actual
        item["edited_value"] = new_value

        key = (
            item["address"],
            item["type"]
        )

        values[key] = new_value
        valid_items.append(item)

    print(
        f"Edited and verified "
        f"{len(valid_items)} "
        f"addresses."
    )

    if valid_items:
        freeze_prompt(
            pine,
            valid_items,
            values
        )


def values_match(expected, actual, typ):
    if typ in ("float", "double"):
        return math.isclose(
            float(expected),
            float(actual),
            rel_tol=1e-6,
            abs_tol=1e-6
        )

    return int(expected) == int(actual)


def write_value(pine, item, value, verify=True):
    """
    Write one value through PINE and optionally verify it immediately.
    Returns: (success, actual_value)
    """
    addr = item["address"]
    typ = item["type"]

    if typ == "byte":
        value = int(value)

        if not 0 <= value <= 0xFF:
            raise ValueError("Byte value must be 0..255")

        pine.write8(addr, value)

    elif typ == "word":
        value = int(value)

        if not 0 <= value <= 0xFFFF:
            raise ValueError("Word value must be 0..65535")

        pine.write16(addr, value)

    elif typ == "dword":
        value = int(value)

        if not 0 <= value <= 0xFFFFFFFF:
            raise ValueError("Dword value must be 0..4294967295")

        # Same operation as:
        # p.write32(0x00780378, 1000000)
        pine.write32(addr, value)

    elif typ == "qword":
        value = int(value)

        if not 0 <= value <= 0xFFFFFFFFFFFFFFFF:
            raise ValueError(
                "Qword value must be 0..18446744073709551615"
            )

        pine.write32(addr, value & 0xFFFFFFFF)
        pine.write32(
            addr + 4,
            (value >> 32) & 0xFFFFFFFF
        )

    elif typ == "float":
        value = float(value)

        if not math.isfinite(value):
            raise ValueError("Float value must be finite")

        raw = struct.unpack(
            "<I",
            struct.pack("<f", value)
        )[0]

        pine.write32(addr, raw)

    elif typ == "double":
        value = float(value)
        if not math.isfinite(value):
            raise ValueError("Double value must be finite")
        raw = struct.unpack("<Q", struct.pack("<d", value))[0]
        pine.write64(addr, raw)

    else:
        raise ValueError(f"Unknown value type: {typ}")

    if not verify:
        return True, None

    actual = read_value(pine, addr, typ)

    return values_match(value, actual, typ), actual


def edit(pine):
    cache = load_json(
        CACHE_FILE,
        None
    )

    if (
        not cache
        or not cache.get("results")
    ):
        print(
            "No current search. "
            "Use [f] first."
        )
        return

    results = cache["results"]

    while True:

        print()
        print(
            "[1] Edit one address"
        )
        print(
            "[2] Edit batch"
        )
        print(
            "[x] Back"
        )

        choice = input(
            "> "
        ).strip().lower()

        if choice == "1":
            edit_one(
                pine,
                results
            )

        elif choice == "2":
            edit_batch(
                pine,
                results
            )

        elif choice == "x":
            break

        else:
            print(
                "Invalid option."
            )

    cache["results"] = results

    save_json(
        CACHE_FILE,
        cache
    )


# ==================== ADDRESS TABLE ====================


def add_results_to_table():
    cache = load_json(CACHE_FILE, None)
    if not cache or not cache.get("results"):
        print("No current search.")
        return
    saved = load_json(SAVE_FILE, [])
    existing = {(x["address"], x["type"]) for x in saved}
    print()
    text = input("Addresses to add (example: 1,3,5-8 or 'all'): ").strip().lower()
    if text == "all":
        selected = list(range(1, len(cache["results"]) + 1))
    else:
        selected = set()
        try:
            for part in text.split(","):
                part = part.strip()
                if not part:
                    continue
                if "-" in part:
                    a, b = part.split("-", 1)
                    selected.update(range(int(a), int(b) + 1))
                else:
                    selected.add(int(part))
        except Exception:
            print("Invalid selection.")
            return
        selected = sorted(selected)
    added = 0
    for n in selected:
        if n < 1 or n > len(cache["results"]):
            continue
        item = dict(cache["results"][n - 1])
        key = (item["address"], item["type"])
        if key not in existing:
            item["description"] = f"0x{item['address']:08X}"
            item["frozen"] = False
            saved.append(item)
            existing.add(key)
            added += 1
    save_json(SAVE_FILE, saved)
    print(f"Added {added} address(es). Total table: {len(saved)}")


def address_table(pine):
    while True:
        saved = load_json(SAVE_FILE, [])
        print()
        print("=" * 65)
        print("ADDRESS TABLE")
        print("=" * 65)
        if not saved:
            print("No addresses in table.")
        else:
            for i, item in enumerate(saved, 1):
                current = item.get("value", "?")
                try:
                    current = read_value(pine, item["address"], item["type"])
                except Exception:
                    pass
                frozen = "ON" if item.get("frozen") else "off"
                print(f"[{i:3}] {item.get('description', '0x%08X' % item['address'])} | 0x{item['address']:08X} | {item['type']:7} | {current} | freeze={frozen}")
        print()
        print("[a] Add from current scan")
        print("[e] Edit value")
        print("[f] Freeze/unfreeze")
        print("[r] Remove")
        print("[x] Back")
        choice = input("> ").strip().lower()
        if choice == "x":
            return
        if choice == "a":
            add_results_to_table()
            continue
        if not saved:
            print("Address table is empty.")
            continue
        try:
            n = int(input("Address #: "))
            item = saved[n - 1]
        except Exception:
            print("Invalid selection.")
            continue
        if choice == "r":
            stop_freeze(item)
            saved.pop(n - 1)
            save_json(SAVE_FILE, saved)
        elif choice == "f":
            if item.get("frozen"):
                stop_freeze(item)
                item["frozen"] = False
            else:
                current = read_value(pine, item["address"], item["type"])
                item["value"] = current
                start_freeze(pine, item, current)
                item["frozen"] = True
            save_json(SAVE_FILE, saved)
        elif choice == "e":
            text = input("New value: ").strip()
            try:
                value = parse_patch_value(text, item["type"])
                ok, actual = write_value(pine, item, value, verify=True)
                if not ok:
                    raise ValueError(f"readback={actual}")
                item["value"] = actual
                save_json(SAVE_FILE, saved)
            except Exception as e:
                print(f"Write failed: {e}")
        else:
            print("Invalid option.")


# ==================== SAVE ====================

def save_current():
    cache = load_json(
        CACHE_FILE,
        None
    )

    if (
        not cache
        or not cache.get("results")
    ):
        print(
            "No current search."
        )
        return

    saved = load_json(
        SAVE_FILE,
        []
    )

    existing = {
        (
            x["address"],
            x["type"]
        )
        for x in saved
    }

    added = 0

    for item in cache["results"]:

        key = (
            item["address"],
            item["type"]
        )

        if key not in existing:

            saved.append(item)
            existing.add(key)
            added += 1

    save_json(
        SAVE_FILE,
        saved
    )

    print(
        f"Saved {added} "
        f"new addresses."
    )

    print(
        f"Total saved: "
        f"{len(saved)}"
    )


def clear_current():
    if CACHE_FILE.exists():
        CACHE_FILE.unlink()

    print(
        "Current search cleared."
    )


# ==================== MAIN ====================

def main():
    pine = Pine()

    print()
    print("=" * 33)
    print(f"{BOLD}       PINE MEMORY SEARCH{RESET}")
    print("=" * 33)
    print("")
    print(f"{BOLD}{pine.title()} {pine.serial()} {pine.crc()}{RESET}")

    while True:

        cache = load_json(
            CACHE_FILE,
            None
        )

        count = (
            len(cache["results"])
            if cache
            and cache.get("results")
            else 0
        )

        with freeze_lock:
            freeze_count = len(
                freezes
            )

        print()
        print(
            f"Current addresses: "
            f"{count:,}"
        )

        print(
            f"Active freezes: "
            f"{freeze_count}"
        )

        print()

        print('[f] Find number')
        print('[n] Next')
        print('[e] Edit')
        print('[a] Address table')
        print('[v] View freezes')
        print('[T] Toggle patches')
        print('[c] Clear')
        print('[s] Save')
        print('[x] Quit')

        choice = input(
            "> "
        ).strip().lower()

        if choice == "f":
            find_value(pine)

        elif choice == "n":
            next_scan(pine)

        elif choice == "e":
            edit(pine)

        elif choice == "a":
            address_table(pine)

        elif choice == "v":
            view_freezes(pine)

        elif choice == "t":
            toggle_patches(pine)

        elif choice == "c":
            clear_current()

        elif choice == "s":
            save_current()

        elif choice == "x":

            print(
                "Stopping freezes..."
            )

            stop_all_freezes()
            break

        else:
            print(
                "Invalid option."
            )

    pine.close()


if __name__ == "__main__":
    main()