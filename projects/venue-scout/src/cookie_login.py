"""One-click cookie login: open a browser window, let the user sign in, then
harvest the site cookies and write them to config/settings.yaml.

Once the user has signed in inside the dedicated persistent profile
(``data/browser-profile/{site}``) their session survives across runs:
the next call reuses the profile, detects the login marker and writes the
cookie out without asking the user to sign in again.

Usage from CLI (diagnostics):
    python -m src.cookie_login --site ctrip            # no write, report only
    python -m src.cookie_login --site xiaohongshu --write
"""

from __future__ import annotations

import json
import shutil
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

PROFILE_DIR = ROOT / "data" / "browser-profile"
SETTINGS = ROOT / "config" / "settings.yaml"

LOGIN_PAGE_CTRIP = "https://hotels.ctrip.com/"
LOGIN_PAGE_XHS = "https://www.xiaohongshu.com/"

SITES: dict[str, dict] = {
    "ctrip": {
        "label": "携程",
        "yaml_section": "ctrip",
        # a login marker cookie name that only exists after sign-in
        "markers": ("cticket", "login_uid"),
        "domains": ("hotels.ctrip.com", "m.ctrip.com", "passport.ctrip.com", "ctrip.com"),
        "login_url": LOGIN_PAGE_CTRIP,
    },
    "xiaohongshu": {
        "label": "小红书",
        "yaml_section": "xiaohongshu",
        "markers": ("web_session",),
        "domains": ("www.xiaohongshu.com", "xiaohongshu.com"),
        "login_url": LOGIN_PAGE_XHS,
    },
}

Phase = Callable[[str, str], None]


def _noop(phase: str, message: str) -> None:
    pass


def site_cookie_names(site: str) -> tuple[str, ...]:
    return SITES[site]["markers"]


def has_marker(name_pairs: list[dict] | dict, site: str) -> bool:
    markers = site_cookie_names(site)
    if isinstance(name_pairs, dict):
        names = set(name_pairs)
    else:
        names = {c.get("name", "") for c in name_pairs}
    return any(m in names for m in markers)


def cookies_to_header(cookies: list[dict], domains: tuple[str, ...]) -> str:
    """Deduplicate by (name, domain); prefer smaller (host-specific) domains."""
    picked: dict[str, dict] = {}
    for c in cookies:
        name = c.get("name") or ""
        if not name or c.get("value") is None:
            continue
        domain = (c.get("domain") or "").lstrip(".")
        key = name
        prev = picked.get(name)
        if prev is None or len(domain) < len((prev.get("domain") or "").lstrip(".")):
            picked[name] = c
    return "; ".join(f"{c.get('name')}={c.get('value')}" for c in picked.values())


def read_profile_cookies(site: str) -> tuple[dict, str]:
    """Return (parsed, header) by reading the persistent profile via Playwright."""
    from playwright.sync_api import sync_playwright

    cfg = SITES[site]
    user_dir = PROFILE_DIR / site
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(user_dir),
            headless=True,
            viewport={"width": 1280, "height": 800},
        )
        try:
            raw = []
            for dom in cfg["domains"]:
                raw.extend(ctx.cookies(f"https://{dom}"))
                raw.extend(ctx.cookies(f"http://{dom}"))
            header = cookies_to_header(raw, cfg["domains"])
            return {"n_cookies": len(raw), "has_marker": has_marker(raw, site)}, header
        finally:
            ctx.close()


def login(
    site: str,
    progress: Phase | None = None,
    timeout_seconds: int = 240,
    headful: bool | None = None,
    force: bool = False,
) -> dict:
    """Open a visible browser window for the user to sign in, then harvest.

    If the persistent profile already holds a logged-in session, harvest and
    write it immediately without opening a window — unless ``force``."""
    from playwright.sync_api import sync_playwright

    cfg = SITES[site]
    report = progress or _noop
    user_dir = PROFILE_DIR / site
    user_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        if not force:
            # 1) try silent read: already logged in?
            report("opening", f"读取 {cfg['label']} 登录态…")
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=str(user_dir),
                headless=True,
                viewport={"width": 1280, "height": 800},
            )
            try:
                raw = []
                for dom in cfg["domains"]:
                    raw.extend(ctx.cookies(f"https://{dom}"))
                if has_marker(raw, site):
                    header = cookies_to_header(raw, cfg["domains"])
                    n = len({c.get("name") for c in raw})
                    report("harvesting", f"检测到已登录（{n} 个 cookie），正在写入…")
                    write_cookie(site, header)
                    report("saved", f"✓ 已写入 {SETTINGS.name}（{n} 个 cookie）")
                    return {
                        "ok": True,
                        "mode": "already_logged_in",
                        "n_cookies": n,
                        "header_len": len(header),
                    }
            finally:
                ctx.close()

        # 2) need sign-in: open a real window, poll for the login marker
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(user_dir),
            headless=not headful if headful is not None else False,
            viewport={"width": 1280, "height": 860},
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto(cfg["login_url"], wait_until="domcontentloaded", timeout=30_000)
            report("waiting_login", f"浏览器已打开，请在弹出的窗口中登录 {cfg['label']}（最多 {timeout_seconds}s）")
            deadline = time.time() + timeout_seconds
            last_probe = 0.0
            while time.time() < deadline:
                time.sleep(1.0)
                probe = ctx.cookies()
                if has_marker(probe, site):
                    break
                if time.time() - last_probe > 10:
                    last_probe = time.time()
                    report("waiting_login", f"还在等您登录 {cfg['label']}…")
            else:
                raise TimeoutError(f"等待登录超时（{timeout_seconds}s）")
            ready = ctx.cookies()
            header = cookies_to_header(ready, cfg["domains"])
            n = len({c.get("name") for c in ready})
            report("harvesting", f"检测到登录成功（{n} 个 cookie），正在写入…")
            write_cookie(site, header)
            report("saved", f"✓ 已写入 {SETTINGS.name}（{n} 个 cookie）")
            return {
                "ok": True,
                "mode": "signed_in_window",
                "n_cookies": n,
                "header_len": len(header),
            }
        finally:
            ctx.close()


