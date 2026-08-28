# ============================================================
# Regression: recall reads, injection writes
# 回归测试：召回只读，注入才写
#
# Two contracts are locked down here.
#
# 1. Retrieval is not a touch. A memory that was scored and then cut from the
#    bundle was never remembered. If searching recorded touches, the system's
#    own housekeeping would slowly rewrite which memories look important.
#    被检索到不等于被触碰。打了分又被砍掉的记忆根本没被想起来。
#    如果检索就记账，系统的日常维护会慢慢改写「哪些记忆看起来重要」。
#
# 2. Involuntary and deliberate recall are different events. Something
#    surfacing on you is not the same as going back for it on purpose, and
#    they must not leave the same mark — that distinction is the only honest
#    raw material for texture later.
#    不由自主和刻意是两种事件。它自己浮上来，跟你特地回去找它，不一样，
#    不能留下同样的痕 —— 这个区分是将来质地唯一诚实的原料。
# ============================================================

from datetime import datetime, timedelta

import pytest

import recall
import recall_journal


NOW = datetime(2026, 8, 27, 12, 0, 0)


def _bucket(bid, content="内容", created=None, **meta):
    base = {
        "id": bid,
        "created": (created or NOW).isoformat(timespec="seconds"),
        "importance": 5,
        "arousal": 0.3,
        "domain": ["日常"],
        "tags": [],
    }
    base.update(meta)
    return {"id": bid, "content": content, "metadata": base}


# ---------------------------------------------------------
# Selection is deterministic and led by what is unfinished
# ---------------------------------------------------------

def test_selection_is_deterministic():
    """Same inputs, same past. The old sampler was random, so two wake-ups a
    second apart disagreed about what mattered."""
    buckets = [_bucket(f"b{i}", content=f"第 {i} 条") for i in range(20)]
    first = [r["id"] for r in recall.select(buckets, NOW, limit=7)]
    second = [r["id"] for r in recall.select(buckets, NOW, limit=7)]
    assert first == second
    assert len(first) == 7


def test_unfinished_outranks_merely_recent():
    """An open question from a while ago beats a settled note from today."""
    old_open = _bucket("open", content="这件事我还没想明白。",
                       created=NOW - timedelta(days=9), domain=["unresolved"])
    fresh_done = _bucket("done", content="今天顺手记一笔。",
                         created=NOW, resolved=True)
    picked = [r["id"] for r in recall.select([fresh_done, old_open], NOW, limit=2)]
    assert picked[0] == "open"


def test_resolved_scores_zero_unfinishedness():
    assert recall.unfinishedness(_bucket("x", resolved=True)) == 0.0


def test_query_pulls_topic_matches_up():
    about = _bucket("hit", content="我们聊过记忆连续性的问题。",
                    created=NOW - timedelta(days=5))
    other = _bucket("miss", content="今天煮了面，汤有点咸。", created=NOW)
    picked = [r["id"] for r in recall.select([other, about], NOW, query="记忆连续性", limit=2)]
    assert picked[0] == "hit"


def test_neglect_counterweights_recency():
    """Without this the same few recent memories surface every single time and
    the rest of the past goes dark while still sitting on disk."""
    now_b = _bucket("new", created=NOW)
    old_b = _bucket("old", created=NOW - timedelta(days=60))
    assert recall.neglect(old_b, NOW, None) > recall.neglect(now_b, NOW, None)
    # a memory touched recently is no longer neglected
    touched = recall.neglect(old_b, NOW, NOW - timedelta(days=1))
    assert touched < recall.neglect(old_b, NOW, None)


def test_every_item_can_say_why_it_was_chosen():
    """A recall you cannot interrogate is indistinguishable from one that made
    it up."""
    rows = recall.select([_bucket("b1")], NOW, limit=1)
    assert set(rows[0]["parts"]) == {
        "unfinished", "query", "salience", "recency", "neglect",
    }


# ---------------------------------------------------------
# Elapsed time is a fact, never an experience
# ---------------------------------------------------------

