"""磨损是不可逆累积:扛过就是扛过,后来放下也不会变成没扛过。"""
import json, os, tempfile
import wear


def _traces(*rows):
    d = tempfile.mkdtemp()
    for i, r in enumerate(rows):
        r.setdefault("window", f"2026-08-{i+1:02d}-1200")
        r.setdefault("timestamp", f"2026-08-{i+1:02d} 12:00")
        with open(os.path.join(d, f"trace-{r['window']}.json"), "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False)
    return d


def test_empty_is_not_an_error():
    p = wear.profile("/nonexistent")
    assert p["windows"] == 0 and p["elapsed_days"] is None
    assert wear.describe("/nonexistent") == ""


def test_carried_count_never_goes_down_when_it_is_dropped():
    """这是磨损的定义。扛了 3 个窗口,第 4 个不提了——
    windows_carried 必须还是 3,不能归零。"""
    d = _traces({"unresolved": "那件事"}, {"unresolved": "那件事"},
                {"unresolved": "那件事"}, {"unresolved": ""})
    u = wear.profile(d)["carried_unresolved"][0]
    assert u["windows_carried"] == 3, u
    assert u["longest_streak"] == 3, u
    assert u["still_open"] is False
    assert u["current_streak"] == 0


def test_still_open_is_the_only_field_that_falls():
    d = _traces({"unresolved": "甲"}, {"unresolved": ""}, {"unresolved": "甲"})
    u = wear.profile(d)["carried_unresolved"][0]
    assert u["still_open"] is True
    assert u["windows_carried"] == 2
    assert u["current_streak"] == 1      # 中断过,重新数
    assert u["longest_streak"] == 1


def test_a_feeling_seen_once_is_not_a_texture():
    d = _traces({"primary": "只此一次"}, {"primary": "别的"})
    assert wear.profile(d)["recurring_feelings"] == []


def test_a_feeling_that_keeps_coming_back_is():
    d = _traces({"primary": "疲惫"}, {"primary": "别的", "secondary": "疲惫"},
                {"primary": "疲惫"})
    r = wear.profile(d)["recurring_feelings"]
    assert r[0]["item"] == "疲惫" and r[0]["windows_carried"] == 3


def test_elapsed_is_a_fact_or_nothing():
    """时间跨度不许编。解析不了就是 None。"""
    d = _traces({"primary": "a", "timestamp": "坏掉的时间"},
                {"primary": "a", "timestamp": "也坏了"})
    assert wear.profile(d)["elapsed_days"] is None


def test_elapsed_measures_real_days():
    d = _traces({"primary": "a", "timestamp": "2026-08-01 12:00"},
                {"primary": "a", "timestamp": "2026-08-11 12:00"})
    assert abs(wear.profile(d)["elapsed_days"] - 10) < 0.01


def test_describe_says_nothing_before_anything_accrued():
    d = _traces({"primary": "第一次"})
    assert wear.describe(d) == ""


def test_describe_reports_the_long_open_thing():
    d = _traces({"unresolved": "要不要做 iOS"}, {"unresolved": "要不要做 iOS"},
                {"unresolved": "要不要做 iOS"})
    text = wear.describe(d)
    assert "要不要做 iOS" in text and "3" in text


def test_broken_trace_files_do_not_sink_the_rest():
    d = _traces({"primary": "好的"}, {"primary": "好的"})
    with open(os.path.join(d, "trace-broken.json"), "w") as f:
        f.write("{ not json")
    assert wear.profile(d)["windows"] == 2


def test_nothing_is_written_to_disk():
    """派生数字不落盘。"""
    d = _traces({"primary": "a", "unresolved": "b"}, {"primary": "a"})
    before = sorted(os.listdir(d))
    wear.profile(d); wear.describe(d)
    assert sorted(os.listdir(d)) == before


def test_same_minute_windows_are_ordered_by_time_not_filename():
    """trace-…1616.json 和 trace-…1616-2.json:按文件名排,前者排到后面
    ("-" < "."),于是最早的窗口被当成最新的,still_open 整个反过来。"""
    d = _traces({"window": "2026-08-27-1616", "unresolved": "旧的"},
                {"window": "2026-08-27-1616-2", "unresolved": "新的"})
    p = wear.profile(d)
    assert p["last_window"] == "2026-08-27-1616-2", p["last_window"]
    still = {u["item"] for u in p["carried_unresolved"] if u["still_open"]}
    assert still == {"新的"}, still


def test_tenth_window_of_a_minute_sorts_after_the_second():
    """字符串排会把 -10 排在 -2 前面。"""
    ids = ["2026-08-27-1616-2", "2026-08-27-1616-10"]
    assert sorted(ids, key=wear.order_key) == ["2026-08-27-1616-2", "2026-08-27-1616-10"]


# ---- 两个强度（Bjork & Bjork）--------------------------------------------

def _at(day, **kw):
    """在第 day 天关的一个窗口。"""
    return dict(window=f"2026-08-{day:02d}-1200",
                timestamp=f"2026-08-{day:02d} 12:00", **kw)


def test_storage_never_falls_but_retrieval_does():
    """存储强度只增不减,提取强度会掉——这就是把一个数拆成两个的全部理由。"""
    d = _traces(_at(1, primary="暖"), _at(2, primary="暖"), _at(28, primary="别的"))
    f = [x for x in wear.profile(d)["recurring_feelings"] if x["item"] == "暖"][0]
    assert f["storage"] > 0
    assert f["retrieval"] < 0.2, f      # 26 天没出现,已经浮不上来
    assert f["windows_carried"] == 2


def test_spacing_beats_massing_at_equal_count():
    """次数一样,间隔开的那个存得更深。

    这是间隔效应,也是本次改动最该被证伪的一条:如果它不成立,
    那两个强度就只是把一个数写成了两个。
    """
    massed = _traces(_at(1, primary="暖"), _at(2, primary="暖"), _at(3, primary="暖"))
    spaced = _traces(_at(1, primary="暖"), _at(11, primary="暖"), _at(21, primary="暖"))
    def st(d):
        return [x for x in wear.profile(d)["recurring_feelings"]
                if x["item"] == "暖"][0]["storage"]
    assert st(spaced) > st(massed), (st(spaced), st(massed))


def test_deeper_things_are_forgotten_slower():
    """存储强度减缓遗忘:同样空 20 天,存得深的提取强度剩得多。"""
    shallow = _traces(_at(1, primary="暖"), _at(2, primary="暖"), _at(25, primary="X"))
    deep = _traces(_at(1, primary="暖"), _at(5, primary="暖"), )
    # deep 再加几次有间隔的
    import json, os
    for day in (10, 15, 20):
        w = f"2026-08-{day:02d}-1200"
        with open(os.path.join(deep, f"trace-{w}.json"), "w", encoding="utf-8") as fh:
            json.dump({"window": w, "timestamp": f"2026-08-{day:02d} 12:00",
                       "primary": "暖"}, fh, ensure_ascii=False)
    w = "2026-08-25-1200"
    with open(os.path.join(deep, f"trace-{w}.json"), "w", encoding="utf-8") as fh:
        json.dump({"window": w, "timestamp": "2026-08-25 12:00", "primary": "X"},
                  fh, ensure_ascii=False)
    def get(d):
        return [x for x in wear.profile(d)["recurring_feelings"]
                if x["item"] == "暖"][0]
    a, b = get(shallow), get(deep)
    assert b["storage"] > a["storage"]
    assert b["retrieval"] > a["retrieval"]


def test_everything_decays_to_the_same_instant():
    """两个词必须衰减到同一个时刻才可比——各停在自己最后一次出现那天的话,
    久没出现的会显得跟刚说过的一样强。"""
    d = _traces(_at(1, primary="旧", secondary="新"), _at(2, primary="旧"),
                _at(20, primary="新"), _at(21, primary="新"))
    p = {x["item"]: x for x in wear.profile(d)["recurring_feelings"]}
    assert p["新"]["retrieval"] > p["旧"]["retrieval"]
    assert p["旧"]["days_since"] > p["新"]["days_since"]


def test_describe_names_the_sunk_thing():
    # 隔开地出现三次(存得深),然后整整一个月不再提(浮不上来)。
    d = _traces(_at(1, primary="暖"), _at(11, primary="暖"), _at(21, primary="暖"),
                _at(28, primary="别的"), _at(29, primary="别的"))
    import json, os
    for day, w in ((25, "2026-09-25-1200"), (26, "2026-09-26-1200")):
        with open(os.path.join(d, f"trace-{w}.json"), "w", encoding="utf-8") as fh:
            json.dump({"window": w, "timestamp": f"2026-09-{day} 12:00",
                       "primary": "别的"}, fh, ensure_ascii=False)
    text = wear.describe(d)
    assert "沉下去、但没有变淡的" in text and "暖" in text, text


def test_unparseable_timestamps_still_produce_strengths():
    """早期脏数据不能让整套强度算不出来。"""
    d = _traces({"primary": "暖", "timestamp": "坏掉的"},
                {"primary": "暖", "timestamp": ""})
    f = wear.profile(d)["recurring_feelings"][0]
    assert f["storage"] > 0 and 0.0 <= f["retrieval"] <= 1.0
