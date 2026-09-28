from __future__ import annotations

import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "output" / "latest.json"
OUT = ROOT / "data" / "output" / "review.html"


def ent(s: str) -> str:
    return html.escape(s or "")


def amap_nav(v: dict, esc: bool = True) -> str:
    loc = v["location"].replace(",", "%2C")
    name = ent(v["name"]) if esc else v["name"]
    url = f"https://uri.amap.com/navigation?to={loc}%2C{name}&mode=car"
    return url if esc else url.replace("&amp;", "&")


def amap_search(v: dict) -> str:
    base, sep, _ = v["location"].partition(",")
    return (
        f"https://uri.amap.com/marker?position={base},{sep}&name={ent(v['name'])}"
        if v["location"]
        else f"https://uri.amap.com/search?keyword={ent(v['name'])}&city=广州"
    )


def ctrip_mobile_url(v: dict) -> str:
    """Ctrip PC link -> mobile H5 link (opens/redirects to Ctrip App on phone)."""
    url = v.get("sources", {}).get("ctrip", "")
    if not url:
        return ""
    import re

    m = re.search(r"/hotels/(\d+)\.html", url)
    if not m:
        return url
    hid = m.group(1)
    name = ent(v.get("name") or "")
    return f"https://m.ctrip.com/webapp/hotel/hoteldetail/{hid}.html?" \
           f"keyword1={name}&fromapp=web2mobile&referer=venue_scout&link=1"


def lodging_cap(v: dict):
    cap = v.get("lodging_capacity") or v.get("bed_capacity")
    if cap is None and v.get("room_count"):
        cap = v["room_count"] * 2
    return cap or None


def share_text(v: dict) -> str:
    """One-line share text for WeChat: name + amap link (raw URL, no HTML escaping)."""
    name = v.get("name") or ""
    cap = lodging_cap(v)
    cap_txt = f"{cap}人" if cap else "容量待确认"
    drive = v.get("drive_minutes")
    drive_txt = f"{drive}分钟" if drive is not None else "车程待确认"
    link = amap_nav(v, esc=False)
    return f"{name}·{cap_txt}·车程{drive_txt}·高德导航→{link}"


def venue_card(v: dict, rank: int) -> str:
    cap = lodging_cap(v)
    rooms = ", ".join(
        f"{ent(r.get('name') or '')}:{r.get('capacity') or '?'}"
        for r in v.get("meeting_rooms") or []
    ) or "—"
    classes = " / ".join(v.get("entertainment", {}).get("classes_hit") or []) or "—"
    notes = "；".join(v.get("notes") or [])
    manual = bool(v.get("needs_manual"))

    cap_txt = f"{cap}" if cap else "待确认"
    drive_txt = f"{v.get('drive_minutes')} min" if v.get("drive_minutes") is not None else "—"
    score = v.get("score") or 0

    badge_cls = "pass" if v.get("pass_hard_filters") else "no"
    manual_badge = '<span class="badge manual">待人工</span>' if manual else ""
    cap_badge = (
        '<span class="badge cap">住宿≥100</span>'
        if cap and cap >= 100
        else '<span class="badge capbad">住宿&lt;100</span>'
        if cap
        else '<span class="badge capbad">住宿待确认</span>'
    )

    ctrip_url = ctrip_mobile_url(v)
    ctrip = (
        f'<a class="btn" href="{ent(ctrip_url)}" target="_blank">✈ 携程酒店</a>'
        if ctrip_url
        else ""
    )

    return f"""<div class="card {"passed" if v.get("pass_hard_filters") else ""}">
  <div class="rank">{rank}</div>
  <div class="body">
    <div class="head">
      <h3>{ent(v.get('name') or '')}</h3>
      <div class="chips">{manual_badge} {cap_badge}</div>
    </div>
    <div class="meta">
      <span><b>{score:.2f}</b> 分</span>
      <span>🚗 {drive_txt}</span>
      <span>🛏 {cap_txt}</span>
      <span>🗣 会议室: {ent(rooms)}</span>
      <span>🎡 {ent(classes)}</span>
    </div>
    <div class="addr">{ent(v.get('address') or '-')} {ent(v.get('city') or '')}{ent(v.get('district') or '')}</div>
    <div class="actions">
      <a class="btn primary" href="{amap_nav(v)}" data-rel="nofollow">🧭 高德导航</a>
      <a class="btn" href="{amap_search(v)}" data-rel="nofollow">🗺 高德地图</a>
      {ctrip}
      <button class="btn" onclick="copyShare(this)" data-share='{json.dumps(share_text(v), ensure_ascii=False).replace("'", "&#39;")}'>📋 复制分享</button>
    </div>
    {f'<div class="notes">💬 {ent(notes)}</div>' if notes else ''}
  </div>
</div>"""