def test_elapsed_reports_duration_only():
    out = recall.describe_elapsed((NOW - timedelta(days=12)).isoformat(), NOW)
    assert out["known"] is True
    assert out["elapsed_seconds"] == 12 * 86400
    assert "12 天" in out["elapsed_phrase"]
    # nothing in the payload claims anything about those days
    blob = " ".join(str(v) for v in out.values())
    for invented in ("想", "等", "一直", "感觉", "wait", "miss"):
        assert invented not in blob


def test_elapsed_unknown_on_first_ever_encounter():
    out = recall.describe_elapsed("", NOW)
    assert out["known"] is False
    assert "now" in out


def test_bundle_states_it_wrote_nothing():
    bundle = recall.build_bundle(
        recall.select([_bucket("b1")], NOW, limit=1),
        recall.describe_elapsed("", NOW),
        recall.INVOLUNTARY,
        recall_id="r1",
    )
    assert bundle["read_only"] == {"wrote_anything": False, "touch_recorded": False}
    assert bundle["items"][0]["id"] == "b1"


def test_bundle_rejects_unknown_mode():
    with pytest.raises(ValueError):
        recall.build_bundle([], recall.describe_elapsed("", NOW), "vibes")


def test_formatted_bundle_carries_ids_back_to_the_agent():
    bundle = recall.build_bundle(
        recall.select([_bucket("abc123", content="一条记忆")], NOW, limit=1),
        recall.describe_elapsed("", NOW), recall.DELIBERATE, recall_id="r1",
    )
    text = recall.format_bundle(bundle)
    assert "id:abc123" in text, "注入的文本里没有 id，agent 回不去这条记忆"


# ---------------------------------------------------------
# The journal: only real touches, counted once, split by mode
# ---------------------------------------------------------

def test_scoring_alone_records_nothing(tmp_path):
    """Building a bundle must not write. This is the whole read-only claim."""
    d = str(tmp_path)
    recall.build_bundle(
        recall.select([_bucket("b1"), _bucket("b2")], NOW, limit=2),
        recall.describe_elapsed("", NOW), recall.INVOLUNTARY, recall_id="r1",
    )
    assert recall_journal.read_events(d) == []
    assert recall_journal.last_encounter(d) == ""


def test_touch_is_idempotent_on_recall_id(tmp_path):
    """A retried confirm from a flaky client must not make a memory look twice
    as touched as it was."""
    d = str(tmp_path)
    first = recall_journal.record_touch(d, "r1", recall.INVOLUNTARY, ["b1"])
    second = recall_journal.record_touch(d, "r1", recall.INVOLUNTARY, ["b1"])
    assert first["recorded"] is True
    assert second["recorded"] is False and second["duplicate"] is True
    assert len(recall_journal.read_events(d)) == 1
    assert recall_journal.touch_summary(d)["b1"]["involuntary"] == 1


def test_modes_are_counted_separately(tmp_path):
    """Surfacing on you and going back for it are not the same event."""
    d = str(tmp_path)
    recall_journal.record_touch(d, "r1", recall.INVOLUNTARY, ["b1"])
    recall_journal.record_touch(d, "r2", recall.DELIBERATE, ["b1"])
    recall_journal.record_touch(d, "r3", recall.DELIBERATE, ["b1"])
    summary = recall_journal.touch_summary(d)["b1"]
    assert summary["involuntary"] == 1
    assert summary["deliberate"] == 2
    assert summary["last_touch"]


def test_touch_requires_recall_id_and_valid_mode(tmp_path):
    d = str(tmp_path)
    with pytest.raises(ValueError):
        recall_journal.record_touch(d, "", recall.INVOLUNTARY, ["b1"])
    with pytest.raises(ValueError):
        recall_journal.record_touch(d, "r1", "vibes", ["b1"])
    assert recall_journal.read_events(d) == []


def test_touch_records_which_body_it_reached(tmp_path):
    """Once a phone, a terminal and a chat window share one memory, 'who was
    this said to' stops being pedantry."""
    d = str(tmp_path)
    recall_journal.record_touch(d, "r1", recall.INVOLUNTARY, ["b1"],
                                endpoint="chat-c", session_id="s-42")
    row = recall_journal.read_events(d)[0]
    assert row["endpoint"] == "chat-c"
    assert row["session_id"] == "s-42"


