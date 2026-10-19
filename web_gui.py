import json
import math
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import search

HOST = "127.0.0.1"
PORT = 8765

pine = search.Pine()
identity = None
data = None
PINE_LOCK = threading.RLock()
SCANNER = {"active": False, "results": [], "type": "dword", "target": None, "tolerance": 0.0, "range": "default", "has_scan": False}
SCANNER_TABLE = []


def build_identity():
    game = search.get_game_title(pine)
    serial = search.get_pine_identity_value(
        pine, ("serial", "get_serial", "game_serial")
    )
    crc = search.get_pine_identity_value(
        pine, ("crc", "get_crc", "game_crc")
    )
    folder = search.TOGGLE_ROOT / search.sanitize_name(game)
    identity_file = folder / ".identity.json"
    saved = search.load_json(identity_file, {})

    if isinstance(saved, dict):
        serial = serial or str(saved.get("serial", "")).strip()
        crc = crc or str(saved.get("crc", "")).strip()

    return {
        "game": game,
        "serial": serial,
        "crc": crc,
        "folder": folder,
        "identity_file": identity_file,
        "file": None,
    }


def rebuild_file_path():
    identity["file"] = (
        identity["folder"]
        / f"{search.sanitize_name(identity['serial'])}_"
          f"{search.sanitize_name(identity['crc'])}.json"
    )


def load_backend():
    global identity, data

    identity = build_identity()

    if not identity["serial"] or not identity["crc"]:
        data = None
        return

    rebuild_file_path()
    data = search.load_toggle_file(pine, identity)

    if data is None:
        data = {
            "game": identity["game"],
            "serial": identity["serial"],
            "crc": identity["crc"],
            "categories": ["Uncategorized"],
            "patches": [],
        }
        save_data()
    else:
        ensure_categories()
        search.auto_restore_or_activate(pine, data)
        save_data()


def patch_addresses(item):
    if isinstance(item.get("addresses"), list):
        return item["addresses"]
    return [{
        "address": item["address"],
        "type": item.get("type", "dword"),
        "value": item.get("value", 0),
        "original_value": item.get("original_value"),
    }]


def get_state():
    patches = data.get("patches", []) if data else []
    output = []

    for item in patches:
        addresses = patch_addresses(item)
        output.append({
            "name": search.patch_label(item),
            "category": patch_category(item),
            "enabled": bool(item.get("enabled")),
            "addresses": [
                {
                    "address": int(a["address"]),
                    "type": a.get("type", "dword"),
                    "value": a.get("value", 0),
                    "original_value": a.get("original_value"),
                }
                for a in addresses
            ],
        })

    return {
        "game": identity["game"],
        "serial": identity["serial"],
        "crc": identity["crc"],
        "identity_ready": bool(identity["serial"] and identity["crc"]),
        "file": str(identity["file"]) if identity["file"] else None,
        "categories": list(data.get("categories", ["Uncategorized"]) if data else ["Uncategorized"]),
        "patches": output,
    }


def parse_value(value, typ):
    if typ == "float":
        return float(value)
    return int(str(value), 0)


VALID_TYPES = {"byte", "word", "dword", "qword", "float"}


def parse_addresses(raw_addresses):
    if not isinstance(raw_addresses, list) or not raw_addresses:
        raise ValueError("At least one address is required.")

    result = []

    for raw in raw_addresses:
        if not isinstance(raw, dict):
            raise ValueError("Invalid address row.")

        address_text = str(raw.get("address", "")).strip()
        if not address_text:
            raise ValueError("Every address is required.")

        address = int(address_text, 0)
        typ = str(raw.get("type", "dword")).lower()

        if typ not in VALID_TYPES:
            raise ValueError("Invalid type.")

        value = parse_value(raw.get("value", ""), typ)

        result.append({
            "address": address,
            "type": typ,
            "value": value,
        })

    return result


def make_patch(name, addresses, originals=None, category="Uncategorized"):
    name = str(name).strip()
    if not name:
        name = f"0x{int(addresses[0]['address']):08X}"

    if len(addresses) == 1:
        a = addresses[0]
        item = {
            "name": name,
            "category": category,
            "address": a["address"],
            "type": a["type"],
            "value": a["value"],
            "enabled": False,
        }
        if originals is not None:
            item["original_value"] = originals[0]
        else:
            item["original_value"] = search.patch_read(pine, item)
        return item

    children = []

    for i, a in enumerate(addresses):
        child = {
            "address": a["address"],
            "type": a["type"],
            "value": a["value"],
        }

        if originals is not None:
            child["original_value"] = originals[i]
        else:
            child["original_value"] = search.patch_read(pine, child)

        children.append(child)

    return {
        "name": name,
        "category": category,
        "enabled": False,
        "addresses": children,
    }


def set_patch(index, enabled):
    item = data["patches"][index]

    if enabled:
        ok = search.toggle_patch_on(pine, item)
    else:
        ok = search.toggle_patch_off(pine, item)

    if not ok:
        raise RuntimeError("Patch write failed.")

    search.save_toggle_file(identity, data)


def restore_all():
    failed = 0

    for item in data.get("patches", []):
        if item.get("enabled"):
            try:
                if not search.toggle_patch_off(pine, item):
                    failed += 1
            except Exception:
                failed += 1

    search.save_toggle_file(identity, data)
    return failed


def add_patch(payload):
    ensure_categories()
    category = str(payload.get("category", "Uncategorized")).strip() or "Uncategorized"
    if category not in data["categories"]:
        raise ValueError("Category not found.")

    addresses = parse_addresses(payload.get("addresses", []))
    item = make_patch(payload.get("name", ""), addresses, category=category)
    data["patches"].append(item)
    save_data()


def edit_patch(index, payload):
    ensure_categories()
    item = data["patches"][index]
    was_enabled = bool(item.get("enabled"))

    if was_enabled:
        if not search.toggle_patch_off(pine, item):
            raise RuntimeError("Could not turn patch OFF before editing.")

    category = str(payload.get("category", patch_category(item))).strip() or "Uncategorized"
    if category not in data["categories"]:
        raise ValueError("Category not found.")

    addresses = parse_addresses(payload.get("addresses", []))
    originals = []

    for a in addresses:
        old_original = None

        if isinstance(item.get("addresses"), list):
            for old in item["addresses"]:
                if (
                    int(old.get("address", -1)) == a["address"]
                    and old.get("type", "dword") == a["type"]
                ):
                    old_original = old.get("original_value")
                    break
        else:
            if (
                int(item.get("address", -1)) == a["address"]
                and item.get("type", "dword") == a["type"]
            ):
                old_original = item.get("original_value")

        if old_original is None:
            probe = {
                "address": a["address"],
                "type": a["type"],
                "value": a["value"],
            }
            old_original = search.patch_read(pine, probe)

        originals.append(old_original)

    new_item = make_patch(
        payload.get("name", ""),
        addresses,
        originals,
        category,
    )

    data["patches"][index] = new_item

    if was_enabled:
        if not search.toggle_patch_on(pine, new_item):
            new_item["enabled"] = False
            search.save_toggle_file(identity, data)
            raise RuntimeError("Patch was updated but could not be turned back ON.")

    save_data()


