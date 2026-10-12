"""scripts/test_ctrip_seeds.py suggest 排序口径的可纯化断言（BUG-0009 跨城错配）。"""
from scripts.test_ctrip_seeds import rank_hit


def test_rank_name_match_first():
    hits = [
        {"name": "广州某酒店", "city": "广州"},
        {"name": "清远清泉湾爱琴海酒店", "city": "清远"},
    ]
    ranked = sorted(hits, key=lambda h: rank_hit(h, "清泉湾爱琴海", [], "清远", ""))
    assert ranked[0]["name"].startswith("清远清泉湾")


def test_rank_reject_wrong_city():
    local = {"name": "广州乡约雅轩温泉民宿", "city": "广州"}
    wrong = {"name": "忻州沐雅轩温泉民宿", "city": "忻州"}
    assert rank_hit(local, "乡约雅轩温泉民宿", [], "广州", "") == (0, 0)
    assert rank_hit(wrong, "乡约雅轩温泉民宿", [], "广州", "") == (1, 10)
    assert rank_hit(wrong, "乡约雅轩温泉民宿", [], "广州", "") > rank_hit(local, "乡约雅轩温泉民宿", [], "广州", "")


def test_rank_alias_and_district_bonus():
    h = {"name": "从化碧水峡度假村", "city": "广州"}
    by_alias = rank_hit(h, "碧水峡山庄", ["碧水峡度假村"], "广州", "从化")
    assert by_alias == (0, -1)  # 别名命中 + 区县加分
    plain = rank_hit(h, "碧水峡山庄", [], "广州", "从化")
    assert plain[0] == 1  # 无别名 → 名称不命中
