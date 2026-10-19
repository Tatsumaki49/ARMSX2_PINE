from pathlib import Path
import re
import py_compile


def replace(text, pattern, value):
    out, count = re.subn(pattern, value, text, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError(f"Pattern not found: {pattern}")
    return out


p = Path("search.py")
s = p.read_text()
s = replace(s, r'def choose_scan_type\(default="dword"\):.*?\n\ndef parse_scan_number', r'''def choose_scan_type(default="all"):
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


def parse_scan_number''')
s = replace(s, r'def get_scan_tolerance\(typ\):.*?\n\ndef choose_scan_ranges', r'''def get_scan_tolerance(typ):
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


def choose_scan_ranges''')
s = replace(s, r'def find_value\(pine\):.*?\n\ndef read_value', r'''def find_value(pine):
    ranges = choose_scan_ranges()
    if not ranges:
        return
    typ = choose_scan_type()
    text = input("\nFind exact value: ").strip()
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
    print()
    print(f"First scan: {typ} == {text}")
    for scan_typ in scan_types:
        try:
            scan_target = parse_scan_number(text, scan_typ)
        except Exception:
            continue
        print(f"\nType: {scan_typ}")
        for name, start, end in ranges:
            print(f"\n{name}\n0x{start:08X} - 0x{end:08X}")
            scan_range(pine, start, end, scan_typ, scan_target, tolerance if scan_typ in ("float", "double") else 0.0, results)
            print()
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


def read_value''')
s = replace(s, r'def run_next_scan\(pine, mode=None\):.*?\n\ndef next_scan', r'''def run_next_scan(pine, mode=None):
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
    print(f"\nNext scan: {mode} ({scan_typ})")
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


def next_scan''')
p.write_text(s)

p = Path("web_gui.py")
s = p.read_text()
s = replace(s, r'def scanner_first_scan\(target_text, typ, tolerance, range_name\):.*?\n\ndef scanner_next_scan', r'''def scanner_first_scan(target_text, typ, tolerance, range_name):
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


def scanner_next_scan''')
s = replace(s, r'def scanner_next_scan\(mode, target_text=None, tolerance=0\.0\):.*?\n\ndef scanner_add_results', r'''def scanner_next_scan(mode, target_text=None, tolerance=0.0):
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


def scanner_add_results''')
old = '<select id="scanType"><option value="byte">byte</option><option value="word">word</option><option value="dword" selected>dword</option><option value="qword">qword</option><option value="float">float</option><option value="double">double</option></select>'
new = '<select id="scanType"><option value="all" selected>All / Unknown Type</option><option value="byte">byte</option><option value="word">word</option><option value="dword">dword</option><option value="qword">qword</option><option value="float">float</option><option value="double">double</option></select>'
if old not in s:
    raise RuntimeError("scanType select not found")
s = s.replace(old, new, 1).replace('Tolerance (float/double)', 'Tolerance (All / float / double)', 1)
p.write_text(s)

py_compile.compile("search.py", doraise=True)
py_compile.compile("web_gui.py", doraise=True)
