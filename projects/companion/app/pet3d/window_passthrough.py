"""Win32 boundary: colour-key click-through, global hotkey registration, hit-test probe.

Everything that actually talks to `user32.dll` is here. The functions take an optional
`user32` argument so tests can pass a recording double and assert on the calls without a
desktop session. The real module-level API binds a lazily created real `user32`.

Click-through mechanism (docs/03 section 3.3):
  WS_EX_LAYERED + SetLayeredWindowAttributes(LWA_COLORKEY, crKey=black).
  WS_EX_TRANSPARENT is deliberately never set - it only reorders painting.
  Live-probe caveat: on the Qt WebEngine translucent window this conflicts with
  Qt's UpdateLayeredWindow surface (see win32_styles module docstring); the live
  window uses per-pixel alpha instead. Kept here as the tested alternative path.
"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

from . import win32_styles as ws

HOTKEY_ID = 0xC03

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312

VK_SPACE = 0x20
VK_P = 0x50
VK_Q = 0x51

DEFAULT_TOGGLE_COMBO = (MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_SPACE)
DEFAULT_QUIT_COMBO = (MOD_CONTROL | MOD_ALT | MOD_SHIFT | MOD_NOREPEAT, VK_Q)

# Virtual keys recognised without needing a keyboard layout hook.
VK_NAMES = {"space": 0x20, "p": 0x50, "q": 0x51, "f1": 0x70, "f2": 0x71, "f12": 0x7B}


def parse_hotkey(spec: str) -> tuple[int, int]:
    """'ctrl+alt+space' -> (modifiers, virtual key). Raises ValueError on anything else."""
    mods = 0
    key = None
    for raw in spec.split("+"):
        part = raw.strip().lower()
        if not part:
            continue
        if part in ("ctrl", "control"):
            mods |= MOD_CONTROL
        elif part == "alt":
            mods |= MOD_ALT
        elif part in ("shift",):
            mods |= MOD_SHIFT
        elif part in VK_NAMES:
            key = VK_NAMES[part]
        else:
            raise ValueError(f"unknown hotkey token {raw!r} in {spec!r}")
    if key is None:
        raise ValueError(f"hotkey {spec!r} has no key, only modifiers")
    return mods | MOD_NOREPEAT, key


def _bind_user32():
    if os.name != "nt":
        return None
    dll = ctypes.WinDLL("user32", use_last_error=True)
    dll.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    dll.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    dll.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    dll.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    dll.SetLayeredWindowAttributes.argtypes = [
        wintypes.HWND,
        wintypes.COLORREF,
        wintypes.BYTE,
        wintypes.DWORD,
    ]
    dll.SetLayeredWindowAttributes.restype = wintypes.BOOL
    dll.WindowFromPoint.argtypes = [wintypes.POINT]
    dll.WindowFromPoint.restype = wintypes.HWND
    dll.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    dll.RegisterHotKey.restype = wintypes.BOOL
    dll.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    dll.UnregisterHotKey.restype = wintypes.BOOL
    dll.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    dll.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT
    ]
    dll.SetWindowPos.restype = wintypes.BOOL
    dll.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    dll.GetWindowRect.restype = wintypes.BOOL
    return dll


HWND_TOPMOST = wintypes.HWND(-1)
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010


def window_rect(hwnd: int, dll=None) -> tuple[int, int, int, int] | None:
    """(left, top, width, height) in physical pixels."""
    dll = dll or user32()
    if dll is None or not hwnd:
        return None
    rect = wintypes.RECT()
    if not dll.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
        return None
    return int(rect.left), int(rect.top), int(rect.right - rect.left), int(rect.bottom - rect.top)


def reassert_topmost(hwnd: int, dll=None) -> bool:
    """Push the window back to the top without moving, resizing or activating it.

    docs/03 risk table: WS_EX_TOPMOST can be lost when another app takes focus. Rate
    limiting lives with the caller so this never fights a user drag.
    """
    dll = dll or user32()
    if dll is None or not hwnd:
        return False
    return bool(
        dll.SetWindowPos(
            wintypes.HWND(hwnd), HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
        )
    )


USER32 = None


def user32():
    """Lazily bind the real user32 so importing this module works on Linux/macOS too."""
    global USER32
    if USER32 is None:
        USER32 = _bind_user32()
    return USER32


# ---------------------------------------------------------------- style access


def get_ex_style(hwnd: int, dll=None) -> int:
    dll = dll or user32()
    if dll is None or not hwnd:
        return 0
    return int(dll.GetWindowLongPtrW(wintypes.HWND(hwnd), ws.GWL_EXSTYLE))


def set_ex_style(hwnd: int, bits: int, dll=None) -> bool:
    dll = dll or user32()
    if dll is None or not hwnd:
        return False
    return bool(dll.SetWindowLongPtrW(wintypes.HWND(hwnd), ws.GWL_EXSTYLE, ctypes.c_ssize_t(bits)))


def apply_base_style(hwnd: int, dll=None) -> bool:
    """Force the always-on styles and strip the two forbidden ones."""
    current = get_ex_style(hwnd, dll)
    stripped = current
    for bit in ws.FORBIDDEN_STYLES.values():
        stripped &= ~bit
    return set_ex_style(hwnd, stripped | ws.base_style(), dll)


def set_click_through(hwnd: int, enabled: bool, dll=None) -> bool:
    """Toggle per-pixel click-through via SetLayeredWindowAttributes.

    Returns True when Win32 accepted the call. Enabling keys out pure black; disabling
    passes LWA_ALPHA with CLR_NONE so nothing is keyed and the pet becomes clickable
    again over its whole rectangle.

    Live-probe note (2026-10-15): mutually exclusive with Qt's UpdateLayeredWindow
    painting on the WA_TranslucentBackground pet window - kept as the tested
    alternative surface, not the live mechanism. See set_click_through_via_style.
    """
    dll = dll or user32()
    if dll is None or not hwnd:
        return False
    flags, color, alpha = ws.layered_attributes(enabled)
    return bool(
        dll.SetLayeredWindowAttributes(wintypes.HWND(hwnd), wintypes.COLORREF(color), alpha, flags)
    )


def set_click_through_via_style(hwnd: int, enabled: bool, dll=None) -> bool:
    """The live click-through toggle: WS_EX_LAYERED + WS_EX_TRANSPARENT.

    Docs/03 §3.3 correctly warns that WS_EX_TRANSPARENT is *not* per-pixel
    transparency - and the live probe proved the per-pixel candidates do not work
    here either (LWA_COLORKEY kills Qt's UpdateLayeredWindow repaint; the Qt6 WebEngine
    native child surface keeps hit-testing every pixel regardless of alpha). So the
    shipped mechanism passes mouse input through the whole window rectangle while
    click-through is on, exactly matching the product rule in docs/03: the pet body
    need not be clickable in that mode and recovery is hotkey + tray (V6), never a
    click on the pet. Interactive mode always clears the bit again.
    """
    dll = dll or user32()
    if dll is None or not hwnd:
        return False
    style = get_ex_style(hwnd, dll)
    if enabled:
        style |= ws.WS_EX_LAYERED | ws.WS_EX_TRANSPARENT
    else:
        style &= ~ws.WS_EX_TRANSPARENT
    return set_ex_style(hwnd, style, dll)


def layered_state(hwnd: int, dll=None) -> tuple[int, int, int]:
    """Read back (crKey, bAlpha, dwFlags). Needs the BLENDFUNCTION shape from user32."""
    dll = dll or user32()
    if dll is None or not hwnd:
        return (ws.CLR_NONE, 0, 0)

    class BlendFunction(ctypes.Structure):
        _fields_ = [
            ("BlendOp", ctypes.c_ubyte),
            ("BlendFlags", ctypes.c_ubyte),
            ("SourceConstantAlpha", ctypes.c_ubyte),
            ("AlphaFormat", ctypes.c_ubyte),
        ]

    fn = BlendFunction()
    ok = getattr(dll, "GetLayeredWindowAttributes", None)
    if ok is None:
        return (ws.CLR_NONE, 0, 0)
    ok.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.COLORREF), ctypes.POINTER(wintypes.BYTE), ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(BlendFunction)]
    ok.restype = wintypes.BOOL
    color = wintypes.COLORREF()
    alpha = wintypes.BYTE()
    flags = wintypes.DWORD()
    if not ok(wintypes.HWND(hwnd), ctypes.byref(color), ctypes.byref(alpha), ctypes.byref(flags), ctypes.byref(fn)):
        return (ws.CLR_NONE, 0, 0)
    return int(color.value), int(alpha.value), int(flags.value)


# ---------------------------------------------------------------- hit testing


def window_from_point(x: int, y: int, dll=None) -> int:
    """The window Windows would deliver a click at this screen point to.

    This is the honest way to prove click-through: it asks the window manager, not the
    renderer. A keyed-out pixel must return some other window's handle (typically 0 when
    the point is over the desktop wallpaper), never the pet's handle.
    """
    dll = dll or user32()
    if dll is None:
        return 0
    return int(dll.WindowFromPoint(wintypes.POINT(int(x), int(y))) or 0)


def describe_hwnd(hwnd: int, dll=None) -> str:
    dll = dll or user32()
    if dll is None or not hwnd:
        return "<none>"
    buf = ctypes.create_unicode_buffer(256)
    dll.GetWindowTextW(wintypes.HWND(hwnd), buf, 256)
    return buf.value or f"<hwnd {hwnd}>"


# ---------------------------------------------------------------- monitors


class MonitorInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


MONITORINFOF_PRIMARY = 0x00000001


def _bind_monitors(dll):
    dll.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
    dll.GetMonitorInfoW.restype = wintypes.BOOL
    dll.EnumDisplayMonitors.argtypes = [
        wintypes.HWND,
        ctypes.c_void_p,
        ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM),
        wintypes.LPARAM,
    ]
    dll.EnumDisplayMonitors.restype = wintypes.BOOL


def _dpi_for_monitor(monitor, dll):
    """Effective scale factor, from the window DPI when available, else 96-dpi fallback.

    GetDpiForWindow is per-window; for a monitor handle we walk the top-level windows on
    it, and fall back to the process's own DPI. The result is only ever used as a ratio to
    96, so a conservative 1.0 fallback is safe.
    """
    try:
        get_dpi_for_window = dll.GetDpiForWindow
        get_dpi_for_window.argtypes = [wintypes.HWND]
        get_dpi_for_window.restype = wintypes.UINT

        callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def visit(hwnd, _):
            m = ctypes.c_void_p()
            dll.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
            dll.MonitorFromWindow.restype = wintypes.HANDLE
            m.value = dll.MonitorFromWindow(hwnd, 0)
            if m.value == monitor:
                dpi = get_dpi_for_window(hwnd)
                if dpi:
                    monitor_dpi["value"] = dpi
                    return False
            return True

        monitor_dpi = {"value": 0}
        dll.EnumWindows(callback(visit), 0)
        if monitor_dpi["value"]:
            return round(monitor_dpi["value"] / 96.0, 4)
    except (AttributeError, OSError, TypeError):
        pass
    try:
        dll.GetDpiForSystem.argtypes = []
        dll.GetDpiForSystem.restype = wintypes.UINT
        return round(float(dll.GetDpiForSystem()) / 96.0, 4)
    except (AttributeError, OSError):
        return 1.0


def enum_monitors(dll=None) -> list[dict]:
    """Every attached display in Win32 physical pixels plus its scale factor.

    Physical pixels are the only unambiguous frame of reference when several screens with
    different scaling are joined; Qt's virtual-desktop logical space is not.
    """
    dll = dll or user32()
    if dll is None:
        return []
    _bind_monitors(dll)
    found: list[dict] = []

    callback = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HANDLE, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM
    )

    def visit(handle, _hdc, _rect, _data):
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(info)
        if not dll.GetMonitorInfoW(handle, ctypes.byref(info)):
            return True
        rect = info.rcMonitor
        found.append(
            {
                "name": info.szDevice,
                "left": int(rect.left),
                "top": int(rect.top),
                "right": int(rect.right),
                "bottom": int(rect.bottom),
                "primary": bool(info.dwFlags & MONITORINFOF_PRIMARY),
                "scale": _dpi_for_monitor(ctypes.c_void_p(handle.value if hasattr(handle, "value") else handle), dll),
            }
        )
        return True

    dll.EnumDisplayMonitors(None, None, callback(visit), 0)
    return found


# ---------------------------------------------------------------- hotkeys


def register_hotkey(hwnd: int, hotkey_id: int, modifiers: int, virtual_key: int, dll=None) -> bool:
    dll = dll or user32()
    if dll is None or not hwnd:
        return False
    return bool(dll.RegisterHotKey(wintypes.HWND(hwnd), hotkey_id, modifiers, virtual_key))


def unregister_hotkey(hwnd: int, hotkey_id: int, dll=None) -> bool:
    dll = dll or user32()
    if dll is None or not hwnd:
        return False
    return bool(dll.UnregisterHotKey(wintypes.HWND(hwnd), hotkey_id))