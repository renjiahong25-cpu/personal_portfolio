"""C03 - frameless VRM desktop pet.

Selection: option A2 from docs/03 section 3.7 - the existing PySide6 window is kept and
hosts a three.js + @pixiv/three-vrm WebGL renderer through QWebEngineView.

Module map:
    geometry.py       pure multi-monitor / DPI / passthrough math (no Qt, no Win32)
    win32_styles.py    extended window style bits as pure data (no Win32)
    window_passthrough.py  Win32 calls: LWA_COLORKEY, hotkey, hit-test probe
    vrm_loader.py      VRM 1.0 / 0.x detection, model registry and licence bookkeeping
    assets_server.py   loopback-only static host for the built web bundle and the VRM file
    renderer.py        QWebEngineView container + JSON bridge
    fallback2d.py      the pre-existing 2D pet repainted, used when WebGL or the VRM fails
    tray.py            tray icon: the only permanent UI, carries menu and exit
    window.py          the frameless, topmost, no-taskbar pet window itself
"""

__all__ = [
    "geometry",
    "win32_styles",
    "window_passthrough",
    "vrm_loader",
    "assets_server",
    "renderer",
    "fallback2d",
    "tray",
    "window",
]