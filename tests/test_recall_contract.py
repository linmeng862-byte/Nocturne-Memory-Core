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