# ---------- two-phase interactive login (gate flow) ----------
# Phase 1 `open_login_window`: opens a visible browser on the CALLING thread and
# BLOCKS on a confirmation event (harvest happens on the SAME thread — Playwright
# sync objects are thread-affine and cannot be touched from another thread).
# Phase 2 `confirm_and_save`: sets the event so the open-thread harvests and
# writes the cookie, then waits for the result.

_live_ctx_lock = threading.Lock()
_windows: dict[str, dict] = {}  # site -> {"ctx","p","event","result","opened_at"}


def _open_window_impl(site: str) -> tuple:
    from playwright.sync_api import sync_playwright

    cfg = SITES[site]
    user_dir = PROFILE_DIR / site
    user_dir.mkdir(parents=True, exist_ok=True)
    p = sync_playwright().start()
    try:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(user_dir),
            headless=False,
            viewport={"width": 1280, "height": 860},
        )
    except Exception:
        p.stop()
        raise
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(cfg["login_url"], wait_until="domcontentloaded", timeout=30_000)
    return p, ctx


def open_login_window(
    site: str,
    progress: Phase | None = None,
    timeout_seconds: int = 600,
    poll_interval: float = 2.0,
) -> dict:
    """Open a visible browser so the user can sign in.

    Polls the live context every ``poll_interval`` seconds and harvests as soon
    as the login marker appears (= user finished signing in) — no button press
    required. A :func:`confirm_and_save` call (frontend confirm button) is still
    honored as an early trigger. Harvest/write happens here on the same thread
    that owns the Playwright context (thread-affine).

    Returns after the harvest has finished (mode ``signed_in_window`` on success,
    ``timeout`` if login was never detected).
    """
    cfg = SITES[site]
    report = progress or _noop

    with _live_ctx_lock:
        if site in _windows:
            raise RuntimeError(f"{cfg['label']} 登录窗口已在等待登录，请勿重复打开")
        p, ctx = _open_window_impl(site)
        win = {"ctx": ctx, "p": p, "event": threading.Event(), "result": None, "opened_at": time.time()}
        _windows[site] = win
    report("waiting_login",
           f"浏览器已打开，请在弹出的窗口中登录 {cfg['label']}。登录完成后将自动检测并继续（最多 {timeout_seconds}s）")
    try:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            if win["event"].is_set():
                break  # 用户点了前端「确认」按钮，立即收割
            started = win["opened_at"]
            try:
                probe = ctx.cookies()
                if has_marker(probe, site):
                    break  # 检测到登录成功，自动收割
            except Exception:
                pass
            time.sleep(poll_interval)
            if time.time() - started > 15 and time.time() < deadline:
                report("waiting_login", f"还在等您登录 {cfg['label']}… 登录完成后将自动继续")
        else:
            report("error", f"{cfg['label']} 等待登录超时（{timeout_seconds}s）")
            return {"ok": False, "mode": "timeout", "site": site}
        # 用户已登录（事件触发或 marker 命中）：在本线程收割
        try:
            raw = []
            for dom in cfg["domains"]:
                raw.extend(ctx.cookies(f"https://{dom}"))
            if not has_marker(raw, site):
                raise RuntimeError(f"未在 {cfg['label']} 登录窗口中检测到登录状态，请确认已登录")
            header = cookies_to_header(raw, cfg["domains"])
            n = len({c.get("name") for c in raw})
            report("harvesting", f"检测到登录成功（{n} 个 cookie），正在写入…")
            write_cookie(site, header)
            result = {"ok": True, "mode": "signed_in_window", "n_cookies": n, "header_len": len(header)}
        except Exception as exc:
            report("error", f"收割失败：{exc}")
            result = {"ok": False, "mode": "error", "site": site, "error": str(exc)}
        with _live_ctx_lock:
            win["result"] = result
        if result["ok"]:
            report("saved", f"✓ 已写入 {SETTINGS.name}（{result['n_cookies']} 个 cookie）")
        return result
    finally:
        with _live_ctx_lock:
            _windows.pop(site, None)
        try:
            ctx.close()
        except Exception:
            pass
        try:
            p.stop()
        except Exception:
            pass


