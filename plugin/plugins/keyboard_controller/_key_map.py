"""Key-name to VK code mapping and combo parsing for keyboard_controller."""

from __future__ import annotations

import string
from typing import Iterable

# --- Virtual-key codes (Windows) ----------------------------------------------
VK_BACK = 0x08
VK_TAB = 0x09
VK_RETURN = 0x0D
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_PAUSE = 0x13
VK_CAPITAL = 0x14
VK_ESCAPE = 0x1B
VK_SPACE = 0x20
VK_PRIOR = 0x21
VK_NEXT = 0x22
VK_END = 0x23
VK_HOME = 0x24
VK_LEFT = 0x25
VK_UP = 0x26
VK_RIGHT = 0x27
VK_DOWN = 0x28
VK_SNAPSHOT = 0x2C
VK_INSERT = 0x2D
VK_DELETE = 0x2E
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_NUMPAD0 = 0x60
VK_NUMPAD9 = 0x69
VK_MULTIPLY = 0x6A
VK_ADD = 0x6B
VK_SEPARATOR = 0x6C
VK_SUBTRACT = 0x6D
VK_DECIMAL = 0x6E
VK_DIVIDE = 0x6F
VK_F1 = 0x70
VK_F24 = 0x87
VK_NUMLOCK = 0x90
VK_SCROLL = 0x91
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LMENU = 0xA4
VK_RMENU = 0xA5
VK_OEM_1 = 0xBA  # ;:
VK_OEM_PLUS = 0xBB  # =+
VK_OEM_COMMA = 0xBC  # ,<
VK_OEM_MINUS = 0xBD  # -_
VK_OEM_PERIOD = 0xBE  # .>
VK_OEM_2 = 0xBF  # /?
VK_OEM_3 = 0xC0  # `~
VK_OEM_4 = 0xDB  # [{
VK_OEM_5 = 0xDC  # \|
VK_OEM_6 = 0xDD  # ]}
VK_OEM_7 = 0xDE  # '"

# --- Extended keys (need KEYEVENTF_EXTENDEDKEY with scan codes) --------------
EXTENDED_KEYS: frozenset[int] = frozenset({
    VK_LEFT, VK_UP, VK_RIGHT, VK_DOWN,
    VK_PRIOR, VK_NEXT, VK_END, VK_HOME,
    VK_INSERT, VK_DELETE,
    VK_RCONTROL, VK_RMENU, VK_RWIN,
    VK_DIVIDE,
})

# --- Modifier keys -------------------------------------------------------------
MODIFIER_VKS: frozenset[int] = frozenset({
    VK_SHIFT, VK_CONTROL, VK_MENU,
    VK_LSHIFT, VK_RSHIFT, VK_LCONTROL, VK_RCONTROL,
    VK_LMENU, VK_RMENU, VK_LWIN, VK_RWIN,
})


def _build_aliases() -> dict[str, int]:
    aliases: dict[str, int] = {}

    for ch in string.ascii_lowercase:
        aliases[ch] = ord(ch.upper())
    for digit in string.digits:
        aliases[digit] = ord(digit)

    for index in range(1, 25):
        aliases[f"f{index}"] = VK_F1 + index - 1

    for index in range(10):
        aliases[f"numpad{index}"] = VK_NUMPAD0 + index
        aliases[f"num{index}"] = VK_NUMPAD0 + index

    aliases.update({
        "back": VK_BACK,
        "backspace": VK_BACK,
        "bs": VK_BACK,
        "tab": VK_TAB,
        "enter": VK_RETURN,
        "return": VK_RETURN,
        "shift": VK_SHIFT,
        "lshift": VK_LSHIFT,
        "rshift": VK_RSHIFT,
        "ctrl": VK_CONTROL,
        "control": VK_CONTROL,
        "lctrl": VK_LCONTROL,
        "lcontrol": VK_LCONTROL,
        "rctrl": VK_RCONTROL,
        "rcontrol": VK_RCONTROL,
        "alt": VK_MENU,
        "menu": VK_MENU,
        "lalt": VK_LMENU,
        "ralt": VK_RMENU,
        "pause": VK_PAUSE,
        "break": VK_PAUSE,
        "capslock": VK_CAPITAL,
        "esc": VK_ESCAPE,
        "escape": VK_ESCAPE,
        "space": VK_SPACE,
        "spacebar": VK_SPACE,
        "pgup": VK_PRIOR,
        "pageup": VK_PRIOR,
        "pgdn": VK_NEXT,
        "pagedown": VK_NEXT,
        "end": VK_END,
        "home": VK_HOME,
        "left": VK_LEFT,
        "up": VK_UP,
        "right": VK_RIGHT,
        "down": VK_DOWN,
        "printscreen": VK_SNAPSHOT,
        "prtsc": VK_SNAPSHOT,
        "insert": VK_INSERT,
        "ins": VK_INSERT,
        "delete": VK_DELETE,
        "del": VK_DELETE,
        "win": VK_LWIN,
        "windows": VK_LWIN,
        "lwin": VK_LWIN,
        "rwin": VK_RWIN,
        "cmd": VK_LWIN,
        "numlock": VK_NUMLOCK,
        "scrolllock": VK_SCROLL,
        "numlock_on": VK_NUMLOCK,
        "multiply": VK_MULTIPLY,
        "numpad_multiply": VK_MULTIPLY,
        "num*": VK_MULTIPLY,
        "add": VK_ADD,
        "numpad_add": VK_ADD,
        "num+": VK_ADD,
        "separator": VK_SEPARATOR,
        "subtract": VK_SUBTRACT,
        "numpad_subtract": VK_SUBTRACT,
        "num-": VK_SUBTRACT,
        "decimal": VK_DECIMAL,
        "numpad_decimal": VK_DECIMAL,
        "num.": VK_DECIMAL,
        "divide": VK_DIVIDE,
        "numpad_divide": VK_DIVIDE,
        "num/": VK_DIVIDE,
        "semicolon": VK_OEM_1,
        ";": VK_OEM_1,
        "plus": VK_OEM_PLUS,
        "=": VK_OEM_PLUS,
        "comma": VK_OEM_COMMA,
        ",": VK_OEM_COMMA,
        "minus": VK_OEM_MINUS,
        "-": VK_OEM_MINUS,
        "period": VK_OEM_PERIOD,
        ".": VK_OEM_PERIOD,
        "slash": VK_OEM_2,
        "/": VK_OEM_2,
        "backquote": VK_OEM_3,
        "`": VK_OEM_3,
        "lbracket": VK_OEM_4,
        "[": VK_OEM_4,
        "backslash": VK_OEM_5,
        "\\": VK_OEM_5,
        "rbracket": VK_OEM_6,
        "]": VK_OEM_6,
        "quote": VK_OEM_7,
        "'": VK_OEM_7,
    })
    return aliases


