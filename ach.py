"""
ARMSX2 RetroAchievements browser/debugger over PINE.

Features:
- List and inspect achievements
- View detailed achievement information
- Watch achievement state live while playing
- Unlock a selected achievement
- Unlock ALL achievements
- Confirmation before unlocking all achievements
- Success/failure handling for unlock operations
"""

import sys
import time

from pine_client import Pine, PineError


BUCKET_NAMES = {
    0: "Unknown",
    1: "Locked",
    2: "Unlocked",
    3: "Unsupported",
    4: "Unofficial",
    5: "Recently Unlocked",
    6: "Active Challenge",
    7: "Almost There",
}

BUCKET_ORDER = [6, 5, 2, 7, 1, 4, 3]


def fmt_bool(b):
    return "Yes" if b else "No"


def print_game_summary(data):
    if not data.get("active"):
        print("No game with achievements currently loaded.")

    print(f"Logged in: {fmt_bool(data.get('loggedIn'))}", end="")

    if data.get("loggedIn"):
        print(f"  ({data.get('userName', '?')})", end="")

    print()

    print(f"Hardcore mode: {fmt_bool(data.get('hardcore'))}")

    items = data.get("items", [])

    unlocked = sum(
        1 for it in items
        if it.get("unlocked")
    )

    total_pts = sum(
        it.get("points", 0)
        for it in items
    )

    unlocked_pts = sum(
        it.get("points", 0)
        for it in items
        if it.get("unlocked")
    )

    print(
        f"Achievements: {unlocked}/{len(items)} unlocked"
        f"  ({unlocked_pts}/{total_pts} points)"
    )


def print_achievement_info(a):
    print("-" * 50)
    print(f"Title:            {a.get('title')}")
    print(f"ID:               {a.get('id')}")
    print(f"Description:      {a.get('description')}")
    print(f"Points:           {a.get('points')}")
    print(f"Unlocked:         {fmt_bool(a.get('unlocked'))}")
    print(
        f"Bucket:           "
        f"{BUCKET_NAMES.get(a.get('bucket'), a.get('bucket'))}"
    )
    print(f"Active challenge: {fmt_bool(a.get('primed'))}")
    print(f"Subset ID:        {a.get('subsetId')}")
    print(
        f"Type:              {a.get('type')}"
        "  (0=standard, see rc_client.h for others)"
    )

    # unlockedMask is a bitmask:
    # bit 0 = softcore
    # bit 1 = hardcore
    mask = a.get("unlockedMask", 0)

    print(
        "Unlock mode:      "
        f"softcore={fmt_bool(mask & 1)} "
        f"hardcore={fmt_bool(mask & 2)}"
    )

    print(f"Rarity:           {a.get('rarity')}%")

    mp = a.get("measuredProgress")

    if mp:
        print(
            f"Measured progress:{mp}"
            f"  ({a.get('measuredPercent')}%)"
        )

    print("-" * 50)


def unlock_selected_achievement(
    p: Pine,
    achievement_id: int,
    title: str = None,
):
    """
    Unlock one achievement through PINE.
    """

    print()

    if title:
        print(f"Unlocking: {title}")
    else:
        print(f"Unlocking achievement ID: {achievement_id}")

    try:
        success = p.unlock_achievement(achievement_id)

        if success:
            print(
                "\n[SUCCESS] Unlock command was accepted by ARMSX2."
            )
        else:
            print(
                "\n[FAILED] ARMSX2 reported that the unlock command "
                "was not accepted."
            )

    except PineError as e:
        print(f"\n[PINE ERROR] {e}")

    except Exception as e:
        print(
            "\n[ERROR] Unexpected error while unlocking achievement:"
        )
        print(f"        {e}")

    input("\nPress Enter to continue...")


