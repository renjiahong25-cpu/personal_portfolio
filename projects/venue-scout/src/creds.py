"""Account-login requirement awareness.

Some operations only work (or work better) with a logged-in user cookie stored
in config/settings.yaml.  This module is the single source of truth describing
which operations need an account, so all three layers prompt consistently:

  * program  — structured status dicts (used in collect result / API responses)
  * terminal — ``[ACCOUNT]`` console lines for long-running jobs
  * frontend — ``/api/creds/status`` payload rendered as an amber banner

Presence detection only: a cookie string is ``configured`` when non-empty.
We do not validate freshness here — that is reported by the failing op itself.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

_COOKIE_GETTERS: dict[str, Callable[[dict], str]] = {
    "ctrip": lambda cfg: ((cfg.get("ctrip") or {}).get("cookie") or "").strip(),
    "xiaohongshu": lambda cfg: ((cfg.get("xiaohongshu") or {}).get("cookie") or "").strip(),
}

ACCOUNT_OPERATIONS: list[dict] = [
    {
        "id": "ctrip",
        "label": "携程设施／会议抓取",
        "level": "enhance",  # can degrade, login raises success rate
        "missing": "未配置携程登录 cookie：设施／会议抓取降级，会议解析与明细成功率降低",
    },
    {
        "id": "xiaohongshu",
        "label": "小红书候选发现",
        "level": "required",  # no cookie -> no candidates at all
        "missing": "未配置小红书登录 cookie：候选发现在采集中被跳过，会漏掉小红书来源候选",
    },
]


def load_settings() -> dict:
    p = ROOT / "config" / "settings.yaml"
    if not p.exists():
        p = ROOT / "config" / "settings.example.yaml"
    if not p.exists():
        return {}
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def account_status(cfg: dict | None = None) -> list[dict]:
    """Per-operation status. ``configured`` False means "needs a login first"."""
    cfg = cfg if cfg is not None else load_settings()
    out: list[dict] = []
    for op in ACCOUNT_OPERATIONS:
        cookie = _COOKIE_GETTERS[op["id"]](cfg)
        out.append(
            {
                "id": op["id"],
                "label": op["label"],
                "level": op["level"],
                "configured": bool(cookie),
                "missing": op["missing"],
            }
        )
    return out


def ops_needing_login(cfg: dict | None = None) -> list[dict]:
    return [s for s in account_status(cfg) if not s["configured"]]


def terminal_lines(cfg: dict | None = None) -> list[str]:
    """``[ACCOUNT]`` console lines for the unconfigured login-required ops."""
    lines: list[str] = []
    for st in ops_needing_login(cfg):
        prefix = "⚠" if st["level"] == "required" else "·"
        lines.append(f"[ACCOUNT] {prefix} 需登录账号的操作「{st['label']}」：{st['missing']}")
    if lines:
        lines.append(
            "[ACCOUNT] 登录方式：前端横幅点「一键获取 Cookie」，或在 config/settings.yaml 填入 cookie 后重启服务"
        )
    return lines


# ------- cookie validity -------

MARKER_NAMES = {
    "ctrip": ("cticket", "login_uid"),
    "xiaohongshu": ("web_session",),
}

_PROBE_URLS = {
    "ctrip": "https://hotels.ctrip.com/hotels/513040.html",
    "xiaohongshu": "https://www.xiaohongshu.com/search_result?keyword=test",
}


def probe(site: str, cookie: str = "", timeout: float = 12.0) -> str:
    """Cheap validity probe for a stored cookie header.

    Returns one of:
      * ``missing``     — no cookie configured at all
      * ``no_marker``   — cookie text exists but has no login marker (expired/broke)
      * ``valid``       — marker present and a real request succeeds
      * ``unreachable`` — marker present but the probe request failed (network/login wall)
    """
    if not cookie:
        return "missing"
    markers = MARKER_NAMES.get(site, ())
    if markers and not any(m in cookie for m in markers):
        return "no_marker"
    url = _PROBE_URLS.get(site)
    if not url:
        return "valid"
    try:
        import httpx

        r = httpx.get(
            url,
            headers={
                "Cookie": cookie,
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                ),
            },
            timeout=timeout,
            follow_redirects=True,
        )
        if r.status_code == 200:
            return "valid"
        return "unreachable"
    except Exception:
        return "unreachable"