r"""小红书攻略研究（MediaCrawler 封装）。

按场地生成关键词 → 跑 MediaCrawler 搜索爬取（首次弹浏览器扫码登录，登录态缓存）
→ 解析笔记 + 合并评论 → 相关性打标（本场地/附近地点/同类玩法/同城）
→ 落盘 data/output/xhs/{venue_id}.json。

用法（在项目根执行）：
  python scripts\xhs_research.py --venue-id <id> [--pipeline <file>] [--notes 15]

退出前打印一行 JSON：{"ok":bool,"venue_id","keywords":[...],"n_notes":N,"file":"...","err":""}
"""
import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MC = ROOT / "vendor" / "MediaCrawler"
PY_MC = MC / ".venv" / "Scripts" / "python.exe"
XHS_OUT = ROOT / "data" / "output" / "xhs"
LOGDIR = Path(__file__).resolve().parents[1] / "data" / "output"
CITY_SHORT = re.compile(r"(市|区|县|省)")


def short_name(name: str) -> str:
    s = re.sub(r"（[^）]*）|\([^)]*\)", "", name or "").strip()
    return s


def build_keywords(venue: dict, ent_prefs: list, poi_names: list) -> list:
    name = venue.get("name") or ""
    sn = short_name(name)
    city = CITY_SHORT.sub("", (venue.get("city") or ""))
    district = (venue.get("district") or "").replace("区", "").replace("县", "")
    ent_cn = {"boating": "游船", "karting": "卡丁车", "rafting": "漂流"}
    kws = [sn]
    if city:
        if district:
            kws.append(f"{city}{district}团建")
        kws.append(f"{city}两天一夜")
        for e in ent_prefs:
            if not e:
                continue
            kws.append(f"{city}{ent_cn.get(e, e)}")
    for p in poi_names[:2]:
        kws.append(f"{p}攻略")
    seen, out = set(), []
    for k in kws:
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out[:6]


def parse_note(d: dict) -> dict:
    user = d.get("nickname") or (d.get("user") or {}).get("nickname") or ""
    nid = d.get("note_id") or ""
    return {
        "note_id": nid,
        "url": d.get("note_url") or f"https://www.xiaohongshu.com/explore/{nid}",
        "title": (d.get("title") or "").strip(),
        "desc": (d.get("desc") or "").strip()[:600],
        "user": user,
        "source_keyword": d.get("source_keyword") or "",
        "liked": d.get("liked_count") or "",
        "collected": d.get("collected_count") or "",
        "time": d.get("time"),
        "tags": [t.get("name") if isinstance(t, dict) else t for t in (d.get("tag_list") or [])][:8],
    }