def delete_patch(index):
    item = data["patches"][index]

    if item.get("enabled"):
        if not search.toggle_patch_off(pine, item):
            raise RuntimeError("Could not restore patch before deletion.")

    del data["patches"][index]
    save_data()


def ensure_categories():
    if not isinstance(data, dict):
        return

    categories = data.get("categories")

    if not isinstance(categories, list):
        categories = []

    cleaned = []

    for category in categories:
        name = str(category).strip()
        if name and name not in cleaned:
            cleaned.append(name)

    for item in data.get("patches", []):
        category = str(item.get("category", "")).strip()
        if category and category not in cleaned:
            cleaned.append(category)

    if not cleaned:
        cleaned = ["Uncategorized"]

    if "Uncategorized" not in cleaned:
        cleaned.append("Uncategorized")

    data["categories"] = cleaned


def patch_category(item):
    category = str(item.get("category", "")).strip()
    return category or "Uncategorized"


def save_data():
    ensure_categories()
    search.save_toggle_file(identity, data)


def add_category(name):
    name = str(name).strip()
    if not name:
        raise ValueError("Category name is required.")
    if len(name) > 80:
        raise ValueError("Category name is too long.")

    ensure_categories()

    if name in data["categories"]:
        raise ValueError("Category already exists.")

    data["categories"].append(name)
    save_data()


def rename_category(old_name, new_name):
    old_name = str(old_name).strip()
    new_name = str(new_name).strip()

    if not old_name or not new_name:
        raise ValueError("Category name is required.")

    if new_name == "Uncategorized":
        raise ValueError("Uncategorized is reserved.")

    ensure_categories()

    if old_name not in data["categories"]:
        raise ValueError("Category not found.")

    if new_name in data["categories"] and new_name != old_name:
        raise ValueError("Category already exists.")

    data["categories"] = [
        new_name if category == old_name else category
        for category in data["categories"]
    ]

    for item in data.get("patches", []):
        if patch_category(item) == old_name:
            item["category"] = new_name

    save_data()


def delete_category(name):
    name = str(name).strip()

    if not name or name == "Uncategorized":
        raise ValueError("This category cannot be deleted.")

    ensure_categories()

    if name not in data["categories"]:
        raise ValueError("Category not found.")

    for item in data.get("patches", []):
        if patch_category(item) == name:
            item["category"] = "Uncategorized"

    data["categories"] = [
        category
        for category in data["categories"]
        if category != name
    ]

    save_data()


def save_identity(serial, crc):
    global data

    serial = str(serial).strip()
    crc = str(crc).strip()

    if not serial or not crc:
        raise ValueError("Serial and CRC are required.")

    identity["serial"] = serial
    identity["crc"] = crc
    rebuild_file_path()

    identity["folder"].mkdir(parents=True, exist_ok=True)

    search.save_json(
        identity["identity_file"],
        {
            "game": identity["game"],
            "serial": serial,
            "crc": crc,
        },
    )

    data = search.load_toggle_file(pine, identity)

    if data is None:
        data = {
            "game": identity["game"],
            "serial": serial,
            "crc": crc,
            "categories": ["Uncategorized"],
            "patches": [],
        }
        save_data()
    else:
        ensure_categories()
        search.auto_restore_or_activate(pine, data)
        save_data()



# ---------------------------------------------------------------------------
# Memory scanner
# ---------------------------------------------------------------------------

SCAN_TYPES = {
    "byte": 1,
    "word": 2,
    "dword": 4,
    "qword": 8,
    "float": 4,
    "double": 8,
}

SCAN_FORMATS = {
    "byte": "<B",
    "word": "<H",
    "dword": "<I",
    "qword": "<Q",
    "float": "<f",
    "double": "<d",
}

SCAN_CHUNK = 0x40000
MAX_WEB_RESULTS = 200000


def scanner_ranges(name):
    if name == "extended":
        return [("Extended 0x20 region", search.EXTENDED_START, search.EXTENDED_END)]
    if name == "all":
        return [
            ("Default RAM", search.DEFAULT_START, search.DEFAULT_END),
            ("Extended 0x20 region", search.EXTENDED_START, search.EXTENDED_END),
        ]
    return [("Default RAM", search.DEFAULT_START, search.DEFAULT_END)]


def scanner_parse_target(text, typ):
    if typ in ("float", "double"):
        value = float(text)
        if not math.isfinite(value):
            raise ValueError("Floating-point target must be finite.")
        return value

    value = int(str(text).strip(), 0)
    limits = {
        "byte": (0, 0xFF),
        "word": (0, 0xFFFF),
        "dword": (0, 0xFFFFFFFF),
        "qword": (0, 0xFFFFFFFFFFFFFFFF),
    }
    lo, hi = limits[typ]
    if not lo <= value <= hi:
        raise ValueError(f"{typ} value must be {lo}..{hi}.")
    return value


def scanner_unpack(data, offset, typ):
    return struct.unpack_from(SCAN_FORMATS[typ], data, offset)[0]


def scanner_equal(a, b, typ, tolerance):
    if typ in ("float", "double"):
        return abs(float(a) - float(b)) <= tolerance
    return a == b


