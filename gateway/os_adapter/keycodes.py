"""macOS key names -> CGEvent keycodes and modifier flags.

Keycodes are Apple's stable virtual-keycode constants (Carbon.HIToolbox
`kVK_*`, fixed ABI values). pyobjc 12 no longer ships the HIToolbox
bindings, so they are enumerated here by value — these numbers are
documented and stable across macOS releases.

Platform-specific by design (Engineering Decision #11): lives inside
`os_adapter/`, imported nowhere else in the Gateway.
"""

from __future__ import annotations

from Quartz import (
    kCGEventFlagMaskAlternate,
    kCGEventFlagMaskCommand,
    kCGEventFlagMaskControl,
    kCGEventFlagMaskShift,
)

# ANSI keycodes (kVK_ANSI_*)
KEYCODES: dict[str, int] = {
    "a": 0x00, "s": 0x01, "d": 0x02, "f": 0x03, "h": 0x04, "g": 0x05,
    "z": 0x06, "x": 0x07, "c": 0x08, "v": 0x09, "b": 0x0B, "q": 0x0C,
    "w": 0x0D, "e": 0x0E, "r": 0x0F, "y": 0x10, "t": 0x11,
    "1": 0x12, "2": 0x13, "3": 0x14, "4": 0x15, "6": 0x16, "5": 0x17,
    "=": 0x18, "9": 0x19, "7": 0x1A, "-": 0x1B, "8": 0x1C, "0": 0x1D,
    "]": 0x1E, "o": 0x1F, "u": 0x20, "[": 0x21, "i": 0x22, "p": 0x23,
    "l": 0x25, "j": 0x26, "'": 0x27, "k": 0x28, ";": 0x29, "\\": 0x2A,
    ",": 0x2B, "/": 0x2C, "n": 0x2D, "m": 0x2E, ".": 0x2F, "`": 0x32,
    # special keys (kVK_*)
    "enter": 0x24, "return": 0x24, "tab": 0x30, "space": 0x31,
    "backspace": 0x33, "delete": 0x33, "escape": 0x35, "esc": 0x35,
    "forward_delete": 0x75, "home": 0x73, "end": 0x77,
    "page_up": 0x74, "page_down": 0x79,
    "left": 0x7B, "right": 0x7C, "down": 0x7D, "up": 0x7E,
    "keypad_enter": 0x4C,
    "f1": 0x7A, "f2": 0x78, "f3": 0x63, "f4": 0x76, "f5": 0x60,
    "f6": 0x61, "f7": 0x62, "f8": 0x64, "f9": 0x65, "f10": 0x6D,
    "f11": 0x67, "f12": 0x6F,
}

MODIFIERS: dict[str, int] = {
    "cmd": kCGEventFlagMaskCommand,
    "command": kCGEventFlagMaskCommand,
    "ctrl": kCGEventFlagMaskControl,
    "control": kCGEventFlagMaskControl,
    "alt": kCGEventFlagMaskAlternate,
    "option": kCGEventFlagMaskAlternate,
    "opt": kCGEventFlagMaskAlternate,
    "shift": kCGEventFlagMaskShift,
}

ALL_KEYCODES = frozenset(KEYCODES)


def parse_key_combo(combo: str) -> tuple[int, int]:
    """Parse "cmd+shift+4"-style into (keycode, modifier_flags).

    Raises ValueError on unknown parts or a missing key.
    """
    parts = [p.strip().lower() for p in combo.split("+")]
    if not parts or any(p not in MODIFIERS and p not in KEYCODES for p in parts):
        raise ValueError(f"invalid key combo: {combo!r}")
    key_names = [p for p in parts if p in KEYCODES]
    if len(key_names) != 1:
        raise ValueError(f"key combo must contain exactly one key: {combo!r}")
    flags = 0
    for p in parts:
        if p in MODIFIERS:
            flags |= MODIFIERS[p]
    return KEYCODES[key_names[0]], flags