"""第二期：沉降。停止出现够久的 texture / baseline 往下掉一层。

底色不该永远钉死 —— 关系变了，那个「踏实」得能摘下来。但也不能一次缺席就掉：
扛得越久，需要沉默越久（Roberts & DelVecchio 的固着度）。见 docs/WEAR-STRATA.md。
"""
import os
import tempfile

import wear
import wear_strata as ws


def _state(**items):
    d = tempfile.mkdtemp()
    p = os.path.join(d, "s.json")
    ws.save(p, {"items": items, "last_eval": None})
    return p


# 20 个窗口，日期递增（span 足够长，免得升级逻辑把它当 texture 往回顶）。
_WINDOWS = [f"2026-{6 + i // 28:02d}-{1 + i % 28:02d}-1200" for i in range(20)]


def _patch(monkeypatch, *, item, carried, last_seen):
    """让 evaluate 看到：只有一个 item，一共 20 个窗口，它最后出现在 last_seen。"""
    feel = {"item": item, "windows_carried": carried,
            "first_seen": _WINDOWS[0], "last_seen": last_seen}
    monkeypatch.setattr(wear, "profile",
                        lambda _d: {"recurring_feelings": [feel],
                                    "carried_unresolved": []})
    monkeypatch.setattr(wear, "read_traces",
                        lambda _d: [{"window": w} for w in _WINDOWS])


def _baseline():
    return {"stratum": ws.BASELINE, "first_seen": _WINDOWS[0],
            "history": [{"to": ws.BASELINE, "at": "2026-08-01", "windows": 12}],
            "pending": False}


def test_silent_long_enough_baseline_sinks_to_texture(monkeypatch):
    # carried=12 → entrenchment=0.6 → 需要沉默 3+round(7.2)=10 窗。
    # last_seen=w4 → 沉默 = 19-4 = 15 ≥ 10 → 掉一层。
    _patch(monkeypatch, item="踏实", carried=12, last_seen=_WINDOWS[4])
    p = _state(踏实=_baseline())
    ws.evaluate("x", p, now=None)
    rec = ws.load(p)["items"]["踏实"]
    assert rec["stratum"] == ws.TEXTURE
    # 掉回 texture 后，就不再被 breath 当底色排除掉了 = 摘下来了。
    assert "踏实" not in ws.baseline_items(p)


def test_a_single_recent_absence_does_not_sink(monkeypatch):
    # last_seen=w17 → 沉默 = 2 < 10 → 单次缺席推不动它（hysteresis）。
    _patch(monkeypatch, item="踏实", carried=12, last_seen=_WINDOWS[17])
    p = _state(踏实=_baseline())
    ws.evaluate("x", p, now=None)
    assert ws.load(p)["items"]["踏实"]["stratum"] == ws.BASELINE
    assert "踏实" in ws.baseline_items(p)


def test_entrenchment_makes_deep_things_resist_longer(monkeypatch):
    # carried=20 → entrenchment=1.0 → 需要沉默 3+12=15 窗。
    # 沉默 14（last_seen=w5，19-5=14）还不够，扛得更久所以更难掉。
    _patch(monkeypatch, item="安稳", carried=20, last_seen=_WINDOWS[5])
    p = _state(安稳={"stratum": ws.BASELINE, "first_seen": _WINDOWS[0],
                     "history": [{"to": ws.BASELINE, "at": "2026-08-01", "windows": 20}],
                     "pending": False})
    ws.evaluate("x", p, now=None)
    assert ws.load(p)["items"]["安稳"]["stratum"] == ws.BASELINE
    # 再沉默一窗（15 ≥ 15）就该掉了。
    _patch(monkeypatch, item="安稳", carried=20, last_seen=_WINDOWS[4])
    ws.evaluate("x", p, now=None)
    assert ws.load(p)["items"]["安稳"]["stratum"] == ws.TEXTURE


def test_sinking_appends_history_and_keeps_the_rise(monkeypatch):
    _patch(monkeypatch, item="踏实", carried=12, last_seen=_WINDOWS[4])
    p = _state(踏实=_baseline())
    ws.evaluate("x", p, now=None)
    hist = ws.load(p)["items"]["踏实"]["history"]
    # 升级过的记录永远保留，降级只增在后面。
    assert hist[0]["to"] == ws.BASELINE
    assert hist[-1]["to"] == ws.TEXTURE and hist[-1]["from"] == ws.BASELINE
    assert hist[-1]["lived"] == [_WINDOWS[0], _WINDOWS[4]]   # 活过的区间


def test_event_does_not_sink_further(monkeypatch):
    # 已经在 event 的不再往下沉（它本来就不进 breath）。
    _patch(monkeypatch, item="一闪念", carried=2, last_seen=_WINDOWS[0])
    p = _state(一闪念={"stratum": ws.EVENT, "first_seen": _WINDOWS[0],
                      "history": [], "pending": False})
    ws.evaluate("x", p, now=None)
    assert ws.load(p)["items"]["一闪念"]["stratum"] == ws.EVENT
