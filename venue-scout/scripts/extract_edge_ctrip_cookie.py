from __future__ import annotations

import argparse
import base64
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path

EDGE_USER = Path.home() / "AppData/Local/Microsoft/Edge/User Data"
SETTINGS = Path(r"D:\Program Files\traveldesign\venue-scout\config/settings.yaml")
CTRIP_HOSTS = (
    "ctrip.com",
    "ctrip",
    "trip.com",
    "m.ctrip.com",
    "hotels.ctrip.com",
)


def _load_aes_key() -> bytes:
    local_state = json.loads((EDGE_USER / "Local State").read_text(encoding="utf-8"))
    enc = base64.b64decode(local_state["os_crypt"]["encrypted_key"])
    if enc.startswith(b"DPAPI"):
        enc = enc[5:]
    import win32crypt

    return win32crypt.CryptUnprotectData(enc, None, None, None, 0)[1]


def _decrypt_value(v: bytes, key: bytes) -> str:
    if v.startswith(b"v10") or v.startswith(b"v11"):
        from Crypto.Cipher import AES

        nonce, body, tag = v[3:15], v[15:-16], v[-16:]
        return AES.new(key, AES.MODE_GCM, nonce=nonce).decrypt_and_verify(body, tag).decode(
            "utf-8", "replace"
        )
    if v.startswith(b"v20"):
        # app-bound: try DPAPI on remainder (may fail)
        try:
            import win32crypt

            return win32crypt.CryptUnprotectData(v[10:], None, None, None, 0)[1].decode(
                "utf-8", "replace"
            )
        except Exception as e:
            raise RuntimeError(f"v20 ABE not decryptable via DPAPI: {e}") from e
    # legacy DPAPI
    import win32crypt

    return win32crypt.CryptUnprotectData(v, None, None, None, 0)[1].decode("utf-8", "replace")


def collect_cookies(profile: str = "Default") -> dict[str, str]:
    src = EDGE_USER / profile / "Network" / "Cookies"
    if not src.exists():
        raise FileNotFoundError(src)
    key = _load_aes_key()
    rows: list[tuple] = []
    # Prefer direct read: Edge often allows concurrent SQLite readers (WAL).
    # Fallback: copy via file share / backup API / nolock.
    open_err = None
    for uri in (
        f"file:{src.as_posix()}?mode=ro&immutable=1",
        f"file:{src.as_posix()}?mode=ro&nolock=1",
        f"file:{src.as_posix()}?mode=ro",
    ):
        try:
            con = sqlite3.connect(uri, uri=True)
            try:
                rows = con.execute(
                    "SELECT host_key, name, encrypted_value FROM cookies "
                    "WHERE host_key LIKE '%ctrip%' OR host_key LIKE '%trip.com%' "
                    "OR host_key LIKE '%ctrip.com%'"
                ).fetchall()
                if rows:
                    open_err = None
                    break
            finally:
                con.close()
        except Exception as e:
            open_err = e
            rows = []
    if open_err is not None or not rows:
        with tempfile.TemporaryDirectory() as td:
            dst = Path(td) / "Cookies"
            # Try Python open with share flags via CreateFile if copy fails
            try:
                shutil.copy2(src, dst)
            except PermissionError:
                import ctypes
                from ctypes import wintypes

                GENERIC_READ = 0x80000000
                FILE_SHARE_READ = 1
                FILE_SHARE_WRITE = 2
                FILE_SHARE_DELETE = 4
                OPEN_EXISTING = 3
                k32 = ctypes.windll.kernel32
                handle = k32.CreateFileW(
                    str(src),
                    GENERIC_READ,
                    FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                    None,
                    OPEN_EXISTING,
                    0,
                    None,
                )
                if handle == -1 or handle is None:
                    raise PermissionError(f"CreateFile failed: {ctypes.GetLastError()}") from open_err
                try:
                    data = ctypes.create_string_buffer(0)
                    # read whole file
                    size = k32.GetFileSize(handle, None)
                    if size == 0xFFFFFFFF:
                        raise OSError("GetFileSize failed")
                    buf = (ctypes.c_char * size)()
                    read = wintypes.DWORD()
                    ok = k32.ReadFile(handle, buf, size, ctypes.byref(read), None)
                    if not ok:
                        raise OSError(f"ReadFile failed: {ctypes.GetLastError()}")
                    dst.write_bytes(bytes(buf[: read.value]))
                finally:
                    k32.CloseHandle(handle)
            con = sqlite3.connect(f"file:{dst.as_posix()}?mode=ro", uri=True)
            try:
                rows = con.execute(
                    "SELECT host_key, name, encrypted_value FROM cookies "
                    "WHERE host_key LIKE '%ctrip%' OR host_key LIKE '%trip.com%' "
                    "OR host_key LIKE '%ctrip.com%'"
                ).fetchall()
            finally:
                con.close()
    out: dict[str, str] = {}
    errors: list[str] = []
    for host, name, raw in rows:
        if not raw:
            continue
        try:
            out[name] = _decrypt_value(raw, key)
        except Exception as e:
            errors.append(f"{host}:{name}:{e}")
    if errors and not out:
        raise RuntimeError("all decrypt failed: " + "; ".join(errors[:5]))
    if errors:
        out["_decrypt_errors"] = str(len(errors))
        out["_decrypt_sample"] = errors[0]
    out["_hosts"] = str(sorted({r[0] for r in rows}))
    out["_row_count"] = str(len(rows))
    return out


def to_header(cookies: dict[str, str]) -> str:
    skip = {"_decrypt_errors", "_decrypt_sample", "_hosts"}
    parts = [f"{k}={v}" for k, v in cookies.items() if k not in skip]
    return "; ".join(parts)


def write_yaml_cookie(header: str) -> None:
    text = SETTINGS.read_text(encoding="utf-8")
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        if line.strip() == "ctrip:":
            i += 1
            while i < len(lines) and (lines[i].startswith(" ") or lines[i].startswith("\t")):
                if lines[i].strip().startswith("cookie:"):
                    # escape for double-quoted YAML
                    esc = header.replace("\\", "\\\\").replace('"', '\\"')
                    out.append(f'  cookie: "{esc}"')
                else:
                    out.append(lines[i])
                i += 1
            continue
        i += 1
    SETTINGS.write_text("\n".join(out) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="Default")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--preview", type=int, default=80)
    args = ap.parse_args()
    cookies = collect_cookies(args.profile)
    header = to_header(cookies)
    print(f"n_cookie_parts={len(header.split(';'))} header_len={len(header)}")
    print(f"hosts={cookies.get('_hosts')}")
    if cookies.get("_decrypt_errors"):
        print(f"decrypt_errors={cookies['_decrypt_errors']} sample={cookies.get('_decrypt_sample')}")
    print(f"preview={header[: args.preview]}...")
    # show some useful names
    names = [k for k in cookies if not k.startswith("_")]
    print("names_sample=" + ",".join(names[:30]))
    if args.write:
        write_yaml_cookie(header)
        print(f"wrote {SETTINGS}")


if __name__ == "__main__":
    main()