def test_encounter_advances_only_on_real_touch(tmp_path):
    d = str(tmp_path)
    assert recall_journal.last_encounter(d) == ""
    recall_journal.record_touch(d, "r1", recall.INVOLUNTARY, ["b1"])
    stamp = recall_journal.last_encounter(d)
    assert stamp
    # a duplicate confirm does not move time forward either
    recall_journal.record_touch(d, "r1", recall.INVOLUNTARY, ["b1"])
    assert recall_journal.last_encounter(d) == stamp


def test_journal_is_append_only_across_touches(tmp_path):
    d = str(tmp_path)
    for i in range(4):
        recall_journal.record_touch(d, f"r{i}", recall.DELIBERATE, [f"b{i}"])
    rows = recall_journal.read_events(d)
    assert [r["recall_id"] for r in rows] == ["r0", "r1", "r2", "r3"]


# ---------------------------------------------------------
# 3. How it arrived decides how it reads
#
# Both modes went through one renderer and `mode` was never read, so
# involuntary and deliberate recall produced byte-identical text. A past
# that arrives stamped with an exact date, a bucket id and a relevance
# score is a search result, whatever the mode field says.
# 两种模式共用一个渲染器，`mode` 从没被读过，
# 于是不由自主和刻意回想输出逐字节相同。
# 带着精确日期、bucket id 和相关分到达的过去，不管 mode 写的是什么，都是检索结果。
# ---------------------------------------------------------

def _bundle(mode, buckets, **kw):
    return recall.build_bundle(
        recall.select(buckets, NOW, limit=len(buckets)),
        recall.describe_elapsed("", NOW), mode, recall_id="r1", **kw)


def test_the_two_modes_no_longer_render_the_same():
    buckets = [_bucket("abc123", content="一条记忆")]
    a = recall.format_bundle(_bundle(recall.INVOLUNTARY, buckets), now=NOW)
    b = recall.format_bundle(_bundle(recall.DELIBERATE, buckets), now=NOW)
    assert a != b, "mode 又被忽略了——两种到达方式渲染成了同一段文字"


def test_involuntary_carries_no_ids_no_scores_no_exact_dates():
    """None of the filing apparatus. Nobody remembers something at 0.33."""
    buckets = [_bucket("abc123", content="一条记忆",
                       created=NOW - timedelta(days=9))]
    text = recall.format_bundle(_bundle(recall.INVOLUNTARY, buckets), now=NOW)
    assert "abc123" not in text
    assert "id:" not in text
    assert "0." not in text.split("[现在]")[-1].split("\n", 2)[-1]
    assert "2026-08-18" not in text
    assert "上个礼拜" in text
    assert "一条记忆" in text


def test_deliberate_still_carries_ids_back():
    """She went looking, so she gets the filing. This is the older contract."""
    buckets = [_bucket("abc123", content="一条记忆")]
    text = recall.format_bundle(_bundle(recall.DELIBERATE, buckets), now=NOW)
    assert "id:abc123" in text
    assert "[2026-08-27]" in text


def test_bundle_says_which_kind_each_item_is():
    """memory and feel come from one pool; without kind they are one blur."""
    bundle = _bundle(recall.DELIBERATE, [
        _bucket("m1", content="发生了什么"),
        _bucket("f1", content="当时什么感觉", type="feel"),
    ])
    kinds = {it["id"]: it["kind"] for it in bundle["items"]}
    assert kinds == {"m1": "memory", "f1": "feel"}


def test_feel_is_marked_in_deliberate_text():
    bundle = _bundle(recall.DELIBERATE,
                     [_bucket("f1", content="当时什么感觉", type="feel")])
    assert "当时的感觉" in recall.format_bundle(bundle, now=NOW)


def test_involuntary_leads_with_the_feel():
    """Something tightens first; only then does it come back what it was about."""
    bundle = _bundle(recall.INVOLUNTARY, [
        _bucket("m1", content="发生了什么", importance=10),
        _bucket("f1", content="当时什么感觉", type="feel", importance=1),
    ])
    text = recall.format_bundle(bundle, now=NOW)
    assert text.index("当时什么感觉") < text.index("发生了什么")