def confirm_and_save(
    site: str,
    progress: Phase | None = None,
    wait_seconds: float = 40.0,
) -> dict:
    """Signal an open login window to harvest+save now, then wait for the result.

    If no window is open, falls back to reading the persistent profile directly.
    """
    cfg = SITES[site]
    report = progress or _noop

    with _live_ctx_lock:
        win = _windows.get(site)

    if win is None:
        return _confirm_from_profile(site, report)

    report("harvesting", f"用户已确认，正在收割 {cfg['label']} cookie…")
    win["event"].set()
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        with _live_ctx_lock:
            result = win["result"]
        if result is not None:
            if result.get("ok"):
                report("saved", f"✓ 已写入 {SETTINGS.name}（{result.get('n_cookies', 0)} 个 cookie）")
            else:
                report("error", f"收割失败：{result.get('error', '未知错误')}")
            return result
        time.sleep(0.3)
    raise RuntimeError(f"{cfg['label']} 包含登录窗口收割超时（{wait_seconds}s）")


def _confirm_from_profile(site: str, report: Phase) -> dict:
    """No live window: read the persistent profile headlessly and save if logged in."""
    from playwright.sync_api import sync_playwright

    cfg = SITES[site]
    user_dir = PROFILE_DIR / site
    if not user_dir.exists():
        raise RuntimeError(f"{cfg['label']} 尚未打开过登录窗口")
    pw = sync_playwright().start()
    try:
        ctxb = pw.chromium.launch_persistent_context(
            user_data_dir=str(user_dir),
            headless=True,
            viewport={"width": 1280, "height": 800},
        )
        try:
            raw = []
            for dom in cfg["domains"]:
                raw.extend(ctxb.cookies(f"https://{dom}"))
            if not has_marker(raw, site):
                raise RuntimeError(f"未在 {cfg['label']} 登录窗口中检测到登录状态，请重新登录")
            header = cookies_to_header(raw, cfg["domains"])
        finally:
            ctxb.close()
        n = len({c.get("name") for c in raw})
        report("harvesting", f"检测到已登录（{n} 个 cookie），正在写入…")
        write_cookie(site, header)
        report("saved", f"✓ 已写入 {SETTINGS.name}（{n} 个 cookie）")
        return {"ok": True, "mode": "already_logged_in", "n_cookies": n, "header_len": len(header)}
    finally:
        pw.stop()


def headless_windows() -> bool:
    return False


def _escape_dq(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def write_cookie(section: str, header: str) -> Path:
    """Overwrite ``section.cookie`` in config/settings.yaml (backup first)."""
    backup = SETTINGS.with_suffix(".yaml.bak")
    shutil.copy2(SETTINGS, backup)
    text = SETTINGS.read_text(encoding="utf-8")
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    in_target = False
    replaced = False
    while i < len(lines):
        line = lines[i]
        if line.rstrip().endswith(":"):
            key = line.rstrip()[:-1].strip()
            in_target = key == section
            out.append(line)
            i += 1
            continue
        if in_target and (line.startswith(" ") or line.startswith("\t")):
            if line.strip().startswith("cookie:"):
                out.append(f'  cookie: "{_escape_dq(header)}"')
                replaced = True
            else:
                out.append(line)
            i += 1
            continue
        out.append(line)
        i += 1
    if not replaced:
        raise RuntimeError(f"settings.yaml 未找到 {section}.cookie 行，未写入")
    SETTINGS.write_text("\n".join(out) + "\n", encoding="utf-8")
    return SETTINGS


def current_cookie(section: str) -> str:
    with SETTINGS.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return ((data.get(section) or {}).get("cookie") or "").strip()


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--site", choices=list(SITES), required=True)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--timeout", type=int, default=240)
    args = ap.parse_args()

    def rep(phase: str, message: str) -> None:
        print(f"[{phase}] {message}", flush=True)

    cfg = SITES[args.site]
    if args.write:
        res = login(args.site, progress=rep, timeout_seconds=args.timeout)
        print("RESULT", json.dumps(res, ensure_ascii=False))
    else:
        info, header = read_profile_cookies(args.site)
        print(
            "PROFILE",
            json.dumps(
                {"site": args.site, **info, "header_len": len(header)},
                ensure_ascii=False,
            ),
        )
        print("yaml_section", cfg["yaml_section"], "marker_set", "已配置" if current_cookie(cfg["yaml_section"]) else "未配置")


if __name__ == "__main__":
    main()