def tag_relevance(note: dict, venue: dict, ent_prefs: list, poi_names: list) -> tuple:
    text = note["title"] + " " + note["desc"]
    sn = short_name(venue.get("name") or "")
    city = CITY_SHORT.sub("", (venue.get("city") or ""))
    matched = []
    if sn and sn in text:
        matched.append("本场地")
    for p in poi_names:
        if p and p in text:
            matched.append(f"附近:{p}")
    for e in ent_prefs:
        if e and e not in ("boating", "karting", "rafting", "other") and e in text:
            matched.append(f"玩法:{e}")
    if city and city in text:
        matched.append(f"同城:{city}")
    if "本场地" in matched:
        level = "venue"
    elif any(m.startswith("附近") for m in matched):
        level = "nearby"
    elif any(m.startswith("玩法") for m in matched):
        level = "type"
    elif any(m.startswith("同城") for m in matched):
        level = "city"
    else:
        level = "other"
    return level, matched


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--venue-id", required=True)
    ap.add_argument("--pipeline", default=str(ROOT / "data" / "output" / "latest.json"))
    ap.add_argument("--notes", type=int, default=15)
    ap.add_argument("--no-crawl", action="store_true", help="跳过爬取，直接解析当天已有数据")
    a = ap.parse_args()

    def finish(ok, **kw):
        print(json.dumps({"ok": ok, **kw}, ensure_ascii=False), flush=True)
        return 0 if ok else 1

    pipe = Path(a.pipeline)
    if not pipe.exists():
        return finish(False, err=f"pipeline 文件不存在: {pipe}")
    data = json.loads(pipe.read_text(encoding="utf-8"))
    venue = next((v for v in data.get("venues", []) if v.get("id") == a.venue_id), None)
    if not venue:
        return finish(False, err=f"venue 未找到: {a.venue_id}")

    sp_file = ROOT / "data" / "output" / "session_plan.json"
    ent_prefs = []
    if sp_file.exists():
        try:
            ent_prefs = list((json.loads(sp_file.read_text(encoding="utf-8")).get("ai_answers") or {}).get("ent_prefs") or [])
        except Exception:
            pass

    poi_names = [p.get("name") for p in (venue.get("nearby_pois") or []) if p.get("name")][:5]
    keywords = build_keywords(venue, ent_prefs, poi_names)

    before = set(MC.glob("data/xhs/json/search_contents_*.json")) | set(MC.glob("data/xhs/json/search_comments_*.json"))
    ts = time.strftime("%Y%m%d_%H%M%S")
    logf = LOGDIR / f"xhs_research_{ts}.log"
    if a.no_crawl:
        print("[xhs_research] --no-crawl：复用当天已有数据", flush=True)
        p = None
    else:
        cmd = [
            str(PY_MC), "main.py", "--platform", "xhs", "--lt", "qrcode", "--type", "search",
            "--keywords", ",".join(keywords),
            "--get_comment", "yes", "--get_sub_comment", "no", "--get_media", "no",
            "--headless", "no", "--save_data_option", "json",
            "--crawler_max_notes_count", str(a.notes),
        ]
        print(f"[xhs_research] keywords={keywords}", flush=True)
        print(f"[xhs_research] log={logf}", flush=True)
        try:
            with open(logf, "w", encoding="utf-8") as lf:
                p = subprocess.run(cmd, cwd=str(MC), stdout=lf, stderr=subprocess.STDOUT,
                                   timeout=1800, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            return finish(False, err="crawl timeout 1800s（数据可能已部分落盘，可加 --no-crawl 复用）", log=str(logf))

    if a.no_crawl:
        existing = sorted(MC.glob("data/xhs/json/search_contents_*.json"), key=lambda f: f.stat().st_mtime)
        if not existing:
            return finish(False, err="no existing data")
        new_files = existing[-1:]
    else:
        new_files = [f for f in set(MC.glob("data/xhs/json/search_contents_*.json")) - before]
        if not new_files:
            tail = logf.read_text(encoding="utf-8", errors="replace")[-400:]
            return finish(False, err="no output file（可用 --no-crawl 复用当天已落盘数据）", rc=p.returncode, log_tail=tail)

    notes_raw = json.loads(sorted(new_files)[-1].read_text(encoding="utf-8"))
    if isinstance(notes_raw, dict):
        notes_raw = notes_raw.get("notes") or []
    seen_ids, notes = set(), []
    for d in notes_raw:
        if isinstance(d, dict) and d.get("note_id") and d["note_id"] not in seen_ids:
            seen_ids.add(d["note_id"])
            notes.append(parse_note(d))

    comments_by_note = {}
    if a.no_crawl:
        cfiles = sorted(MC.glob("data/xhs/json/search_comments_*.json"), key=lambda f: f.stat().st_mtime)
    else:
        cfiles = [f for f in set(MC.glob("data/xhs/json/search_comments_*.json")) - before]
    if cfiles:
        try:
            for d in json.loads(sorted(cfiles)[-1].read_text(encoding="utf-8")):
                if not isinstance(d, dict):
                    continue
                nid = d.get("note_id")
                if not nid and isinstance(d.get("noteCard"), dict):
                    nid = d["noteCard"].get("note_id")
                if nid:
                    comments_by_note.setdefault(nid, []).append({
                        "content": (d.get("content") or "")[:200],
                        "user": (d.get("user") or {}).get("nickname", "") if isinstance(d.get("user"), dict) else "",
                        "likes": d.get("like_count") or d.get("likeCount") or "",
                    })
        except Exception:
            pass

    out_notes = []
    stats = {"venue": 0, "nearby": 0, "type": 0, "city": 0, "other": 0}
    for n in notes:
        level, matched = tag_relevance(n, venue, ent_prefs, poi_names)
        n["relevance"] = level
        n["matched"] = matched
        stats[level] = stats.get(level, 0) + 1
        n["comments"] = comments_by_note.get(n["note_id"], [])[:5]
        out_notes.append(n)
    order = {"venue": 0, "nearby": 1, "type": 2, "city": 3, "other": 4}
    out_notes.sort(key=lambda n: (order.get(n["relevance"], 9), -int(n.get("liked") or 0)))

    XHS_OUT.mkdir(parents=True, exist_ok=True)
    out_file = XHS_OUT / f"{a.venue_id}.json"
    out_file.write_text(json.dumps({
        "venue_id": a.venue_id,
        "venue_name": venue.get("name"),
        "city": venue.get("city"),
        "district": venue.get("district"),
        "keywords": keywords,
        "ent_prefs": ent_prefs,
        "crawled_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "stats": stats,
        "notes": out_notes,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    return finish(True, venue_id=a.venue_id, keywords=keywords, n_notes=len(out_notes),
                  stats=stats, file=str(out_file), log=str(logf))


if __name__ == "__main__":
    sys.exit(main())