def unlock_achievement_range(p: Pine):
    """
    Unlock a customizable range of achievements from the currently loaded list.

    The range uses the same numbering shown by List / inspect achievements.
    A configurable delay is used between unlock commands to avoid hammering
    the achievement service when a game has a large achievement set.
    """

    try:
        data = p.get_achievements()
    except PineError as e:
        print(f"\n[PINE error: {e}]")
        input("Press Enter to continue...")
        return

    items = data.get("items", [])

    if not items:
        print("\nNo achievements available.")
        input("Press Enter to continue...")
        return

    numbered = []
    for bucket_id in BUCKET_ORDER:
        bucket_items = [
            it for it in items
            if it.get("bucket") == bucket_id
        ]

        for it in bucket_items:
            numbered.append(it)

    print()
    print("=" * 50)
    print("        UNLOCK ACHIEVEMENT RANGE")
    print("=" * 50)
    print()
    print(f"Available achievements: {len(numbered)}")
    print()
    print("The numbers use the same ordering as the achievement list.")
    print("Already-unlocked achievements will be skipped.")
    print()

    start_text = input(
        f"Start number [1]: "
    ).strip()

    end_text = input(
        f"End number [{min(50, len(numbered))}]: "
    ).strip()

    delay_text = input(
        "Delay between unlocks in seconds [0.25]: "
    ).strip()

    try:
        start = int(start_text) if start_text else 1
        end = (
            int(end_text)
            if end_text
            else min(50, len(numbered))
        )
        delay = float(delay_text) if delay_text else 0.25
    except ValueError:
        print("\nInvalid number.")
        input("Press Enter to continue...")
        return

    if start < 1 or end < 1:
        print("\nAchievement numbers must be 1 or higher.")
        input("Press Enter to continue...")
        return

    if start > len(numbered) or end > len(numbered):
        print(
            f"\nRange must be between 1 and {len(numbered)}."
        )
        input("Press Enter to continue...")
        return

    if start > end:
        print("\nStart number cannot be greater than end number.")
        input("Press Enter to continue...")
        return

    if delay < 0:
        print("\nDelay cannot be negative.")
        input("Press Enter to continue...")
        return

    selected = numbered[start - 1:end]
    locked = [a for a in selected if not a.get("unlocked")]

    if not locked:
        print("\nAll achievements in this range are already unlocked.")
        input("Press Enter to continue...")
        return

    print()
    print(
        f"Selected range: {start}-{end} "
        f"({len(selected)} achievements)"
    )
    print(f"Will unlock: {len(locked)}")
    print(f"Delay: {delay:.2f} seconds")
    print()

    for number, achievement in enumerate(selected, start=start):
        if achievement.get("unlocked"):
            print(
                f"[{number:>3}] SKIP  "
                f"{achievement.get('title', 'Unknown')}"
            )
        else:
            print(
                f"[{number:>3}]       "
                f"{achievement.get('title', 'Unknown')}"
            )

    print()
    print(
        "This sends one unlock command at a time with the selected delay."
    )
    print()

    confirmation = input(
        "Type YES to continue, or anything else to cancel: "
    ).strip()

    if confirmation != "YES":
        print("\nCancelled. No unlock commands were sent.")
        input("Press Enter to continue...")
        return

    print()
    success_count = 0
    failed_count = 0
    skipped_count = 0

    for number, achievement in enumerate(selected, start=start):
        title = achievement.get("title", "Unknown")
        achievement_id = achievement.get("id")

        if achievement.get("unlocked"):
            skipped_count += 1
            print(
                f"[{number:>3}] SKIP   {title}"
            )
            continue

        print(
            f"[{number:>3}] UNLOCK {title} "
            f"(ID {achievement_id})"
        )

        try:
            success = p.unlock_achievement(achievement_id)

            if success:
                success_count += 1
                print("      -> accepted")
            else:
                failed_count += 1
                print("      -> failed")

        except PineError as e:
            failed_count += 1
            print(f"      -> PINE ERROR: {e}")

        except Exception as e:
            failed_count += 1
            print(f"      -> ERROR: {e}")

        if delay > 0:
            time.sleep(delay)

    print()
    print("=" * 50)
    print("Range unlock finished")
    print("=" * 50)
    print(f"Unlocked/accepted: {success_count}")
    print(f"Skipped:           {skipped_count}")
    print(f"Failed:            {failed_count}")
    print()

    input("Press Enter to continue...")


def unlock_all_achievements(p: Pine):
    """
    Unlock all achievements through PINE.

    Requires explicit confirmation before sending the command.
    """

    print()
    print("=" * 50)
    print("        UNLOCK ALL ACHIEVEMENTS")
    print("=" * 50)
    print()
    print(
        "This will send the Unlock All command to ARMSX2."
    )
    print(
        "All currently loaded achievements will be submitted"
    )
    print(
        "to the native achievement system."
    )
    print()

    confirmation = input(
        "Type YES to continue, or anything else to cancel: "
    ).strip()

    if confirmation != "YES":
        print("\nCancelled. No unlock command was sent.")
        input("Press Enter to continue...")
        return

    print()
    print("Sending Unlock All command...")

    try:
        success = p.unlock_all_achievements()

        if success:
            print(
                "\n[SUCCESS] Unlock All command was accepted "
                "by ARMSX2."
            )
        else:
            print(
                "\n[FAILED] ARMSX2 reported that the Unlock All "
                "command was not accepted."
            )

    except PineError as e:
        print(f"\n[PINE ERROR] {e}")

    except Exception as e:
        print(
            "\n[ERROR] Unexpected error while unlocking all:"
        )
        print(f"        {e}")

    input("\nPress Enter to continue...")


