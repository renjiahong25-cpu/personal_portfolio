from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .models import Venue

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "output"
OUT.mkdir(parents=True, exist_ok=True)


def write_reports(venues: list[Venue], passed: list[Venue], tag: str = "run") -> dict:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    payload = {
        "generated_at": ts,
        "tag": tag,
        "total_candidates": len(venues),
        "passed_hard_filters": len(passed),
        "venues": [v.model_dump() for v in venues],
        "passed": [v.model_dump() for v in passed],
    }
    json_path = OUT / f"venues_{tag}_{ts}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"# 团建会场候选报告（{ts}）",
        "",
        f"- 候选总数：{len(venues)}",
        f"- 通过硬过滤：{len(passed)}",
        "",
        "## 通过硬过滤（按得分排序）",
        "",
    ]
    if not passed:
        lines.append("_无通过硬过滤的候选，见下方待人工确认列表。_")
    lines += [
        "| # | 名称 | 车程min | 住宿容量 | 会议室(10-15人) | 娱乐 | 得分 | 需人工 |",
        "|---|------|---------|----------|-----------------|------|------|--------|",
    ]
    for i, v in enumerate(passed, 1):
        fit_rooms = [
            r.capacity
            for r in v.meeting_rooms
            if r.capacity and 10 <= r.capacity <= 15
        ]
        cap = v.lodging_capacity or v.bed_capacity
        if cap is None and v.room_count:
            cap = v.room_count * 2
        lines.append(
            f"| {i} | {v.name} | {v.drive_minutes} | {cap} | "
            f"{len(fit_rooms)}间 {fit_rooms} | "
            f"{'/'.join(v.entertainment.classes_hit) or '-'} | {v.score} | "
            f"{'Y' if v.needs_manual else ''} |"
        )

    lines += ["", "## 全部候选（含未通过）", ""]
    for v in venues:
        lines.append(f"### {v.name}")
        lines.append(f"- 地址：{v.address or '-'} {v.city}{v.district}")
        lines.append(f"- 车程：{v.drive_minutes} min / {(v.drive_distance_m or 0)/1000:.1f} km")
        lines.append(f"- 住宿：lodging={v.lodging_capacity} rooms={v.room_count} beds={v.bed_capacity}")
        rm = ", ".join(
            f"{r.name}:{r.capacity or '?'}" for r in v.meeting_rooms
        ) or "-"
        lines.append(f"- 会议室：{rm}")
        lines.append(
            f"- 周边：餐厅1km={v.restaurant_count_1km} 娱乐={v.entertainment.model_dump()}"
        )
        lines.append(f"- 得分：{v.score} breakdown={v.score_breakdown} hard_pass={v.pass_hard_filters}")
        if v.sources:
            lines.append(f"- 来源：{v.sources}")
        if v.notes:
            lines.append(f"- 备注：{'; '.join(v.notes)}")
        lines.append(f"- 高德：https://uri.amap.com/navigation?to={v.location.replace(',', '%2C')}%2C{v.name}&mode=car")
        if v.sources.get("ctrip"):
            lines.append(f"- 携程：{v.sources['ctrip']}")
        lines.append("")

    md_path = OUT / f"report_{tag}_{ts}.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    # latest pointers
    (OUT / "latest_report.md").write_text("\n".join(lines), encoding="utf-8")
    (OUT / "latest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"json": str(json_path), "md": str(md_path)}
