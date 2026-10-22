"""Local, opt-in usage logging for ARMSX2_PINE.

Only interpreted feature actions and scan summaries are logged. Raw keystrokes,
memory contents, and individual result addresses are not collected.
"""
import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

USAGE_DIR = Path(".usage")
STATE_FILE = USAGE_DIR / "state.json"
EVENT_FILE = USAGE_DIR / "events.jsonl"
SESSION_ID = str(uuid.uuid4())
_CONTEXT = {}


def _load_enabled():
    try:
        with STATE_FILE.open("r", encoding="utf-8") as f:
            state = json.load(f)
        return bool(state.get("enabled", False)) if isinstance(state, dict) else False
    except Exception:
        return False


def is_enabled():
    return _load_enabled()


def set_enabled(enabled):
    try:
        USAGE_DIR.mkdir(parents=True, exist_ok=True)
        with STATE_FILE.open("w", encoding="utf-8") as f:
            json.dump({"enabled": bool(enabled)}, f, indent=2)
        return True
    except Exception as exc:
        print(f"Could not save usage logger setting: {exc}")
        return False


def set_context(**details):
    _CONTEXT.update({key: value for key, value in details.items() if value is not None})


def _write_event(event, details):
    try:
        USAGE_DIR.mkdir(parents=True, exist_ok=True)
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": SESSION_ID,
            "event": event,
            "context": dict(_CONTEXT),
            **details,
        }
        with EVENT_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception:
        # Logging must never interrupt the scanner.
        pass


def log_event(event, **details):
    if is_enabled():
        _write_event(event, details)


@contextmanager
def track_action(action, **details):
    enabled_at_start = is_enabled()
    start = __import__("time").perf_counter()
    if enabled_at_start:
        _write_event("action_started", {"action": action, **details})
    try:
        yield
    except Exception as exc:
        if enabled_at_start:
            _write_event(
                "action_failed",
                {
                    "action": action,
                    "duration_ms": round((__import__("time").perf_counter() - start) * 1000, 2),
                    "error_type": type(exc).__name__,
                    **details,
                },
            )
        raise
    else:
        if enabled_at_start:
            _write_event(
                "action_finished",
                {
                    "action": action,
                    "duration_ms": round((__import__("time").perf_counter() - start) * 1000, 2),
                    **details,
                },
            )


def usage_logger_menu():
    while True:
        enabled = is_enabled()
        print()
        print("=" * 65)
        print("USAGE LOGGER")
        print("=" * 65)
        print(f"Status: {'ON' if enabled else 'OFF'}")
        print("Logs are stored locally in .usage/events.jsonl")
        print("Only feature actions and scan summaries are recorded.")
        print()
        print("[1] Turn ON" if not enabled else "[1] Turn OFF")
        print("[x] Back")
        choice = input("> ").strip().lower()

        if choice == "x":
            log_event("usage_logger_menu_exit", status="on" if is_enabled() else "off")
            return
        if choice == "1":
            new_state = not enabled
            if set_enabled(new_state):
                print(f"Usage logger {'enabled' if new_state else 'disabled'}.")
                if new_state:
                    log_event("usage_logger_enabled")
            continue
        print("Invalid option.")
