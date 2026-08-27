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