def build() -> None:
    payload = json.loads(SRC.read_text(encoding="utf-8"))
    venues = payload["passed"] + [
        v for v in payload["venues"] if not v.get("pass_hard_filters")
    ]

    passed_n = payload["passed_hard_filters"]
    total = payload["total_candidates"]

    cards = [venue_card(v, i + 1) for i, v in enumerate(venues)]
    passed_cards = [
        venue_card(v, i + 1)
        for i, v in enumerate(payload["passed"])
    ]

    page = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>团建会场核验清单 · venue-scout</title>
<style>
  :root {{
    --bg:#f4f5f7; --card:#fff; --ink:#1f2430; --sub:#6b7280;
    --line:#e5e7eb; --pri:#0b7aff; --prid:#0759c4; --ok:#0e9f6e; --warn:#e11d48;
  }}
  * {{ box-sizing:border-box; }}
  body {{ font-family:"Segoe UI","Microsoft YaHei",system-ui,sans-serif; background:var(--bg); color:var(--ink); margin:0; padding:24px; }}
  h1 {{ font-size:22px; margin:0 0 4px; }}
  .sub {{ color:var(--sub); font-size:13px; margin-bottom:18px; }}
  .toolbar {{ display:flex; gap:8px; align-items:center; margin-bottom:18px; flex-wrap:wrap; }}
  .toolbar input {{
    padding:8px 12px; border:1px solid var(--line); border-radius:8px; font-size:14px; width:260px;
  }}
  .seg {{
    display:flex; border:1px solid var(--line); border-radius:8px; overflow:hidden; background:var(--card);
  }}
  .seg button {{
    border:0; background:transparent; padding:8px 14px; cursor:pointer; font-size:13px; color:var(--sub);
  }}
  .seg button.on {{ background:var(--pri); color:#fff; }}
  #count {{ color:var(--sub); font-size:13px; }}
  .list {{ max-width:1080px; margin:0 auto; display:grid; gap:12px; grid-template-columns:1fr 1fr; }}
  .card {{ display:flex; gap:12px; background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px; position:relative; }}
  .card.passed {{ border-left:4px solid var(--ok); }}
  .card.noop {{ display:none; }}
  .rank {{ font-size:18px; font-weight:700; color:var(--sub); min-width:30px; line-height:1.2; }}
  .body {{ flex:1; min-width:0; }}
  .head {{ display:flex; justify-content:space-between; align-items:flex-start; gap:8px; }}
  .head h3 {{ margin:0; font-size:16px; line-height:1.3; }}
  .chips {{ display:flex; gap:6px; flex-wrap:wrap; }}
  .badge {{ padding:2px 8px; border-radius:999px; font-size:11px; font-weight:600; }}
  .badge.manual {{ background:#fff1f2; color:var(--warn); }}
  .badge.cap {{ background:#ecfdf5; color:var(--ok); }}
  .badge.capbad {{ background:#f3f4f6; color:var(--sub); }}
  .meta {{ display:flex; flex-wrap:wrap; gap:6px 14px; margin:8px 0 6px; font-size:13px; color:var(--sub); }}
  .meta b {{ color:var(--ink); font-size:15px; }}
  .addr {{ font-size:12px; color:var(--sub); margin-bottom:10px; }}
  .actions {{ display:flex; gap:8px; flex-wrap:wrap; }}
  .btn {{
    display:inline-flex; align-items:center; gap:4px; padding:7px 12px; border:1px solid var(--line); border-radius:8px;
    font-size:13px; color:var(--ink); text-decoration:none; background:#fff; cursor:pointer; font-family:inherit;
  }}
  .btn:hover {{ border-color:var(--pri); color:var(--prid); }}
  .btn.primary {{ background:var(--pri); color:#fff; border-color:var(--pri); }}
  .btn.primary:hover {{ background:var(--prid); }}
  .btn.copied {{ background:var(--ok); color:#fff; border-color:var(--ok); }}
  .notes {{ margin-top:8px; font-size:12px; color:#92400e; background:#fffbeb; border:1px solid #fde68a; border-radius:8px; padding:6px 8px; }}
  @media (max-width:820px) {{ .list {{ grid-template-columns:1fr; }} }}
</style>
</head>
<body>
  <h1>团建会场核验清单</h1>
  <div class="sub">生成 {ent(payload.get('generated_at') or '')} · 共 {total} 候选 · 通过硬过滤 {passed_n} 家 · 坐标系 GCJ-02<br>手机点击「高德导航/高德地图」唤起高德 App 直达地点；「携程酒店」唤起携程 App 看酒店详情。支持复制分享。</div>
  <div class="toolbar">
    <div class="seg" id="mode">
      <button data-mode="all" class="on">全部</button>
      <button data-mode="pass">只看通过 ({passed_n})</button>
    </div>
    <input id="q" placeholder="🔍 按名称 / 区县 / 关键词过滤…">
    <span id="count"></span>
  </div>
  <div class="list" id="list">{''.join(cards)}</div>
<script>
  const raw = {json.dumps([{ "name": v["name"], "addr": (v.get("address") or "") + (v.get("city") or "") + (v.get("district") or ""), "loc": v.get("location") or "", "score": v.get("score") or 0, "pass": v.get("pass_hard_filters"), "notes": "；".join(v.get("notes") or []) } for v in payload['venues']], ensure_ascii=False)};
  const cards = Array.from(document.querySelectorAll('.card'));
  const passOnly = new Set(raw.filter(x => x.pass).map(x => x.name));
  const q = document.getElementById('q');
  const count = document.getElementById('count');
  const modeBtns = document.querySelectorAll('#mode button');

  function render() {{
    const text = (q.value || '').trim().toLowerCase();
    let shown = 0;
    cards.forEach(card => {{
      const name = card.querySelector('h3').textContent;
      const note = (card.querySelector('.notes') ? card.querySelector('.notes').textContent : '') + (card.querySelector('.addr') ? card.querySelector('.addr').textContent : '');
      const ok = (
        (!text || name.toLowerCase().includes(text) || note.toLowerCase().includes(text))
        && (mode !== 'pass' || passOnly.has(name))
      );
      card.classList.toggle('noop', !ok);
      if (ok) shown++;
    }});
    count.textContent = `显示 ${{shown}} / ${{cards.length}}`;
  }}
  let mode = 'all';
  modeBtns.forEach(b => b.addEventListener('click', () => {{
    mode = b.dataset.mode;
    modeBtns.forEach(x => x.classList.toggle('on', x === b));
    render();
  }}));
  q.addEventListener('input', render);
  render();

  window.copyShare = function(btn) {{
    const text = btn.dataset.share;
    const done = () => {{
      const old = btn.textContent;
      btn.textContent = '✓ 已复制';
      btn.classList.add('copied');
      setTimeout(() => {{ btn.textContent = old; btn.classList.remove('copied'); }}, 1600);
    }};
    if (navigator.clipboard && navigator.clipboard.writeText) {{
      navigator.clipboard.writeText(text).then(done).catch(() => fallbackCopy(text, done));
    }} else {{
      fallbackCopy(text, done);
    }}
  }};
  function fallbackCopy(text, done) {{
    const ta = document.createElement('textarea');
    ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    try {{ document.execCommand('copy'); }} catch(e) {{}}
    document.body.removeChild(ta); done();
  }}
</script>
</body>
</html>"""

    OUT.write_text(page, encoding="utf-8")
    print(f"written: {OUT}")
    print(f"passed={passed_n} total={total}")


if __name__ == "__main__":
    build()