KEY_ALIASES: dict[str, int] = _build_aliases()

_SUPPORTED_NAMES: tuple[str, ...] = tuple(sorted(KEY_ALIASES.keys()))


def supported_key_names() -> list[str]:
    return list(_SUPPORTED_NAMES)


def lookup_vk(name: str) -> int | None:
    if not isinstance(name, str):
        return None
    normalized = name.strip().lower()
    if not normalized:
        return None
    return KEY_ALIASES.get(normalized)


class KeySpecError(ValueError):
    """Raised when a key/combo spec cannot be parsed."""


def parse_combo(spec: str) -> tuple[list[int], int]:
    """Parse ``"ctrl+shift+c"`` or ``"space"`` into (modifiers, main_vk).

    Returns ``([], main_vk)`` for a plain key. ``modifiers`` preserves
    press order; callers should release them in reverse.
    """
    if not isinstance(spec, str) or not spec.strip():
        raise KeySpecError("empty key spec")
    parts = [part.strip() for part in spec.split("+") if part.strip()]
    if not parts:
        raise KeySpecError(f"invalid key spec: {spec!r}")
    if len(parts) == 1:
        vk = lookup_vk(parts[0])
        if vk is None:
            raise KeySpecError(f"unknown key: {parts[0]!r}")
        if vk in MODIFIER_VKS:
            raise KeySpecError(f"bare modifier is not allowed: {parts[0]!r}")
        return [], vk

    modifiers: list[int] = []
    main_vk: int | None = None
    for part in parts:
        vk = lookup_vk(part)
        if vk is None:
            raise KeySpecError(f"unknown key in combo: {part!r}")
        if vk in MODIFIER_VKS:
            if vk not in modifiers:
                modifiers.append(vk)
        else:
            if main_vk is not None:
                raise KeySpecError(f"multiple non-modifier keys in combo: {spec!r}")
            main_vk = vk
    if main_vk is None:
        raise KeySpecError(f"combo has only modifiers: {spec!r}")
    return modifiers, main_vk


def parse_keys_list(specs: Iterable[object]) -> list[tuple[list[int], int]]:
    """Parse a list of combos; each item may be a string or ``[vk, vk, ...]``."""
    result: list[tuple[list[int], int]] = []
    for item in specs:
        if isinstance(item, str):
            result.append(parse_combo(item))
        elif isinstance(item, (list, tuple)) and len(item) >= 1 and all(isinstance(v, int) for v in item):
            vks = [int(v) for v in item]
            modifiers = [v for v in vks if v in MODIFIER_VKS]
            mains = [v for v in vks if v not in MODIFIER_VKS]
            if not mains:
                raise KeySpecError(f"key list has no non-modifier: {item!r}")
            if len(mains) > 1:
                raise KeySpecError(f"key list has multiple non-modifiers: {item!r}")
            result.append((modifiers, mains[0]))
        else:
            raise KeySpecError(f"unsupported key spec item: {item!r}")
    return result
