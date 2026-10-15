"""A-level checks for the click-through mechanism (docs/03 card 1, V5/V6).

Two layers are tested:
  * `win32_styles` - the pure intent: colour key on, WS_EX_TRANSPARENT never,
    WS_EX_APPWINDOW never.
  * `window_passthrough` - the Win32 boundary, driven through a recording stand-in
    so the exact SetLayeredWindowAttributes / RegisterHotKey arguments are asserted
    without a desktop session.
  * `_HotkeyFilter` - that a WM_HOTKEY message actually invokes the callbacks
    (the V6 recovery path; it previously crashed because a non-QObject cannot own
    a Signal).
"""
import ctypes
from ctypes import wintypes

import pytest
from PySide6.QtCore import QCoreApplication

from app.pet3d import win32_styles as ws
from app.pet3d import window_passthrough as wp


def _plain(value):
    """ctypes scalars (c_void_p / COLORREF / BYTE) -> python int."""
    return int(value.value if hasattr(value, "value") else value)


class FakeUser32:
    """Records the calls window_passthrough makes; returns scripted truthy values."""

    def __init__(self, ex_style=0):
        self.calls = []
        self._ex_style = ex_style

    def GetWindowLongPtrW(self, hwnd, index):
        self.calls.append(("GetWindowLongPtrW", _plain(hwnd), index))
        return self._ex_style

    def SetWindowLongPtrW(self, hwnd, index, bits):
        self.calls.append(("SetWindowLongPtrW", _plain(hwnd), index, _plain(bits) & 0xFFFFFFFF))
        self._ex_style = _plain(bits) & 0xFFFFFFFF
        return 1

    def SetLayeredWindowAttributes(self, hwnd, color, alpha, flags):
        self.calls.append(
            ("SetLayeredWindowAttributes", _plain(hwnd), _plain(color), _plain(alpha), _plain(flags))
        )
        return 1

    def RegisterHotKey(self, hwnd, hotkey_id, mods, vk):
        self.calls.append(("RegisterHotKey", _plain(hwnd), hotkey_id, mods, vk))
        return 1

    def UnregisterHotKey(self, hwnd, hotkey_id):
        self.calls.append(("UnregisterHotKey", _plain(hwnd), hotkey_id))
        return 1

    def SetWindowPos(self, hwnd, insert, x, y, cx, cy, flags):
        self.calls.append(("SetWindowPos", _plain(hwnd), _plain(flags)))
        return 1

    def GetWindowRect(self, hwnd, rect_ref):
        # byref(...) exposes the target through the (read-only) _obj attribute;
        # memmove the values in exactly like the real API would.
        target = rect_ref._obj
        ctypes.memmove(
            ctypes.addressof(target),
            ctypes.byref(wintypes.RECT(7, 11, 327, 391)),
            ctypes.sizeof(target),
        )
        self.calls.append(("GetWindowRect", _plain(hwnd)))
        return 1

    def names(self):
        return [call[0] for call in self.calls]


# ------------------------------------------------------------- pure style intent


def test_base_style_has_the_four_always_on_bits_and_never_the_forbidden_ones():
    bits = ws.base_style()
    decoded = ws.describe(bits)
    assert decoded["WS_EX_LAYERED"] and decoded["WS_EX_TOOLWINDOW"]
    assert decoded["WS_EX_TOPMOST"] and decoded["WS_EX_NOACTIVATE"]
    assert not decoded["WS_EX_TRANSPARENT"], "docs/03 3.3: paint-order transparency is not click-through"
    assert not decoded["WS_EX_APPWINDOW"], "docs/03 card 1 forbids forcing a taskbar button"


def test_click_through_uses_colour_key_and_interactive_uses_alpha_only():
    on = ws.layered_attributes(True)
    off = ws.layered_attributes(False)
    assert on[0] == ws.LWA_COLORKEY and on[1] == ws.PASSTHROUGH_COLOR_KEY
    assert off[0] == ws.LWA_ALPHA and off[1] == ws.CLR_NONE, "interactive mode must never key pixels out"


def test_violations_detects_each_forbidden_bit():
    assert ws.violations(ws.WS_EX_TRANSPARENT) == ["WS_EX_TRANSPARENT"]
    assert ws.violations(ws.WS_EX_APPWINDOW) == ["WS_EX_APPWINDOW"]
    assert set(ws.violations(ws.base_style() | ws.WS_EX_TRANSPARENT | ws.WS_EX_APPWINDOW)) == {
        "WS_EX_APPWINDOW",
        "WS_EX_TRANSPARENT",
    }
    assert ws.violations(ws.base_style()) == []


def test_color_ref_is_blue_green_red():
    assert ws.make_color_ref((255, 0, 0)) == 0x0000FF
    assert ws.split_color_ref(ws.make_color_ref((1, 2, 3))) == (1, 2, 3)


# ------------------------------------------------------------- Win32 boundary


def test_apply_base_style_strips_forbidden_bits_and_or_base():
    fake = FakeUser32(ex_style=ws.WS_EX_APPWINDOW | ws.WS_EX_TRANSPARENT | 0x00100000)
    assert wp.apply_base_style(42, fake) is True
    applied = [c for c in fake.calls if c[0] == "SetWindowLongPtrW"][0][3]
    assert ws.violations(applied) == []
    assert applied & ws.WS_EX_LAYERED and applied & ws.WS_EX_NOACTIVATE
    assert applied & 0x00100000, "unrelated styles must survive"


