"""scripts/test_data_chain.py「高德路程」段的可纯化断言：车程分钟取整 + 折线解析（HTTP 层 stub）。"""
from src.amap_client import AMapClient


def _stub(c: AMapClient, paths: list) -> None:
    c._get = lambda url, params, use_cache=True: {"route": {"paths": paths}}


def test_drive_minutes_rounding():
    c = AMapClient(key="stub")
    _stub(c, [{"duration": "3610", "distance": "53000"}])
    assert c.drive_minutes("a", "b") == 60  # max(1, round(3610/60))
    _stub(c, [{"duration": "30", "distance": "100"}])
    assert c.drive_minutes("a", "b") == 1  # 不足 1 分钟按 1 计
    _stub(c, [])
    assert c.drive_minutes("a", "b") is None


def test_driving_via_points_parsing():
    c = AMapClient(key="stub")
    _stub(c, [{"duration": "600", "distance": "1000",
               "steps": [{"polyline": "113.1,23.1;113.2,23.2"}, {"polyline": ""}]}])
    r = c.driving("a", "b", extensions="all")
    assert r["duration_s"] == 600
    assert r["distance_m"] == 1000
    assert r["via"] == [[113.1, 23.1], [113.2, 23.2]]


def test_driving_base_no_via():
    c = AMapClient(key="stub")
    _stub(c, [{"duration": "600", "distance": "1000"}])
    r = c.driving("a", "b")
    assert "via" not in r
