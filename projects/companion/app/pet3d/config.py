"""C03 pet configuration. `pet.json` next to the project root overrides the defaults."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

DEFAULTS = {
    "model": None,  # None -> vrm_loader.DEFAULT_MODEL
    "window": {"width": 320, "height": 380, "mode": "normal"},
    "passthrough": {"enabled": False, "start_click_through": False},
    "hotkeys": {"toggle_passthrough": "ctrl+alt+space", "quit": "ctrl+alt+shift+q"},
    "view": {"look_at_mouse": True, "idle_animation": True, "click_reaction": True},
    "bubble": {"enabled": True},
    "tray": {"enabled": True},
}


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


@dataclass(frozen=True)
class PetConfig:
    model: str | None = None
    width: int = 320
    height: int = 380
    window_mode: str = "normal"  # "normal" | "fullscreen"
    click_through: bool = False
    start_click_through: bool = False
    toggle_hotkey: str = "ctrl+alt+space"
    quit_hotkey: str = "ctrl+alt+shift+q"
    look_at_mouse: bool = True
    idle_animation: bool = True
    click_reaction: bool = True
    bubble: bool = True
    tray: bool = True
    raw: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | str | None = None, project_root: Path | str | None = None) -> "PetConfig":
        root = Path(project_root) if project_root else Path(__file__).resolve().parent.parent.parent
        data = dict(DEFAULTS)
        target = Path(path) if path else root / "pet.json"
        if target.exists():
            try:
                data = _merge(data, json.loads(target.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                pass  # A broken optional config must not stop the pet from starting.
        window = data["window"]
        return cls(
            model=data["model"],
            width=max(160, min(1920, int(window["width"]))),
            height=max(160, min(1920, int(window["height"]))),
            window_mode=str(window.get("mode", "normal")),
            click_through=bool(data["passthrough"]["enabled"]),
            start_click_through=bool(data["passthrough"]["start_click_through"]),
            toggle_hotkey=str(data["hotkeys"]["toggle_passthrough"]),
            quit_hotkey=str(data["hotkeys"]["quit"]),
            look_at_mouse=bool(data["view"]["look_at_mouse"]),
            idle_animation=bool(data["view"]["idle_animation"]),
            click_reaction=bool(data["view"]["click_reaction"]),
            bubble=bool(data["bubble"]["enabled"]),
            tray=bool(data["tray"]["enabled"]),
            raw=data,
        )

    @property
    def logical_size(self) -> tuple[int, int]:
        return self.width, self.height

    def describe(self) -> list[str]:
        return [
            f"模型 {self.model or '（默认 VRM1_Constraint_Twist_Sample.vrm）'}",
            f"窗口 {self.width}x{self.height} 逻辑像素 / 模式 {self.window_mode}",
            f"初始穿透 {'开' if self.start_click_through else '关'}",
            f"热键 穿透={self.toggle_hotkey} 退出={self.quit_hotkey}",
            f"视线跟随 {'开' if self.look_at_mouse else '关'} · 气泡 {'开' if self.bubble else '关'} · 托盘 {'开' if self.tray else '关'}",
        ]