"""整窗的情绪分布：峰和终只是两个点，一整窗的情绪是一条分布。

Fleeson：一个人的特质是他状态分布的重心，不是某一个瞬间。
"""
import continuity_core as cc


def test_a_flat_window_and_a_spiky_one_no_longer_look_alike():
    """这就是加这个字段的全部理由。

    两窗的峰一样、结尾一样，但一个是从头烈到尾、一个只在某处炸了一下。
    只留端点的话，它们留下的痕迹一模一样。
    """
    spiky = cc._render_affect({"n": 12, "mean": 2.0, "peak": 9})
    flat = cc._render_affect({"n": 12, "mean": 8.5, "peak": 9})
    assert spiky != flat
    assert "2.0" in spiky and "8.5" in flat


def test_moods_are_reported_as_counts_not_interpreted():
    """说分布本身，不替他解释。"""
    t = cc._render_affect({"n": 7, "moods": {"warm": 5, "ache": 2}})
    assert "warm（5 次）" in t and "ache（2 次）" in t
    assert "所以" not in t and "开心" not in t


def test_nothing_said_when_nothing_was_sent():
    for v in (None, {}, {"n": 0}, "坏的", 42, []):
        assert cc._render_affect(v) == ""


# ---- 解析：这个函数在关窗路径上，它炸了就是这一窗的质地整个丢了 ----

def test_garbage_never_raises_and_never_half_writes():
    for raw in ("", "不是json", "[1,2]", "null", '{"n":"abc"}', '{"n":0}',
                '{"n":-3}', "{", '"字符串"', None):
        assert cc._parse_affect(raw) is None, raw


def test_a_good_payload_survives():
    got = cc._parse_affect('{"n": 12, "mean": 5.44, "peak": 9, "moods": {"warm": 5}}')
    assert got == {"n": 12, "mean": 5.44, "peak": 9.0, "moods": {"warm": 5}}


def test_unknown_fields_are_ignored_not_fatal():
    """上游哪天多传一个字段，这边不该报错。"""
    got = cc._parse_affect('{"n": 3, "brand_new_field": [1,2,3]}')
    assert got == {"n": 3}


def test_partial_payload_keeps_what_it_can():
    """少传一个字段，不该整块丢掉。"""
    assert cc._parse_affect('{"n": 3, "mean": 4}') == {"n": 3, "mean": 4.0}


def test_moods_are_capped_so_upstream_cannot_bloat_the_trace():
    big = {"moods": {f"m{i}": 1 for i in range(50)}, "n": 50}
    import json
    got = cc._parse_affect(json.dumps(big))
    assert len(got["moods"]) == 12


def test_bad_mood_counts_are_dropped_not_fatal():
    got = cc._parse_affect('{"n": 2, "moods": {"warm": "五", "ache": 2}}')
    assert got["moods"] == {"ache": 2}