def list_menu(p: Pine):
    while True:
        try:
            data = p.get_achievements()
        except PineError as e:
            print(f"\n[PINE error: {e}]")
            input("Press Enter to go back...")
            return

        items = data.get("items", [])

        if not items:
            print(
                "\nNo achievements available "
                "(no game loaded, or none defined)."
            )
            input("Press Enter to go back...")
            return

        # Group + number for selection.
        numbered = []

        print("\n=== Achievement List ===")

        for bucket_id in BUCKET_ORDER:
            bucket_items = [
                it for it in items
                if it.get("bucket") == bucket_id
            ]

            if not bucket_items:
                continue

            print(
                f"\n-- {BUCKET_NAMES.get(bucket_id, bucket_id)} --"
            )

            for it in bucket_items:
                numbered.append(it)

                mark = "*" if it.get("unlocked") else " "

                print(
                    f"[{len(numbered)}] "
                    f"{mark} "
                    f"{it.get('title')}  "
                    f"({it.get('points')} pts)"
                )

        print()
        print("[R] Refresh   [X] Back")

        choice = input(
            "Select achievement number, or option: "
        ).strip().lower()

        if choice == "x":
            return

        if choice == "r":
            continue

        if choice.isdigit():
            number = int(choice)

            if 1 <= number <= len(numbered):
                selected = numbered[number - 1]

                achievement_detail_menu(
                    p,
                    selected.get("id"),
                )
                continue

        print("Invalid choice.")


def achievement_detail_menu(
    p: Pine,
    achievement_id: int,
):
    """
    Shows one achievement's information.

    Existing live-watch functionality is preserved.
    """

    while True:
        try:
            data = p.get_achievements()
        except PineError as e:
            print(f"\n[PINE error: {e}]")
            input("Press Enter to go back...")
            return

        match = next(
            (
                it
                for it in data.get("items", [])
                if it.get("id") == achievement_id
            ),
            None,
        )

        if not match:
            print(
                "Achievement no longer found "
                "(game/list changed)."
            )
            input("Press Enter to continue...")
            return

        print_achievement_info(match)

        print(
            "[U] Unlock this achievement"
            "   [W] Watch live"
        )
        print(
            "[R] Refresh"
            "   [X] Back"
        )

        choice = input("> ").strip().lower()

        if choice == "x":
            return

        elif choice == "w":
            watch_achievement(
                p,
                achievement_id,
            )

        elif choice == "r":
            continue

        elif choice == "u":
            title = match.get("title", "Unknown")

            unlock_selected_achievement(
                p,
                achievement_id,
                title,
            )

        else:
            print("Invalid choice.")


def watch_achievement(
    p: Pine,
    achievement_id: int,
):
    print(
        "Watching - play the game now. "
        "Ctrl+C to stop.\n"
    )

    last_state = None

    try:
        while True:
            try:
                data = p.get_achievements()

            except PineError as e:
                print(f"\n[PINE error: {e}]")
                time.sleep(1)
                continue

            match = next(
                (
                    it
                    for it in data.get("items", [])
                    if it.get("id") == achievement_id
                ),
                None,
            )

            if match:
                state = (
                    match.get("unlocked"),
                    match.get("measuredProgress"),
                    match.get("measuredPercent"),
                )

                if state != last_state:
                    ts = time.strftime("%H:%M:%S")

                    print(
                        f"[{ts}] "
                        f"unlocked={state[0]}  "
                        f"progress={state[1]}  "
                        f"({state[2]}%)"
                    )

                    last_state = state

            time.sleep(1)

    except KeyboardInterrupt:
        print("\nStopped watching.")


def main_menu():
    p = Pine()

    while True:
        try:
            data = p.get_achievements()

            print(
                "\n=== ARMSX2 Achievement Debugger ==="
            )

            print_game_summary(data)

        except PineError as e:
            print(f"\n[PINE error: {e}]")

        print()
        print("[1] List / inspect achievements")
        print("[2] Unlock ALL achievements")
        print("[3] Unlock achievement range")
        print("[4] Refresh game summary")
        print("[Q] Quit")

        choice = input("> ").strip().lower()

        if choice == "1":
            list_menu(p)

        elif choice == "2":
            unlock_all_achievements(p)

        elif choice == "3":
            unlock_achievement_range(p)

        elif choice == "4":
            continue

        elif choice == "q":
            print("Bye.")
            sys.exit(0)

        else:
            print("Invalid choice.")


if __name__ == "__main__":
    main_menu()