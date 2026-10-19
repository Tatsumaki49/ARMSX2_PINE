"""
Pure-Python PINE client for ARMSX2 / PCSX2.
No native libraries required — just sockets. Works fine in Termux.

Wire protocol:
Request:
    [u32 LE total_size] [command...]*

Each command:
    [u8 opcode][payload]

Response:
    [u32 LE total_size][u8 result: 0=OK, 0xFF=FAIL][reply...]

ARMSX2-local extensions:
    0x16 = Toggle patch
    0x17 = Get achievements
    0x18 = Unlock one achievement
    0x19 = Unlock all achievements
"""

import json
import math
import socket
import struct


# ---------------------------------------------------------------------------
# Standard PINE commands
# ---------------------------------------------------------------------------

MSG_READ8 = 0
MSG_READ16 = 1
MSG_READ32 = 2
MSG_READ64 = 3

MSG_WRITE8 = 4
MSG_WRITE16 = 5
MSG_WRITE32 = 6
MSG_WRITE64 = 7

MSG_VERSION = 8
MSG_SAVE_STATE = 9
MSG_LOAD_STATE = 0xA
MSG_TITLE = 0xB
MSG_ID = 0xC
MSG_UUID = 0xD
MSG_GAME_VERSION = 0xE
MSG_STATUS = 0xF


# ---------------------------------------------------------------------------
# ARMSX2-local PINE extensions
# ---------------------------------------------------------------------------

# Patch control
MSG_TOGGLE_PATCH = 0x16

# Achievement control
MSG_GET_ACHIEVEMENTS = 0x17
MSG_UNLOCK_ACHIEVEMENT = 0x18
MSG_UNLOCK_ALL_ACHIEVEMENTS = 0x19


# ---------------------------------------------------------------------------
# PINE limits
# ---------------------------------------------------------------------------

MAX_IPC_SIZE = 650000
MAX_IPC_RETURN_SIZE = 450000


class PineError(Exception):
    pass