def scanner_read_one(addr, typ):
    size = SCAN_TYPES[typ]

    if typ == "byte":
        return pine.read8(addr)
    if typ == "word":
        return pine.read16(addr)
    if typ == "dword":
        return pine.read32(addr)

    raw = pine.read_block(addr, (size + 3) // 4)
    return struct.unpack(SCAN_FORMATS[typ], raw[:size])[0]


def scanner_first_scan(target_text, typ, tolerance, range_name):
    scan_types = tuple(SCAN_TYPES.keys()) if typ == "all" else (typ,)
    if typ != "all" and typ not in SCAN_TYPES:
        raise ValueError("Invalid scan type.")
    results = []
    with PINE_LOCK:
        for scan_typ in scan_types:
            target = scanner_parse_target(target_text, scan_typ)
            step = SCAN_TYPES[scan_typ]
            for _, start, end in scanner_ranges(range_name):
                addr = start
                while addr < end and len(results) < MAX_WEB_RESULTS:
                    length = min(SCAN_CHUNK, end - addr)
                    length -= length % 4
                    if length <= 0:
                        break
                    data_block = pine.read_block(addr, length // 4)
                    usable = len(data_block) - len(data_block) % step
                    for off in range(0, usable, step):
                        try:
                            value = scanner_unpack(data_block, off, scan_typ)
                        except struct.error:
                            break
                        if scan_typ in ("float", "double") and not math.isfinite(value):
                            continue
                        if scanner_equal(value, target, scan_typ, tolerance if scan_typ in ("float", "double") else 0.0):
                            results.append({"address": addr + off, "type": scan_typ, "value": value, "previous": None})
                            if len(results) >= MAX_WEB_RESULTS:
                                break
                    addr += length
    SCANNER["results"] = results
    SCANNER["type"] = typ
    SCANNER["target"] = target_text if typ == "all" else scanner_parse_target(target_text, typ)
    SCANNER["tolerance"] = tolerance
    SCANNER["range"] = range_name
    SCANNER["has_scan"] = True
    return results


def scanner_next_scan(mode, target_text=None, tolerance=0.0):
    if not SCANNER["has_scan"]:
        raise ValueError("No active scan. Run First Scan first.")
    scan_typ = SCANNER["type"]
    if scan_typ != "all" and scan_typ not in SCAN_TYPES:
        raise ValueError("Invalid cached scan type.")
    exact_text = str(target_text or "").strip()
    new_results = []
    with PINE_LOCK:
        for item in list(SCANNER["results"]):
            item_typ = str(item.get("type", scan_typ)).lower()
            if item_typ not in SCAN_TYPES:
                continue
            try:
                current = scanner_read_one(int(item["address"]), item_typ)
            except Exception:
                continue
            previous = item["value"]
            item_tolerance = tolerance if item_typ in ("float", "double") else 0.0
            if mode == "exact":
                target = scanner_parse_target(exact_text, item_typ)
                keep = scanner_equal(current, target, item_typ, item_tolerance)
            elif mode == "increased":
                keep = current > previous + item_tolerance if item_typ in ("float", "double") else current > previous
            elif mode == "decreased":
                keep = current < previous - item_tolerance if item_typ in ("float", "double") else current < previous
            elif mode == "changed":
                keep = not scanner_equal(current, previous, item_typ, item_tolerance)
            elif mode == "unchanged":
                keep = scanner_equal(current, previous, item_typ, item_tolerance)
            else:
                raise ValueError("Invalid next-scan mode.")
            if keep:
                new_results.append({"address": int(item["address"]), "type": item_typ, "value": current, "previous": previous})
    SCANNER["results"] = new_results
    if mode == "exact":
        SCANNER["target"] = target_text if scan_typ == "all" else scanner_parse_target(exact_text, scan_typ)
        SCANNER["tolerance"] = tolerance
    return new_results


def scanner_add_results(items=None):
    items = SCANNER["results"] if items is None else items
    added = 0

    for item in items:
        key = (int(item["address"]), str(item["type"]))

        if any(
            (int(x["address"]), str(x["type"])) == key
            for x in SCANNER_TABLE
        ):
            continue

        SCANNER_TABLE.append({
            "address": int(item["address"]),
            "type": str(item["type"]),
            "value": item.get("value", 0),
            "frozen": False,
        })
        added += 1

    return added


def scanner_table_read():
    with PINE_LOCK:
        for item in SCANNER_TABLE:
            try:
                item["value"] = scanner_read_one(
                    item["address"],
                    item["type"],
                )
            except Exception:
                pass

    return list(SCANNER_TABLE)


def scanner_write_item(index, value):
    if index < 0 or index >= len(SCANNER_TABLE):
        raise ValueError("Invalid address-table entry.")

    item = SCANNER_TABLE[index]
    typ = item["type"]
    addr = item["address"]

    if typ == "byte":
        value = int(value, 0)
        if not 0 <= value <= 0xFF:
            raise ValueError("Byte value must be 0..255.")
        pine.write8(addr, value)

    elif typ == "word":
        value = int(value, 0)
        if not 0 <= value <= 0xFFFF:
            raise ValueError("Word value must be 0..65535.")
        pine.write16(addr, value)

    elif typ == "dword":
        value = int(value, 0)
        if not 0 <= value <= 0xFFFFFFFF:
            raise ValueError("Dword value must be 0..4294967295.")
        pine.write32(addr, value)

    elif typ == "qword":
        value = int(value, 0)
        if not 0 <= value <= 0xFFFFFFFFFFFFFFFF:
            raise ValueError(
                "Qword value must be 0..18446744073709551615."
            )
        pine.write32(addr, value & 0xFFFFFFFF)
        pine.write32(addr + 4, (value >> 32) & 0xFFFFFFFF)

    elif typ == "float":
        value = float(value)
        if not math.isfinite(value):
            raise ValueError("Float value must be finite.")
        raw = struct.unpack("<I", struct.pack("<f", value))[0]
        pine.write32(addr, raw)

    elif typ == "double":
        value = float(value)
        if not math.isfinite(value):
            raise ValueError("Double value must be finite.")
        raw = struct.pack("<d", value)
        lo, hi = struct.unpack("<II", raw)
        pine.write32(addr, lo)
        pine.write32(addr + 4, hi)

    else:
        raise ValueError("Unknown value type.")

    with PINE_LOCK:
        item["value"] = scanner_read_one(addr, typ)

    return item["value"]


def scanner_set_frozen(index, enabled):
    if index < 0 or index >= len(SCANNER_TABLE):
        raise ValueError("Invalid address-table entry.")

    SCANNER_TABLE[index]["frozen"] = bool(enabled)


def scanner_freeze_cycle():
    with PINE_LOCK:
        for index, item in enumerate(SCANNER_TABLE):
            if not item.get("frozen"):
                continue

            try:
                scanner_write_item(index, str(item["value"]))
            except Exception:
                pass



HTML = '<!doctype html>\n<html>\n<head>\n<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">\n<title>PINE Memory</title>\n<style>\n:root{color-scheme:dark;--bg:#101114;--card:#1b1c20;--card2:#222329;--text:#f5f5f5;--muted:#9fa0a6;--accent:#8ab4f8;--good:#69d38b;--bad:#ff6b6b;--line:#303137}\n*{box-sizing:border-box}\nhtml{scroll-behavior:smooth}\nbody{margin:0;background:var(--bg);color:var(--text);font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;-webkit-tap-highlight-color:transparent}\nmain{width:100%;max-width:760px;margin:auto;padding:12px 12px 90px}\nh1{font-size:25px;margin:4px 2px 2px}.subtitle{color:var(--muted);font-size:14px;margin:0 2px 16px}.meta{color:var(--muted);font-size:12px;margin:0 2px 14px}\n.section{font-size:14px;font-weight:700;margin:18px 2px 8px}.card{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:14px;margin:8px 0}\nbutton{min-height:48px;border:0;border-radius:14px;background:var(--card2);color:var(--text);font-size:15px;font-weight:600;padding:10px 14px}button.primary{background:var(--accent);color:#101114}button.danger{color:#ff9b9b}button:active{transform:scale(.98)}\ninput,select{width:100%;min-height:48px;border:1px solid var(--line);border-radius:13px;background:#15161a;color:var(--text);padding:11px 13px;font-size:16px;outline:none}\nlabel{display:block;color:var(--muted);font-size:12px;margin:12px 2px 6px}.actions{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px}\n.status{position:sticky;bottom:10px;margin-top:14px;padding:10px 13px;border-radius:13px;background:#202126;color:var(--muted);font-size:13px;min-height:40px}.status.good{color:var(--good)}.status.bad{color:var(--bad)}.hidden{display:none}.small{font-size:12px;color:var(--muted)}\n.category{margin:12px 0 16px}.category-head{display:flex;align-items:center;gap:8px;padding:4px 2px 7px}.category-name{font-size:15px;font-weight:750;flex:1;min-width:0;overflow-wrap:anywhere}.category-count{font-size:12px;color:var(--muted)}\n.category-toggle,.category-action{min-height:40px;width:40px;padding:0;border-radius:12px;background:transparent;color:var(--muted)}\n.category-body{padding:0}.patch-head{display:flex;align-items:center;gap:10px;min-height:54px}.patch-main{min-width:0;flex:1}.name{font-size:16px;font-weight:700;overflow-wrap:anywhere}.detail{font-size:12px;color:var(--muted);margin-top:5px;overflow-wrap:anywhere}\n.chevron{width:38px;height:38px;min-height:38px;padding:0;border-radius:12px;background:transparent;font-size:18px;color:var(--muted)}\n.switch{position:relative;width:52px;height:32px;flex:0 0 auto;display:block}.switch input{display:none}.slider{position:absolute;inset:0;border-radius:18px;background:#3a3b41;transition:.16s}.slider:before{content:"";position:absolute;width:26px;height:26px;left:3px;top:3px;border-radius:50%;background:#eee;transition:.16s}.switch input:checked+.slider{background:var(--good)}.switch input:checked+.slider:before{transform:translateX(20px)}\n.patch-body{border-top:1px solid var(--line);margin-top:10px;padding-top:10px}.addr-row{display:grid;grid-template-columns:minmax(0,1.7fr) .9fr minmax(0,1.2fr);gap:8px;align-items:center;margin:7px 0}.addr-line{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px;overflow-wrap:anywhere}.addr-value{color:var(--muted);font-size:12px}.patch-actions{display:flex;gap:10px;justify-content:flex-end;margin-top:12px}\n.form-row{display:grid;grid-template-columns:minmax(0,1.7fr) .9fr minmax(0,1.2fr) 48px;gap:8px;align-items:end;margin:8px 0}.form-row button{min-height:48px;padding:0}.form-title{font-size:14px;font-weight:700;margin-bottom:8px}\n.scanner-controls{display:grid;grid-template-columns:1fr 1fr;gap:10px}.scanner-controls .wide{grid-column:1/-1}\n.scan-meta{display:flex;justify-content:space-between;gap:8px;color:var(--muted);font-size:12px;margin:10px 2px}\n.result{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px;align-items:center;border-top:1px solid var(--line);padding:10px 0}.result:first-child{border-top:0}.result-main{min-width:0}.result-addr{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px}.result-value{font-size:15px;margin-top:3px}.result-prev{font-size:12px;color:var(--muted)}.result-actions{display:flex;gap:6px}.result-actions button{min-height:40px;padding:7px 10px;font-size:13px}\n.table-row{display:grid;grid-template-columns:minmax(0,1fr) 90px 92px;gap:8px;align-items:center;border-top:1px solid var(--line);padding:10px 0}.table-row:first-child{border-top:0}.table-value{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}.table-actions{display:flex;gap:6px}.table-actions button{min-height:40px;padding:7px 9px;font-size:12px}.table-type{color:var(--muted);font-size:12px}\n.hamburger{position:fixed;right:16px;top:14px;z-index:1001;width:52px;height:52px;min-height:52px;border:1px solid #3b3d44;border-radius:14px;background:rgba(27,28,32,.96);font-size:27px;line-height:1}\n.drawer-backdrop{position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:1000}.drawer{position:fixed;top:0;right:0;bottom:0;width:min(310px,88vw);z-index:1002;background:#15161a;border-left:1px solid var(--line);padding:80px 16px 20px;box-shadow:-12px 0 30px rgba(0,0,0,.3)}.drawer h2{margin:0 0 14px;font-size:18px}.drawer button{width:100%;text-align:left;margin:5px 0}.drawer .active{background:#2b2d34}\n.home-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.home-card{padding:18px;text-align:left;cursor:pointer}.home-card strong{display:block;font-size:17px}.home-card span{display:block;color:var(--muted);font-size:12px;margin-top:5px}\n.empty{text-align:center;color:var(--muted);padding:24px 10px}\n@media(max-width:520px){.actions{grid-template-columns:1fr}.addr-row{grid-template-columns:1fr 1fr}.addr-row .addr-line{grid-column:1/-1}.form-row{grid-template-columns:1fr 1fr}.form-row input:first-child{grid-column:1/-1}.form-row button{grid-column:1/-1}.patch-actions{justify-content:stretch}.patch-actions button{flex:1}.scanner-controls{grid-template-columns:1fr}.scanner-controls .wide{grid-column:auto}.home-grid{grid-template-columns:1fr}.table-row{grid-template-columns:minmax(0,1fr) 70px 92px}}\n</style>\n</head>\n<body>\n<button class="hamburger" onclick="toggleDrawer()" aria-label="Open menu">☰</button>\n<div id="drawerLayer" class="hidden">\n<div class="drawer-backdrop" onclick="toggleDrawer()"></div>\n<nav class="drawer">\n<h2>PINE Memory</h2>\n<button id="navHome" onclick="go(\'/home\')">⌂ Home</button>\n<button id="navScan" onclick="go(\'/scan\')">⌕ Memory Scanner</button>\n<button id="navToggle" onclick="go(\'/toggle\')">☑ Toggle Patches</button>\n</nav>\n</div>\n\n<main>\n<h1 id="pageTitle">PINE Memory</h1>\n<div id="game" class="subtitle">Connecting...</div>\n<div id="meta" class="meta"></div>\n\n<section id="homePage">\n<div class="section">Home</div>\n<div class="card">\n<div class="form-title">ARMSX2 / PINE</div>\n<div class="small">Choose a tool. The menu button stays available while you scroll.</div>\n</div>\n<div class="home-grid">\n<button class="card home-card" onclick="go(\'/scan\')"><strong>⌕ Memory Scanner</strong><span>Find values, narrow results, add addresses.</span></button>\n<button class="card home-card" onclick="go(\'/toggle\')"><strong>☑ Toggle Patches</strong><span>Manage and switch your patch groups.</span></button>\n</div>\n</section>\n\n<section id="scanPage" class="hidden">\n<div class="section">Memory Scanner</div>\n<div class="card">\n<div class="scanner-controls">\n<div><label>Type</label><select id="scanType"><option value="all" selected>All / Unknown Type</option><option value="byte">byte</option><option value="word">word</option><option value="dword">dword</option><option value="qword">qword</option><option value="float">float</option><option value="double">double</option></select></div>\n<div><label>Range</label><select id="scanRange"><option value="default">Default RAM</option><option value="extended">Extended 0x20 region</option><option value="all">All configured ranges</option></select></div>\n<div class="wide"><label>Exact value</label><input id="scanValue" placeholder="100"></div>\n<div class="wide"><label>Tolerance (All / float / double)</label><input id="scanTolerance" value="0" inputmode="decimal"></div>\n</div>\n<div class="actions"><button class="primary" onclick="firstScan()">First Scan</button><button onclick="clearScan()">Clear</button></div>\n<div id="scanStatus" class="small" style="margin-top:10px">No scan results.</div>\n</div>\n\n<div id="nextCard" class="card hidden">\n<div class="form-title">Next Scan</div>\n<div class="scanner-controls">\n<div class="wide"><label>Comparison</label><select id="nextMode"><option value="exact">Exact value</option><option value="increased">Increased</option><option value="decreased">Decreased</option><option value="changed">Changed</option><option value="unchanged">Unchanged</option></select></div>\n</div>\n<div class="actions"><button class="primary" onclick="nextScan()">Next Scan</button><button onclick="addAllResults()">Add All</button></div>\n</div>\n\n<div class="section">Scan Results</div>\n<div class="card">\n<div id="resultsMeta" class="scan-meta"><span>0 results</span><span></span></div>\n<div id="results"><div class="empty">No scan results.</div></div>\n</div>\n\n<div class="section">Address Table</div>\n<div class="card">\n<div class="actions" style="margin-top:0"><button onclick="refreshTable()">Refresh Values</button><button class="danger" onclick="clearTable()">Clear Table</button></div>\n<div id="addressTable"><div class="empty">No addresses added.</div></div>\n</div>\n</section>\n\n<section id="togglePage" class="hidden">\n<section id="identityCard" class="card hidden">\n<div class="section" style="margin:0 0 4px">Game Identity</div>\n<div class="small">Enter these once for this game.</div>\n<label>Serial</label><input id="serial" placeholder="SLUS-20946">\n<label>CRC</label><input id="crc" placeholder="399A49CA">\n<div class="actions"><button class="primary" onclick="saveIdentity()">Save Identity</button></div>\n</section>\n\n<div id="patchSection" class="hidden">\n<div class="section">Toggle Patches</div>\n<div id="categories"></div>\n<div class="actions">\n<button class="primary" onclick="showAdd()">Add Patch</button>\n<button onclick="showCategoryEditor()">Add Category</button>\n</div>\n<div class="actions"><button onclick="restoreAll()">Restore All</button></div>\n\n<div id="editorCard" class="card hidden">\n<div id="editorTitle" class="form-title">Add Patch</div>\n<label>Name</label><input id="patchName" placeholder="Infinite Ammo - All Weapons">\n<label>Category</label><select id="patchCategory"></select>\n<label>Addresses</label>\n<div id="addressRows"></div>\n<button onclick="addAddressRow()">+ Add Address</button>\n<div class="actions"><button class="primary" onclick="savePatch()">Save Patch</button><button onclick="hideEditor()">Cancel</button></div>\n</div>\n\n<div id="categoryCard" class="card hidden">\n<div id="categoryTitle" class="form-title">Add Category</div>\n<label>Name</label><input id="categoryName" placeholder="Gameplay">\n<div class="actions"><button class="primary" onclick="saveCategory()">Save Category</button><button onclick="hideCategoryEditor()">Cancel</button></div>\n<div id="categoryDeleteRow" class="actions hidden"><button class="danger" onclick="deleteEditingCategory()">Delete Category</button></div>\n</div>\n</div>\n</section>\n\n<div id="status" class="status">Connecting to PINE...</div>\n</main>\n\n<script>\nlet busy=false, editingIndex=null, categoryEditing=null, expanded=new Set(), collapsedCategories=new Set(), currentState=null, drawerOpen=false;\n\nfunction esc(s){return String(s).replace(/[&<>"\']/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;",\'"\':"&quot;","\'":"&#39;"}[m]))}\nfunction setStatus(text,good=true){const e=document.getElementById("status");e.textContent=text;e.className="status "+(good?"good":"bad")}\nasync function api(path,options={}){const r=await fetch(path,{cache:"no-store",headers:{"Content-Type":"application/json"},...options});const x=await r.json();if(!r.ok||!x.ok)throw new Error(x.error||"Request failed");return x}\nfunction fmtAddr(v){return "0x"+Number(v).toString(16).toUpperCase().padStart(8,"0")}\nfunction patchAddresses(p){if(Array.isArray(p.addresses))return p.addresses;return [{address:p.address,type:p.type||"dword",value:p.value??0,original_value:p.original_value}]}\nfunction categoriesOf(state){return Array.isArray(state.categories)&&state.categories.length?state.categories:["Uncategorized"]}\n\nfunction toggleDrawer(force){drawerOpen=force===undefined?!drawerOpen:force;document.getElementById("drawerLayer").classList.toggle("hidden",!drawerOpen)}\nfunction go(path){history.pushState({},\'\',path);toggleDrawer(false);route();window.scrollTo(0,0)}\nwindow.addEventListener("popstate",route)\n\nfunction route(){\n const path=location.pathname;\n const page=path==="/scan"?"scan":path==="/toggle"?"toggle":"home";\n document.getElementById("homePage").classList.toggle("hidden",page!=="home");\n document.getElementById("scanPage").classList.toggle("hidden",page!=="scan");\n document.getElementById("togglePage").classList.toggle("hidden",page!=="toggle");\n document.getElementById("pageTitle").textContent=page==="scan"?"Memory Scanner":page==="toggle"?"Toggle Patches":"PINE Memory";\n ["navHome","navScan","navToggle"].forEach(id=>document.getElementById(id).classList.remove("active"));\n document.getElementById(page==="home"?"navHome":page==="scan"?"navScan":"navToggle").classList.add("active");\n}\n\nfunction renderCategorySelect(selected){\n const sel=document.getElementById("patchCategory"),cats=categoriesOf(currentState||{});\n sel.innerHTML=cats.map(c=>\'<option value="\'+esc(c)+\'">\'+esc(c)+\'</option>\').join("");\n sel.value=cats.includes(selected)?selected:"Uncategorized";\n}\n\nfunction render(state){\n currentState=state;\n document.getElementById("game").textContent=state.game||"Unknown Game";\n document.getElementById("meta").textContent=state.identity_ready?((state.serial||"")+"  •  CRC "+(state.crc||"")):"Game identity is required";\n document.getElementById("identityCard").classList.toggle("hidden",state.identity_ready);\n document.getElementById("patchSection").classList.toggle("hidden",!state.identity_ready);\n if(!state.identity_ready){document.getElementById("serial").value=state.serial||"";document.getElementById("crc").value=state.crc||"";return}\n const root=document.getElementById("categories");root.innerHTML="";\n const cats=categoriesOf(state),patches=state.patches||[];\n cats.forEach(category=>{\n  const items=patches.map((p,i)=>({p,i})).filter(x=>(x.p.category||"Uncategorized")===category);\n  const section=document.createElement("div");section.className="category";\n  const closed=collapsedCategories.has(category);\n  const head=document.createElement("div");head.className="category-head";\n  const toggle=document.createElement("button");toggle.className="category-toggle";toggle.type="button";toggle.textContent=closed?"▶":"▼";toggle.addEventListener("click",()=>toggleCategory(category));head.appendChild(toggle);\n  const name=document.createElement("div");name.className="category-name";name.textContent=category;head.appendChild(name);\n  const count=document.createElement("div");count.className="category-count";count.textContent=items.length+" "+(items.length===1?"patch":"patches");head.appendChild(count);\n  if(category!=="Uncategorized"){\n   const action=document.createElement("button");action.className="category-action";action.type="button";action.textContent="⋮";action.setAttribute("aria-label","Edit category "+category);action.addEventListener("click",()=>editCategory(category));head.appendChild(action);\n  }\n  section.appendChild(head);\n  const body=document.createElement("div");body.className="category-body";if(closed)body.classList.add("hidden");\n  if(!items.length)body.innerHTML=\'<div class="small" style="padding:4px 8px 10px">No patches in this category.</div>\';\n  items.forEach(({p,i})=>{\n   const card=document.createElement("div");card.className="card";const addresses=patchAddresses(p),open=expanded.has(i);\n   const patchHead=document.createElement("div");patchHead.className="patch-head";\n   const main=document.createElement("div");main.className="patch-main";\n   const title=document.createElement("div");title.className="name";title.textContent=p.name||"Unnamed Patch";\n   const detail=document.createElement("div");detail.className="detail";detail.textContent=(p.enabled?"ON":"OFF")+" · "+addresses.length+" "+(addresses.length===1?"address":"addresses");\n   main.appendChild(title);main.appendChild(detail);patchHead.appendChild(main);\n   const expand=document.createElement("button");expand.className="chevron";expand.type="button";expand.textContent=open?"▲":"▼";expand.addEventListener("click",()=>toggleExpand(i));patchHead.appendChild(expand);\n   const label=document.createElement("label");label.className="switch";label.style.margin="0";\n   const input=document.createElement("input");input.type="checkbox";input.checked=!!p.enabled;input.addEventListener("change",()=>togglePatch(i,input.checked));\n   const slider=document.createElement("span");slider.className="slider";label.appendChild(input);label.appendChild(slider);patchHead.appendChild(label);card.appendChild(patchHead);\n   if(open){\n    const bodyPart=document.createElement("div");bodyPart.className="patch-body";\n    bodyPart.innerHTML=addresses.map(a=>\'<div class="addr-row"><div class="addr-line">\'+fmtAddr(a.address)+\'</div><div>\'+esc(a.type||"dword")+\'</div><div class="addr-value">target \'+esc(a.value)+\'<br>original \'+esc(a.original_value??"—")+\'</div></div>\').join("");\n    const actions=document.createElement("div");actions.className="patch-actions";\n    const edit=document.createElement("button");edit.type="button";edit.textContent="Edit";edit.addEventListener("click",()=>editPatch(i));\n    const del=document.createElement("button");del.type="button";del.className="danger";del.textContent="Delete";del.addEventListener("click",()=>deletePatch(i));\n    actions.appendChild(edit);actions.appendChild(del);bodyPart.appendChild(actions);card.appendChild(bodyPart);\n   }\n   body.appendChild(card);\n  });\n  section.appendChild(body);root.appendChild(section);\n });\n}\n\nasync function refresh(silent=false){if(busy)return;try{const r=await api("/api/state");render(r.state);if(!silent)setStatus("Connected")}catch(e){setStatus(e.message,false)}}\n\nasync function firstScan(){\n if(busy)return;busy=true;setScanStatus("Scanning memory...");\n try{\n  const r=await api("/api/scan/first",{method:"POST",body:JSON.stringify({type:document.getElementById("scanType").value,range:document.getElementById("scanRange").value,value:document.getElementById("scanValue").value,tolerance:document.getElementById("scanTolerance").value})});\n  renderScan(r);setScanStatus("First scan complete.");\n }catch(e){setScanStatus(e.message,false)}finally{busy=false}\n}\nasync function nextScan(){\n if(busy)return;busy=true;setScanStatus("Checking current results...");\n try{\n  const mode=document.getElementById("nextMode").value;\n  const r=await api("/api/scan/next",{method:"POST",body:JSON.stringify({mode,target:document.getElementById("scanValue").value,tolerance:document.getElementById("scanTolerance").value})});\n  renderScan(r);setScanStatus("Next scan complete.");\n }catch(e){setScanStatus(e.message,false)}finally{busy=false}\n}\nasync function clearScan(){try{const r=await api("/api/scan/clear",{method:"POST",body:"{}"});renderScan(r);setScanStatus("Scan cleared.")}catch(e){setScanStatus(e.message,false)}}\nfunction setScanStatus(t,good=true){const e=document.getElementById("scanStatus");e.textContent=t;e.style.color=good?"":"var(--bad)"}\nfunction renderScan(r){\n const results=r.results||[],meta=document.getElementById("resultsMeta");\n meta.innerHTML="<span>"+results.length.toLocaleString()+" results</span><span>"+(r.type||"")+"</span>";\n document.getElementById("nextCard").classList.toggle("hidden",!r.has_scan);\n const root=document.getElementById("results");\n if(!results.length){root.innerHTML=\'<div class="empty">No scan results.</div>\';return}\n root.innerHTML=results.slice(0,5000).map((x,i)=>\'<div class="result"><div class="result-main"><div class="result-addr">\'+fmtAddr(x.address)+\' · \'+esc(x.type)+\'</div><div class="result-value">\'+esc(x.value)+\'</div>\'+(x.previous===null||x.previous===undefined?\'\':\'<div class="result-prev">previous \'+esc(x.previous)+\'</div>\')+\'</div><div class="result-actions"><button onclick="addResult(\'+i+\')">+</button></div></div>\').join("");\n if(results.length>5000)root.insertAdjacentHTML("beforeend",\'<div class="small" style="padding:10px">Showing first 5,000 results.</div>\');\n}\nasync function addResult(i){try{const r=await api("/api/table/add",{method:"POST",body:JSON.stringify({index:i})});renderTable(r.table);setScanStatus("Address added.")}catch(e){setScanStatus(e.message,false)}}\nasync function addAllResults(){try{const r=await api("/api/table/add_all",{method:"POST",body:"{}"});renderTable(r.table);setScanStatus("Added "+r.added+" address(es).")}catch(e){setScanStatus(e.message,false)}}\nasync function refreshTable(){try{const r=await api("/api/table");renderTable(r.table)}catch(e){setScanStatus(e.message,false)}}\nfunction renderTable(table){\n const root=document.getElementById("addressTable");\n if(!table.length){root.innerHTML=\'<div class="empty">No addresses added.</div>\';return}\n root.innerHTML="";\n table.forEach((x,i)=>{\n  const row=document.createElement("div");row.className="table-row";\n  const main=document.createElement("div");\n  const addr=document.createElement("div");addr.className="table-value";addr.textContent=fmtAddr(x.address);\n  const type=document.createElement("div");type.className="table-type";type.textContent=x.type+(x.frozen?" · FROZEN":"");main.appendChild(addr);main.appendChild(type);\n  const value=document.createElement("input");value.value=x.value??"";value.setAttribute("aria-label","Value for "+fmtAddr(x.address));value.addEventListener("change",()=>writeTable(i,value.value));\n  const actions=document.createElement("div");actions.className="table-actions";\n  const copy=document.createElement("button");copy.type="button";copy.textContent="Copy";copy.addEventListener("click",()=>copyAddress(x.address));\n  const freeze=document.createElement("button");freeze.type="button";freeze.textContent=x.frozen?"ON":"OFF";freeze.addEventListener("click",()=>freezeTable(i,!x.frozen));\n  actions.appendChild(copy);actions.appendChild(freeze);row.appendChild(main);row.appendChild(value);row.appendChild(actions);root.appendChild(row);\n });\n}\nasync function copyAddress(address){\n const text=fmtAddr(address);\n try{\n  if(navigator.clipboard&&navigator.clipboard.writeText)await navigator.clipboard.writeText(text);\n  else{const area=document.createElement("textarea");area.value=text;area.style.position="fixed";area.style.opacity="0";document.body.appendChild(area);area.focus();area.select();document.execCommand("copy");area.remove()}\n  setScanStatus("Copied "+text+".");\n }catch(e){setScanStatus("Copy failed.",false)}\n}\nasync function writeTable(i,value){try{const r=await api("/api/table/write",{method:"POST",body:JSON.stringify({index:i,value})});renderTable(r.table);setScanStatus("Value written.")}catch(e){setScanStatus(e.message,false);await refreshTable()}}\nasync function freezeTable(i,enabled){try{const r=await api("/api/table/freeze",{method:"POST",body:JSON.stringify({index:i,enabled})});renderTable(r.table)}catch(e){setScanStatus(e.message,false)}}\nasync function clearTable(){try{const r=await api("/api/table/clear",{method:"POST",body:"{}"});renderTable(r.table)}catch(e){setScanStatus(e.message,false)}}\n\nfunction toggleCategory(name){collapsedCategories.has(name)?collapsedCategories.delete(name):collapsedCategories.add(name);render(currentState)}\nfunction toggleExpand(i){expanded.has(i)?expanded.delete(i):expanded.add(i);render(currentState)}\nasync function togglePatch(index,enabled){\n if(busy)return;\n busy=true;\n try{\n  await api("/api/toggle",{method:"POST",body:JSON.stringify({index,enabled})});\n  if(currentState&&currentState.patches&&currentState.patches[index]){currentState.patches[index].enabled=!!enabled;render(currentState)}\n  setStatus(enabled?"Patch ON":"Patch OFF");\n }catch(e){setStatus(e.message,false);await refresh(true)}finally{busy=false}\n}\nfunction showAdd(){editingIndex=null;categoryEditing=null;hideCategoryEditor();document.getElementById("editorTitle").textContent="Add Patch";document.getElementById("patchName").value="";renderCategorySelect("Uncategorized");document.getElementById("addressRows").innerHTML="";addAddressRow();document.getElementById("editorCard").classList.remove("hidden");document.getElementById("patchName").focus()}\nfunction hideEditor(){document.getElementById("editorCard").classList.add("hidden");editingIndex=null}\nfunction addAddressRow(a={}){const root=document.getElementById("addressRows"),row=document.createElement("div");row.className="form-row";row.innerHTML=\'<input class="addr" placeholder="0x0088D70B" value="\'+esc(a.address===undefined?"":fmtAddr(a.address))+\'"><select class="typ"><option value="byte">byte</option><option value="word">word</option><option value="dword">dword</option><option value="qword">qword</option><option value="float">float</option></select><input class="val" placeholder="1" value="\'+esc(a.value===undefined?"":a.value)+\'"><button type="button" onclick="this.parentElement.remove()">✕</button>\';root.appendChild(row);row.querySelector(".typ").value=a.type||"dword"}\nfunction collectAddresses(){const rows=[...document.querySelectorAll("#addressRows .form-row")];if(!rows.length)throw new Error("At least one address is required.");return rows.map(row=>({address:row.querySelector(".addr").value.trim(),type:row.querySelector(".typ").value,value:row.querySelector(".val").value.trim()}))}\nfunction editPatch(index){api("/api/state").then(r=>{const p=r.state.patches[index];editingIndex=index;categoryEditing=null;hideCategoryEditor();document.getElementById("editorTitle").textContent="Edit Patch";document.getElementById("patchName").value=p.name||"";renderCategorySelect(p.category);document.getElementById("addressRows").innerHTML="";patchAddresses(p).forEach(a=>addAddressRow(a));document.getElementById("editorCard").classList.remove("hidden");document.getElementById("editorCard").scrollIntoView({behavior:"smooth",block:"nearest"})}).catch(e=>setStatus(e.message,false))}\nasync function savePatch(){if(busy)return;busy=true;const wasEdit=editingIndex!==null;try{const payload={name:document.getElementById("patchName").value.trim(),category:document.getElementById("patchCategory").value,addresses:collectAddresses()};if(!payload.name)throw new Error("Patch name is required.");const path=wasEdit?"/api/edit":"/api/add";if(wasEdit)payload.index=editingIndex;await api(path,{method:"POST",body:JSON.stringify(payload)});hideEditor();await refresh(true);setStatus(wasEdit?"Patch updated":"Patch added")}catch(e){setStatus(e.message,false)}finally{busy=false}}\nasync function deletePatch(index){if(!confirm("Delete this patch? If it is ON, it will be restored first."))return;if(busy)return;busy=true;try{await api("/api/delete",{method:"POST",body:JSON.stringify({index})});expanded.delete(index);await refresh(true);setStatus("Patch deleted")}catch(e){setStatus(e.message,false);await refresh(true)}finally{busy=false}}\nasync function restoreAll(){if(busy)return;busy=true;try{const r=await api("/api/restore",{method:"POST",body:"{}"});await refresh(true);setStatus(r.failed?"Restore completed with failures":"All patches restored",!r.failed)}catch(e){setStatus(e.message,false)}finally{busy=false}}\nfunction showCategoryEditor(){hideEditor();categoryEditing=null;document.getElementById("categoryTitle").textContent="Add Category";document.getElementById("categoryName").value="";document.getElementById("categoryDeleteRow").classList.add("hidden");document.getElementById("categoryCard").classList.remove("hidden");document.getElementById("categoryName").focus()}\nfunction hideCategoryEditor(){document.getElementById("categoryCard").classList.add("hidden");categoryEditing=null}\nfunction editCategory(name){categoryEditing=name;document.getElementById("categoryTitle").textContent="Edit Category";document.getElementById("categoryName").value=name;document.getElementById("categoryDeleteRow").classList.remove("hidden");document.getElementById("categoryCard").classList.remove("hidden");document.getElementById("categoryName").focus()}\nasync function deleteEditingCategory(){if(!categoryEditing)return;await deleteCategory(categoryEditing);hideCategoryEditor()}\nasync function saveCategory(){if(busy)return;busy=true;try{const name=document.getElementById("categoryName").value.trim();if(!name)throw new Error("Category name is required.");if(categoryEditing===null){await api("/api/category/add",{method:"POST",body:JSON.stringify({name})});setStatus("Category added")}else{await api("/api/category/rename",{method:"POST",body:JSON.stringify({old_name:categoryEditing,new_name:name})});setStatus("Category renamed")}hideCategoryEditor();await refresh(true)}catch(e){setStatus(e.message,false)}finally{busy=false}}\nasync function deleteCategory(name){if(!confirm("Delete category \'"+name+"\'? Patches will move to Uncategorized."))return;if(busy)return;busy=true;try{await api("/api/category/delete",{method:"POST",body:JSON.stringify({name})});collapsedCategories.delete(name);await refresh(true);setStatus("Category deleted")}catch(e){setStatus(e.message,false)}finally{busy=false}}\nasync function saveIdentity(){if(busy)return;busy=true;try{await api("/api/identity",{method:"POST",body:JSON.stringify({serial:document.getElementById("serial").value,crc:document.getElementById("crc").value})});await refresh(true);setStatus("Game identity saved")}catch(e){setStatus(e.message,false)}finally{busy=false}}\n\nroute();refresh();refreshTable();setInterval(()=>{if(!busy)refresh(true)},1500);\n</script>\n</body>\n</html>'
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def send_json(self, payload, status=200):
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def send_html(self):
        raw = HTML.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))

        if length > 65536:
            raise ValueError("Request too large.")

        raw = self.rfile.read(length)
        return json.loads(raw.decode() or "{}")

    def do_GET(self):
        path = urlparse(self.path).path

        if path in ("/", "/home", "/scan", "/toggle"):
            self.send_html()
            return

        if path == "/api/state":
            try:
                with PINE_LOCK:
                    self.send_json({"ok": True, "state": get_state()})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
            return

        if path == "/api/table":
            try:
                self.send_json({"ok": True, "table": scanner_table_read()})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
            return

        if path == "/api/scan/state":
            self.send_json({
                "ok": True,
                "results": SCANNER["results"],
                "type": SCANNER["type"],
                "has_scan": SCANNER["has_scan"],
            })
            return

        self.send_json({"ok": False, "error": "Not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path

        try:
            payload = self.read_json()

            if path == "/api/scan/first":
                if SCANNER["active"]:
                    raise RuntimeError("A scan is already running.")

                SCANNER["active"] = True
                try:
                    tolerance = float(payload.get("tolerance", 0) or 0)

                    if tolerance < 0 or not math.isfinite(tolerance):
                        raise ValueError(
                            "Tolerance must be a finite non-negative number."
                        )

                    results = scanner_first_scan(
                        payload.get("value", ""),
                        str(payload.get("type", "dword")).lower(),
                        tolerance,
                        str(payload.get("range", "default")),
                    )
                finally:
                    SCANNER["active"] = False

                result = {
                    "results": results,
                    "type": SCANNER["type"],
                    "has_scan": True,
                }

            elif path == "/api/scan/next":
                if SCANNER["active"]:
                    raise RuntimeError("A scan is already running.")

                SCANNER["active"] = True
                try:
                    mode = str(payload.get("mode", "exact"))
                    tolerance = float(payload.get("tolerance", 0) or 0)

                    if tolerance < 0 or not math.isfinite(tolerance):
                        raise ValueError(
                            "Tolerance must be a finite non-negative number."
                        )

                    results = scanner_next_scan(
                        mode,
                        payload.get("target", ""),
                        tolerance,
                    )
                finally:
                    SCANNER["active"] = False

                result = {
                    "results": results,
                    "type": SCANNER["type"],
                    "has_scan": SCANNER["has_scan"],
                }

            elif path == "/api/scan/clear":
                SCANNER["results"] = []
                SCANNER["has_scan"] = False
                SCANNER["target"] = None
                result = {
                    "results": [],
                    "type": SCANNER["type"],
                    "has_scan": False,
                }

            elif path == "/api/table/add":
                index = int(payload["index"])

                if index < 0 or index >= len(SCANNER["results"]):
                    raise ValueError("Invalid scan result.")

                added = scanner_add_results(
                    [SCANNER["results"][index]]
                )

                result = {
                    "added": added,
                    "table": scanner_table_read(),
                }

            elif path == "/api/table/add_all":
                added = scanner_add_results()

                result = {
                    "added": added,
                    "table": scanner_table_read(),
                }

            elif path == "/api/table/write":
                with PINE_LOCK:
                    scanner_write_item(
                        int(payload["index"]),
                        str(payload.get("value", "")),
                    )

                result = {"table": scanner_table_read()}

            elif path == "/api/table/freeze":
                scanner_set_frozen(
                    int(payload["index"]),
                    bool(payload.get("enabled")),
                )

                result = {"table": scanner_table_read()}

            elif path == "/api/table/clear":
                SCANNER_TABLE.clear()
                result = {"table": []}

            elif path == "/api/toggle":
                set_patch(
                    int(payload["index"]),
                    bool(payload["enabled"]),
                )
                result = {}

            elif path == "/api/add":
                add_patch(payload)
                result = {}

            elif path == "/api/edit":
                edit_patch(
                    int(payload["index"]),
                    payload,
                )
                result = {}

            elif path == "/api/delete":
                delete_patch(int(payload["index"]))
                result = {}

            elif path == "/api/category/add":
                add_category(payload.get("name", ""))
                result = {}

            elif path == "/api/category/rename":
                rename_category(
                    payload.get("old_name", ""),
                    payload.get("new_name", ""),
                )
                result = {}

            elif path == "/api/category/delete":
                delete_category(payload.get("name", ""))
                result = {}

            elif path == "/api/restore":
                result = {"failed": restore_all()}

            elif path == "/api/identity":
                save_identity(
                    payload.get("serial", ""),
                    payload.get("crc", ""),
                )
                result = {}

            else:
                self.send_json(
                    {"ok": False, "error": "Not found"},
                    404,
                )
                return

            self.send_json({"ok": True, **result})

        except Exception as e:
            self.send_json(
                {"ok": False, "error": str(e)},
                400,
            )



def scanner_freeze_loop():
    while True:
        try:
            scanner_freeze_cycle()
        except Exception:
            pass

        threading.Event().wait(0.25)


def main():
    load_backend()
    threading.Thread(target=scanner_freeze_loop, daemon=True).start()

    server = ThreadingHTTPServer(
        (HOST, PORT),
        Handler,
    )

    print()
    print("PINE Web GUI")
    print(f"http://{HOST}:{PORT}")
    print("Press Ctrl+C to stop.")
    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        search.stop_all_freezes()

        try:
            pine.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
