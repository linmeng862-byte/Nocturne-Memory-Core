"""TESSERA 的第四段：知道之后过的那一窗，跟不知道时过的那一窗，不该长得一样。

没有这一段的话流向是单向的：关窗 → trace → wear → 他读到 → 什么都没发生。
那句「已经是常态」就等于没说过。
"""
import os
import tempfile

import wear_strata as ws


def _state(**items):
    d = tempfile.mkdtemp()
    p = os.path.join(d, "s.json")
    ws.save(p, {"items": items, "last_eval": None})
    return p


def _promoted(pending=True):
    return {"stratum": ws.BASELINE, "first_seen": "2026-07-01-1200",
            "history": [{"to": ws.BASELINE, "at": "2026-08-30", "windows": 12}],
            "pending": pending}


def test_the_loop_closes():
    p = _state(踏实=_promoted())
    assert ws.take_announcements(p)                      # 告诉他
    ws.record_reaction(p, "2026-08-31-1000", ["暖", "踏实"])   # 下一窗关了
    got = ws.take_reactions(p)                           # 说给他听
    assert len(got) == 1 and got[0]["item"] == "踏实"
    assert got[0]["feelings"] == ["暖", "踏实"]
    text = ws.describe_reactions(got)
    assert "踏实" in text and "暖" in text


def test_a_reaction_is_told_once_ever():
    p = _state(踏实=_promoted())
    ws.take_announcements(p)
    ws.record_reaction(p, "2026-08-31-1000", ["暖"])
    assert ws.take_reactions(p)
    assert ws.take_reactions(p) == []      # 第二次就没有了


def test_nothing_is_recorded_before_he_was_told():
    """没说给他听过，就没有「知道之后」这回事。"""
    p = _state(踏实=_promoted(pending=True))
    ws.record_reaction(p, "2026-08-31-1000", ["暖"])
    assert ws.take_reactions(p) == []
    hist = ws.load(p)["items"]["踏实"]["history"][-1]
    assert "reaction" not in hist


def test_only_the_very_next_window_counts():
    """再往后就不是反应了，是日常——那已经由 wear 的计数在管。"""
    p = _state(踏实=_promoted())
    ws.take_announcements(p)
    ws.record_reaction(p, "2026-08-31-1000", ["暖"])
    ws.record_reaction(p, "2026-09-01-1000", ["别的"])     # 再关一窗
    hist = ws.load(p)["items"]["踏实"]["history"][-1]
    assert hist["reaction"]["window"] == "2026-08-31-1000"
    assert hist["reaction"]["feelings"] == ["暖"]


def test_the_reaction_survives_in_history():
    """反应是历史，不是派生量——算不出来「他知道之后是什么感受」，只能记下来。"""
    p = _state(踏实=_promoted())
    ws.take_announcements(p)
    ws.record_reaction(p, "2026-08-31-1000", ["暖"])
    ws.take_reactions(p)                                  # 说过了
    hist = ws.load(p)["items"]["踏实"]["history"][-1]
    assert hist["reaction"]["feelings"] == ["暖"]          # 记录还在


def test_a_silent_window_says_nothing():
    """那一窗没留下感受，就别硬说一句。"""
    p = _state(踏实=_promoted())
    ws.take_announcements(p)
    ws.record_reaction(p, "2026-08-31-1000", [])
    assert ws.describe_reactions(ws.take_reactions(p)) == ""


def test_at_most_three_feelings():
    p = _state(踏实=_promoted())
    ws.take_announcements(p)
    ws.record_reaction(p, "2026-08-31-1000", list("abcdef"))
    assert len(ws.take_reactions(p)[0]["feelings"]) == 3