class Pine:
    def __init__(self, host="127.0.0.1", port=28011):
        self.sock = socket.create_connection(
            (host, port),
            timeout=5
        )
        self.sock.settimeout(5)

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # Low-level socket helpers
    # -----------------------------------------------------------------------

    def _recv_exact(self, n):
        if n < 0:
            raise PineError("invalid receive size")

        buf = bytearray()

        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))

            if not chunk:
                raise PineError(
                    "socket closed unexpectedly"
                )

            buf += chunk

        return bytes(buf)

    def _send_batch(self, commands):
        """
        Send one or more PINE commands.

        commands:
            list of raw command bytes

        Returns:
            reply payload after the 1-byte result code.

        Raises:
            PineError if the server rejects the batch.
        """

        if not commands:
            raise PineError("empty PINE command batch")

        body = b"".join(commands)

        total_size = 4 + len(body)

        if total_size < 4:
            raise PineError("invalid PINE request size")

        if total_size > MAX_IPC_SIZE:
            raise PineError(
                f"batch too large: "
                f"{total_size} > {MAX_IPC_SIZE}"
            )

        request = struct.pack(
            "<I",
            total_size
        ) + body

        try:
            self.sock.sendall(request)
        except socket.timeout:
            raise PineError(
                "timeout while sending PINE request"
            )
        except OSError as e:
            raise PineError(
                f"failed to send PINE request: {e}"
            )

        # ---------------------------------------------------------------
        # Read response header
        # ---------------------------------------------------------------

        hdr = self._recv_exact(4)

        reply_size = struct.unpack(
            "<I",
            hdr
        )[0]

        if reply_size < 5:
            raise PineError(
                f"invalid PINE reply size: {reply_size}"
            )

        if reply_size > MAX_IPC_RETURN_SIZE:
            raise PineError(
                f"PINE reply too large: "
                f"{reply_size} > {MAX_IPC_RETURN_SIZE}"
            )

        remaining = self._recv_exact(
            reply_size - 4
        )

        if len(remaining) < 1:
            raise PineError(
                "PINE response missing result code"
            )

        result_code = remaining[0]

        if result_code != 0x00:
            raise PineError(
                "PINE server returned FAIL for this batch "
                "(no VM loaded, invalid command, or invalid data)"
            )

        return remaining[1:]

    # -----------------------------------------------------------------------
    # Memory
    # -----------------------------------------------------------------------

    def read8(self, addr):
        raw = self._send_batch([
            struct.pack(
                "<BI",
                MSG_READ8,
                addr & 0xFFFFFFFF
            )
        ])

        if len(raw) < 1:
            raise PineError("invalid read8 response")

        return raw[0]

    def read16(self, addr):
        raw = self._send_batch([
            struct.pack(
                "<BI",
                MSG_READ16,
                addr & 0xFFFFFFFF
            )
        ])

        if len(raw) < 2:
            raise PineError("invalid read16 response")

        return struct.unpack(
            "<H",
            raw[:2]
        )[0]

    def read32(self, addr):
        raw = self._send_batch([
            struct.pack(
                "<BI",
                MSG_READ32,
                addr & 0xFFFFFFFF
            )
        ])

        if len(raw) < 4:
            raise PineError("invalid read32 response")

        return struct.unpack(
            "<I",
            raw[:4]
        )[0]

    def read64(self, addr):
        raw = self._send_batch([
            struct.pack(
                "<BI",
                MSG_READ64,
                addr & 0xFFFFFFFF
            )
        ])

        if len(raw) < 8:
            raise PineError("invalid read64 response")

        return struct.unpack(
            "<Q",
            raw[:8]
        )[0]

    def write8(self, addr, val):
        self._send_batch([
            struct.pack(
                "<BIB",
                MSG_WRITE8,
                addr & 0xFFFFFFFF,
                val & 0xFF
            )
        ])

    def write16(self, addr, val):
        self._send_batch([
            struct.pack(
                "<BIH",
                MSG_WRITE16,
                addr & 0xFFFFFFFF,
                val & 0xFFFF
            )
        ])

    def write32(self, addr, val):
        self._send_batch([
            struct.pack(
                "<BII",
                MSG_WRITE32,
                addr & 0xFFFFFFFF,
                val & 0xFFFFFFFF
            )
        ])

    def readfloat(self, addr):
        raw = self.read32(addr)
        return struct.unpack("<f", struct.pack("<I", raw))[0]

    def writefloat(self, addr, value):
        value = float(value)
        if not math.isfinite(value):
            raise PineError("float value must be finite")
        raw = struct.unpack("<I", struct.pack("<f", value))[0]
        self.write32(addr, raw)

    def readdouble(self, addr):
        raw = self.read64(addr)
        return struct.unpack("<d", struct.pack("<Q", raw))[0]

    def writedouble(self, addr, value):
        value = float(value)
        if not math.isfinite(value):
            raise PineError("double value must be finite")
        raw = struct.unpack("<Q", struct.pack("<d", value))[0]
        self.write64(addr, raw)

    def write64(self, addr, val):
        self._send_batch([
            struct.pack(
                "<BIQ",
                MSG_WRITE64,
                addr & 0xFFFFFFFF,
                val & 0xFFFFFFFFFFFFFFFF
            )
        ])

    # -----------------------------------------------------------------------
    # Emulator status
    # -----------------------------------------------------------------------
    
    def _string_command(self, opcode):
        raw = self._send_batch([
            struct.pack("<B", opcode)
        ])

        if len(raw) < 4:
            raise PineError("invalid PINE string response")

        size = struct.unpack("<I", raw[:4])[0]

        if size > len(raw) - 4:
            raise PineError(
                f"invalid PINE string size: {size}"
            )

        return raw[4:4 + size].split(
            b"\x00",
            1
        )[0].decode(
            "utf-8",
            "replace"
        )

    def status(self):
        raw = self._send_batch([
            struct.pack(
                "<B",
                MSG_STATUS
            )
        ])

        if len(raw) < 4:
            raise PineError(
                "invalid status response"
            )

        val = struct.unpack(
            "<I",
            raw[:4]
        )[0]

        return {
            0: "Running",
            1: "Paused",
            2: "Shutdown",
        }.get(
            val,
            f"Unknown({val})"
        )
        
    def version(self):
        return self._string_command(MSG_VERSION)

    def save_state(self):
        self._send_batch([
            struct.pack("<B", MSG_SAVE_STATE)
        ])

    def load_state(self):
        self._send_batch([
            struct.pack("<B", MSG_LOAD_STATE)
        ])

    def title(self):
        return self._string_command(MSG_TITLE)

    def game_id(self):
        return self._string_command(MSG_ID)

    def serial(self):
        return self.game_id()

    def get_serial(self):
        return self.serial()

    def uuid(self):
        return self._string_command(MSG_UUID)

    def crc(self):
        return self.uuid()

    def get_crc(self):
        return self.crc()

    def game_version(self):
        return self._string_command(MSG_GAME_VERSION)

    # -----------------------------------------------------------------------
    # Patch control
    # -----------------------------------------------------------------------

    def toggle_patch(
        self,
        name: str,
        enabled: bool,
        cheat: bool = False
    ) -> None:
        """
        Enable or disable an ARMSX2 patch group.

        name:
            Patch group name.

        enabled:
            True = enable
            False = disable

        cheat:
            True if this is a cheat group instead of a normal patch group.
        """

        if not isinstance(name, str):
            raise PineError(
                "patch name must be a string"
            )

        if not name:
            raise PineError(
                "patch name cannot be empty"
            )

        name_b = name.encode(
            "utf-8"
        )

        command = struct.pack(
            "<BBB",
            MSG_TOGGLE_PATCH,
            1 if cheat else 0,
            1 if enabled else 0
        )

        command += struct.pack(
            "<I",
            len(name_b)
        )

        command += name_b

        self._send_batch([
            command
        ])

    # -----------------------------------------------------------------------
    # Achievements
    # -----------------------------------------------------------------------

    def get_achievements(self) -> dict:
        """
        Get the complete achievement state from ARMSX2.

        The native PINE 0x17 handler is expected to return:

            [u32 JSON_size]
            [UTF-8 JSON]
        """

        raw = self._send_batch([
            struct.pack(
                "<B",
                MSG_GET_ACHIEVEMENTS
            )
        ])

        if len(raw) < 4:
            raise PineError(
                "invalid achievement response"
            )

        json_size = struct.unpack(
            "<I",
            raw[:4]
        )[0]

        if json_size < 1:
            raise PineError(
                "achievement response contains empty JSON"
            )

        if json_size > MAX_IPC_RETURN_SIZE:
            raise PineError(
                "achievement JSON is too large"
            )

        if len(raw) < 4 + json_size:
            raise PineError(
                "achievement response is truncated"
            )

        payload = raw[
            4:4 + json_size
        ]

        payload = payload.split(
            b"\x00",
            1
        )[0]

        try:
            text = payload.decode(
                "utf-8",
                "replace"
            )

            return json.loads(
                text
            )

        except json.JSONDecodeError as e:
            raise PineError(
                f"invalid achievement JSON: {e}"
            )

    def unlock_achievement(
        self,
        achievement_id: int
    ) -> bool:
        """
        Unlock one achievement.

        Native protocol:

            0x18
            [u32 achievement_id]

        Expected native reply:

            [u8 success]

        success:
            0 = failed
            non-zero = accepted

        IMPORTANT:
        This confirms that the ARMSX2 native handler accepted the
        request. It does not independently prove that RetroAchievements
        has already committed the unlock to the server.
        """

        try:
            achievement_id = int(
                achievement_id
            )
        except (TypeError, ValueError):
            raise PineError(
                "achievement ID must be an integer"
            )

        if achievement_id <= 0:
            raise PineError(
                f"invalid achievement ID: {achievement_id}"
            )

        if achievement_id > 0xFFFFFFFF:
            raise PineError(
                f"achievement ID out of range: {achievement_id}"
            )

        command = struct.pack(
            "<BI",
            MSG_UNLOCK_ACHIEVEMENT,
            achievement_id
        )

        raw = self._send_batch([
            command
        ])

        if len(raw) < 1:
            raise PineError(
                "invalid unlock achievement response: "
                "native handler returned no success byte"
            )

        return raw[0] != 0

    def unlock_all_achievements(self) -> bool:
        """
        Ask ARMSX2 to unlock all achievements currently belonging
        to the loaded achievement set.

        Native protocol:

            0x19

        Expected native reply:

            [u8 success]
        """

        raw = self._send_batch([
            struct.pack(
                "<B",
                MSG_UNLOCK_ALL_ACHIEVEMENTS
            )
        ])

        if len(raw) < 1:
            raise PineError(
                "invalid unlock-all response: "
                "native handler returned no success byte"
            )

        return raw[0] != 0

    # -----------------------------------------------------------------------
    # Title
    # -----------------------------------------------------------------------

    def title(self):
        raw = self._send_batch([struct.pack("<B", MSG_TITLE)])
        size = struct.unpack("<I", raw[:4])[0]
        return raw[4:4 + size].split(b"\x00", 1)[0].decode("utf-8", "replace")

    # -----------------------------------------------------------------------
    # Bulk memory read
    # -----------------------------------------------------------------------

    def read_block(
        self,
        addr,
        num_words
    ):
        """
        Read num_words consecutive u32 values.

        Returns:
            bytes containing num_words * 4 bytes.
        """

        if num_words < 0:
            raise PineError(
                "num_words cannot be negative"
            )

        if num_words == 0:
            return b""

        max_per_batch = min(
            (MAX_IPC_SIZE - 4) // 5,
            (MAX_IPC_RETURN_SIZE - 5) // 4,
        )

        out = bytearray()

        i = 0

        while i < num_words:
            n = min(
                max_per_batch,
                num_words - i
            )

            commands = [
                struct.pack(
                    "<BI",
                    MSG_READ32,
                    (
                        addr +
                        (i + j) * 4
                    ) & 0xFFFFFFFF
                )
                for j in range(n)
            ]

            result = self._send_batch(
                commands
            )

            expected_size = n * 4

            if len(result) != expected_size:
                raise PineError(
                    "invalid read_block response: "
                    f"expected {expected_size} bytes, "
                    f"received {len(result)}"
                )

            out += result

            i += n

        return bytes(out)


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    p = None

    try:
        p = Pine()

        print(
            "PINE connected."
        )

        print(
            "status:",
            p.status()
        )

        print(
            "title:",
            p.title()
        )
        
        print(
            "serial:",
            p.serial()
        )

        if len(sys.argv) > 1:
            addr = int(
                sys.argv[1],
                16
            )

            value = p.read32(
                addr
            )

            print(
                f"0x{addr:08X} = "
                f"0x{value:08X}"
            )

    except PineError as e:
        print(
            f"PINE error: {e}"
        )

        sys.exit(1)

    except OSError as e:
        print(
            f"Connection error: {e}"
        )

        sys.exit(1)

    finally:
        if p is not None:
            p.close()