def test_coarse_when_never_invents_a_future():
    assert recall.coarse_when((NOW + timedelta(days=3)).isoformat(), NOW) == ""
    assert recall.coarse_when("", NOW) == ""
    assert recall.coarse_when("不是时间", NOW) == ""


def test_coarse_when_walks_one_direction_only():
    """A ladder that doubles back would place an older memory as more recent."""
    seen = []
    for d in (0, 1, 3, 10, 30, 90, 200, 500):
        seen.append(recall.coarse_when((NOW - timedelta(days=d)).isoformat(), NOW))
    assert all(seen), "某一档掉出了梯子"
    assert len(set(seen)) == len(seen), f"两个不同的距离说成了同一句：{seen}"


def test_read_only_claim_survives_both_renderings():
    for mode in (recall.INVOLUNTARY, recall.DELIBERATE):
        b = _bundle(mode, [_bucket("b1")])
        recall.format_bundle(b, now=NOW)
        assert b["read_only"] == {"wrote_anything": False, "touch_recorded": False}


# ---------------------------------------------------------
# 4. The involuntary rendering has to sound like one, all the way down
# ---------------------------------------------------------

def test_involuntary_header_is_not_a_timestamp():
    """An ISO timestamp on line one undoes every coarse date below it."""
    b = recall.build_bundle(
        recall.select([_bucket("b1")], NOW, limit=1),
        recall.describe_elapsed((NOW - timedelta(minutes=50)).isoformat(), NOW),
        recall.INVOLUNTARY, recall_id="r1")
    text = recall.format_bundle(b, now=NOW)
    assert "[现在]" not in text
    assert NOW.isoformat(timespec="seconds") not in text
    assert "50 分钟" in text, "过去了多久是事实，不能因为换语气就丢掉"


def test_deliberate_header_keeps_the_exact_clock():
    b = recall.build_bundle(
        recall.select([_bucket("b1")], NOW, limit=1),
        recall.describe_elapsed((NOW - timedelta(minutes=50)).isoformat(), NOW),
        recall.DELIBERATE, recall_id="r1")
    assert "[现在]" in recall.format_bundle(b, now=NOW)


def test_adjacent_repeats_say_when_only_once():
    """Two memories a day apart both land on the same phrase; starting two
    paragraphs with the same three characters reads like a stuck tape."""
    b = recall.build_bundle(
        recall.select([
            _bucket("a", content="第一件", created=NOW - timedelta(days=40)),
            _bucket("b", content="第二件", created=NOW - timedelta(days=41)),
        ], NOW, limit=2),
        recall.describe_elapsed("", NOW), recall.INVOLUNTARY, recall_id="r1")
    text = recall.format_bundle(b, now=NOW)
    assert text.count("上个月") == 1, f"时间词重复了：\n{text}"
    assert "第一件" in text and "第二件" in text


def test_a_new_time_phrase_is_still_spoken():
    """Suppressing repeats must not suppress an actual change of era."""
    b = recall.build_bundle(
        recall.select([
            _bucket("a", content="近的", created=NOW),
            _bucket("b", content="远的", created=NOW - timedelta(days=200)),
        ], NOW, limit=2),
        recall.describe_elapsed("", NOW), recall.INVOLUNTARY, recall_id="r1")
    text = recall.format_bundle(b, now=NOW)
    assert "今天" in text and "大半年前" in text


def test_neither_ending_reads_like_a_disclaimer_stapled_on():
    """The claim survives in both; only the register changes."""
    inv = recall.format_bundle(_bundle(recall.INVOLUNTARY, [_bucket("b1")]), now=NOW)
    dlb = recall.format_bundle(_bundle(recall.DELIBERATE, [_bucket("b1")]), now=NOW)
    assert "以上是证据，不是结论" not in inv
    assert inv.rstrip().endswith("你有选择相信的权利。")
    assert dlb.rstrip().endswith("你有选择相信的权利。")
    # Both still hand over the same permission. A past that arrives as a
    # verdict has told the agent who it is — the one thing this must not do.
    # 两边交出的是同一个许可。一段作为判决到达的过去等于告诉了它自己是谁 ——
    # 那正是这里绝不能做的事。
    for text in (inv, dlb):
        assert "你有选择相信的权利" in text
