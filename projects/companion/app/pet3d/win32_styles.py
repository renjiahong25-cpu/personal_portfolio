"""Extended window styles for the pet window, expressed as pure data.

The Win32 calls live in `window_passthrough.py`; everything here is arithmetic so a test
can assert on the *intent* ("click-through uses a colour key, never WS_EX_TRANSPARENT")
without a desktop.

Sources: Microsoft Learn "Extended Window Styles" and "SetLayeredWindowAttributes":
  https://learn.microsoft.com/en-us/windows/win32/winmsg/extended-window-styles
  https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setlayeredwindowattributes

STATUS (live probe 2026-10-15): the running window is alpha-composed by Qt via
UpdateLayeredWindow, and the two APIs are mutually exclusive per the MSDN note -
turning LWA_COLORKEY on made every Qt repaint fail with
"UpdateLayeredWindowIndirect failed". The live pet therefore toggles
WS_EX_TRANSPARENT for the whole-rectangle click-through mode and clears it in
interactive mode (see window_passthrough.set_click_through_via_style); these
LWA attributes stay here as the tested alternative for a future non-Qt surface,
and the forbidden-style contract docs/03 card 1 demands is unit-tested against
them.
"""
from __future__ import annotations

GWL_EXSTYLE = -20

# Extended styles. Values from winuser.h.
WS_EX_TRANSPARENT = 0x00000020  # paint-order transparency. NOT click-through.
WS_EX_LAYERED = 0x00080000  # layered window; the prerequisite for the colour key.
WS_EX_TOOLWINDOW = 0x00000080  # no taskbar button, no Alt+Tab entry.
WS_EX_TOPMOST = 0x00000008  # above non-topmost windows even when deactivated.
WS_EX_NOACTIVATE = 0x08000000  # clicking the window does not steal foreground.
WS_EX_APPWINDOW = 0x00040000  # forces a taskbar button. Forbidden by docs/03 card 1.

# SetLayeredWindowAttributes flags.
LWA_COLORKEY = 0x00000001
LWA_ALPHA = 0x00000002

# CLR_NONE disables the colour key entirely.
CLR_NONE = 0xFFFFFFFF

# The pet renders on a transparent surface, so fully cleared pixels are (0, 0, 0, 0)
# premultiplied. Keying on pure black therefore punches holes exactly where nothing was
# drawn, and leaves anti-aliased edges (non-zero RGB, partial alpha) untouched.
PASSTHROUGH_COLOR_KEY = 0x000000  # COLORREF is 0x00BBGGRR.
PASSTHROUGH_ALPHA = 255

FORBIDDEN_STYLES = {
    "WS_EX_APPWINDOW": WS_EX_APPWINDOW,
    "WS_EX_TRANSPARENT": WS_EX_TRANSPARENT,
}


def base_style() -> int:
    """Styles the pet always has, in both interactive and click-through mode."""
    return (
        WS_EX_LAYERED
        | WS_EX_TOOLWINDOW
        | WS_EX_TOPMOST
        | WS_EX_NOACTIVATE
    )


def layered_attributes(click_through: bool) -> tuple[int, int, int]:
    """(dwFlags, crKey, bAlpha) for SetLayeredWindowAttributes.

    Click-through mode enables LWA_COLORKEY, which is what makes transparent pixels
    forward mouse input to the window below. Interactive mode passes LWA_ALPHA only, so
    no pixel can ever be keyed out and the pet stays fully clickable.
    """
    if click_through:
        return LWA_COLORKEY, PASSTHROUGH_COLOR_KEY, PASSTHROUGH_ALPHA
    return LWA_ALPHA, CLR_NONE, PASSTHROUGH_ALPHA


def describe(style_bits: int) -> dict[str, bool]:
    """Human-readable decode of an ex-style value, used by tests and diagnostics."""
    named = {
        "WS_EX_TRANSPARENT": WS_EX_TRANSPARENT,
        "WS_EX_LAYERED": WS_EX_LAYERED,
        "WS_EX_TOOLWINDOW": WS_EX_TOOLWINDOW,
        "WS_EX_TOPMOST": WS_EX_TOPMOST,
        "WS_EX_NOACTIVATE": WS_EX_NOACTIVATE,
        "WS_EX_APPWINDOW": WS_EX_APPWINDOW,
    }
    return {name: bool(style_bits & bit) for name, bit in named.items()}


def violations(style_bits: int) -> list[str]:
    """Styles present that docs/03 card 1 explicitly forbids."""
    return [name for name, bit in FORBIDDEN_STYLES.items() if style_bits & bit]


def make_color_ref(rgb: tuple[int, int, int]) -> int:
    """(r, g, b) -> COLORREF (0x00BBGGRR). Used by tests and the pixel probe."""
    r, g, b = (max(0, min(255, int(c))) for c in rgb)
    return (b << 16) | (g << 8) | r


def split_color_ref(value: int) -> tuple[int, int, int]:
    return value & 0xFF, (value >> 8) & 0xFF, (value >> 16) & 0xFF