def test_set_click_through_calls_layered_attributes_with_expected_arguments():
    fake = FakeUser32()
    assert wp.set_click_through(7, True, fake) is True
    call = [c for c in fake.calls if c[0] == "SetLayeredWindowAttributes"][-1]
    assert call[1:] == (7, ws.PASSTHROUGH_COLOR_KEY, ws.PASSTHROUGH_ALPHA, ws.LWA_COLORKEY)
    wp.set_click_through(7, False, fake)
    call = [c for c in fake.calls if c[0] == "SetLayeredWindowAttributes"][-1]
    assert call[1:] == (7, ws.CLR_NONE, ws.PASSTHROUGH_ALPHA, ws.LWA_ALPHA)
    joined = " ".join(c[0] for c in fake.calls)
    assert "SetWindowLongPtrW" not in joined, "click-through must not be implemented via styles"


def test_hotkey_registration_uses_parsed_combos():
    fake = FakeUser32()
    mods, vk = wp.parse_hotkey("ctrl+alt+space")
    assert wp.register_hotkey(5, wp.HOTKEY_ID, mods, vk, fake) is True
    assert ("RegisterHotKey", 5, wp.HOTKEY_ID, mods, vk) in fake.calls
    assert wp.unregister_hotkey(5, wp.HOTKEY_ID, fake) is True
    assert ("UnregisterHotKey", 5, wp.HOTKEY_ID) in fake.calls


def test_reassert_topmost_moves_without_activating():
    fake = FakeUser32()
    assert wp.reassert_topmost(9, fake) is True
    call = [c for c in fake.calls if c[0] == "SetWindowPos"][0]
    assert call[2] & wp.SWP_NOMOVE and call[2] & wp.SWP_NOSIZE and call[2] & wp.SWP_NOACTIVATE


def test_click_through_via_style_sets_and_clears_only_the_transparent_bit():
    # Live mechanism (2026-10-15 probe): interactive keeps WS_EX_TRANSPARENT off;
    # click-through turns it on over an already-layered window; other bits survive.
    fake = FakeUser32(ex_style=ws.base_style())
    assert wp.set_click_through_via_style(8, True, fake) is True
    after = [c for c in fake.calls if c[0] == "SetWindowLongPtrW"][-1][3]
    assert after & ws.WS_EX_TRANSPARENT and after & ws.WS_EX_LAYERED
    assert after & ws.WS_EX_APPWINDOW == 0
    assert wp.set_click_through_via_style(8, False, fake) is True
    after = [c for c in fake.calls if c[0] == "SetWindowLongPtrW"][-1][3]
    assert not after & ws.WS_EX_TRANSPARENT
    assert after & ws.WS_EX_LAYERED and after & ws.WS_EX_TOOLWINDOW and after & ws.WS_EX_TOPMOST


def test_apply_base_style_keeps_the_interactive_promise():
    # Re-applying base styles (show runs it twice) must return an interactive window
    # even if it previously had the click-through bit - and never resurrect LWA.
    fake = FakeUser32(ex_style=ws.base_style() | ws.WS_EX_TRANSPARENT)
    assert wp.apply_base_style(2, fake) is True
    applied = [c for c in fake.calls if c[0] == "SetWindowLongPtrW"][-1][3]
    assert ws.violations(applied) == []


def test_parse_hotkey_accepts_defaults_and_rejects_junk():
    assert wp.parse_hotkey("ctrl+alt+space") == (wp.MOD_CONTROL | wp.MOD_ALT | wp.MOD_NOREPEAT, wp.VK_SPACE)
    assert wp.parse_hotkey("Ctrl+Alt+Shift+Q") == (
        wp.MOD_CONTROL | wp.MOD_ALT | wp.MOD_SHIFT | wp.MOD_NOREPEAT,
        wp.VK_Q,
    )
    with pytest.raises(ValueError):
        wp.parse_hotkey("ctrl+alt")       # modifiers but no key
    with pytest.raises(ValueError):
        wp.parse_hotkey("ctrl+alt+banana")  # unknown key


def test_window_rect_reports_physical_geometry():
    fake = FakeUser32()
    assert wp.window_rect(3, fake) == (7, 11, 320, 380)


# ------------------------------------------------------------- hotkey -> callback


def _msg_pointer(message_id, wparam):
    msg = wintypes.MSG(0, message_id, wparam, 0)
    return ctypes.addressof(msg), msg  # keep the backing store alive


def test_hotkey_filter_invokes_callbacks_and_ignores_other_messages():
    QCoreApplication.instance() or QCoreApplication([])
    from app.pet3d.window import _HotkeyFilter

    filter_ = _HotkeyFilter()
    hits = []
    filter_.on_toggle = lambda: hits.append("toggle")
    filter_.on_quit = lambda: hits.append("quit")

    addr, keep1 = _msg_pointer(wp.WM_HOTKEY, 1)
    assert filter_.nativeEventFilter(b"windows_generic_MSG", addr) is True
    addr, keep2 = _msg_pointer(wp.WM_HOTKEY, 2)
    assert filter_.nativeEventFilter(b"windows_generic_MSG", addr) is True
    addr, keep3 = _msg_pointer(wp.WM_HOTKEY, 99)
    filter_.nativeEventFilter(b"windows_generic_MSG", addr)
    addr, keep4 = _msg_pointer(0x000F, 1)  # WM_SETFOCUS: not ours
    assert filter_.nativeEventFilter(b"windows_generic_MSG", addr) is False
    assert filter_.nativeEventFilter(b"linux_generic_MSG", addr) is False
    assert hits == ["toggle", "quit"]
