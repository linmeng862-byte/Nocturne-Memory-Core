# ============================================================
# Regression: a reading is held, not stored
# 回归测试：一个读法是被「持有」的，不是被存下来的
#
# The marks were already append-only, which is the hard part and it was
# already right. What they lacked was a spine.
# marks 本来就是只增的 —— 难的那部分早就做对了。缺的是骨头。
#
# 1. A mark is a fact about a reader, not about the memory. Without an actor,
#    two readers disagreeing looks like one reader contradicting themselves.
#    一条 mark 是关于**读者**的事实，不是关于那条记忆的。没有 actor，
#    两个读者不一致会看起来像一个读者自相矛盾。
#
# 2. Demotion deletes "inner" from the memory's domain list, which erases that
#    it was ever promoted. Reinterpretation must be append-only: a reversal is
#    a new entry, never a deletion.
#    降级把 "inner" 从 domain 列表里删掉，抹掉了「它曾经被升上去过」。
#    重新解释必须只增：推翻是一条新记录，绝不是一次删除。
#
# 3. The standing reading is recomputed from the marks. If it were the stored
#    thing, reversing it would destroy the fact that it was ever held.
#    当前读法是从 marks 重算的。如果被存下来的是它，
#    推翻它就会毁掉「它曾经被持有过」这个事实。
# ============================================================

from datetime import datetime, timedelta

import pytest

import interpretation as itp


D0 = datetime(2026, 8, 1, 12, 0, 0)


def _mark(kind, day=0, hour=0, actor="", endpoint=""):
    return {
        "mark": kind,
        "timestamp": (D0 + timedelta(days=day, hours=hour)).isoformat(timespec="seconds"),
        "actor": actor,
        "endpoint": endpoint,
        "note": "",
    }


# ---------------------------------------------------------
# The standing reading
# ---------------------------------------------------------

def test_nothing_marked_is_undecided_not_rejected():
    """Silence is not a "no". Treating an unmarked memory as rejected would
    invent a reading nobody ever held.
    沉默不是「不认」。把没标过的记忆当成被否定的，等于凭空造出一个没人持有过的读法。"""
    assert itp.standing_reading([])["reading"] == itp.READING_UNDECIDED


def test_recognition_across_days_is_what_counts():
    """Recognising something on three separate days is a different event from
    recognising it three times in one sitting, and only the first is evidence
    of it holding up over time."""
    one_sitting = [_mark(itp.RECOGNIZED, hour=h) for h in range(3)]
    assert itp.standing_reading(one_sitting)["reading"] == itp.READING_UNDECIDED

    across_days = [_mark(itp.RECOGNIZED, day=d) for d in range(3)]
    assert itp.standing_reading(across_days)["reading"] == itp.READING_INNER


def test_rejection_outweighs_recognition():
    rows = [_mark(itp.RECOGNIZED, day=d) for d in range(3)]
    rows += [_mark(itp.REJECTED, day=5), _mark(itp.REJECTED, day=6)]
    assert itp.standing_reading(rows)["reading"] == itp.READING_NOT_INNER


def test_suspending_judgement_is_not_a_vote_either_way():
    rows = [_mark(itp.SUSPENDED, day=d) for d in range(5)]
    r = itp.standing_reading(rows)
    assert r["reading"] == itp.READING_UNDECIDED
    assert r["counts"][itp.SUSPENDED] == 5


def test_a_reading_explains_itself():
    """A reading nobody can interrogate is indistinguishable from one that was
    made up."""
    r = itp.standing_reading([_mark(itp.RECOGNIZED, day=d) for d in range(3)])
    assert r["counts"][itp.RECOGNIZED] == 3
    assert len(r["recognition_dates"]) == 3
    assert r["crossed_dates"] is True
    assert r["thresholds"]["recognition"] == itp.RECOGNITION_THRESHOLD


def test_reading_is_deterministic():
    rows = [_mark(itp.RECOGNIZED, day=d) for d in range(3)] + [_mark(itp.SUSPENDED, day=9)]
    assert itp.standing_reading(rows) == itp.standing_reading(rows)


def test_unknown_marks_are_ignored_not_counted_as_agreement():
    rows = [_mark("whatever", day=d) for d in range(9)]
    r = itp.standing_reading(rows)
    assert r["reading"] == itp.READING_UNDECIDED
    assert sum(r["counts"].values()) == 0


# ---------------------------------------------------------
# Reversal is a new entry, never a deletion
# ---------------------------------------------------------

def test_a_reversal_does_not_erase_the_earlier_reading(tmp_path):
    """The question the mutable domain flag cannot answer: it only knows now.
    那个可变的 domain 标记回答不了的问题：它只知道现在。"""
    d = str(tmp_path)
    basis = itp.standing_reading([_mark(itp.RECOGNIZED, day=i) for i in range(3)])

    itp.record_transition(d, "b1", was=itp.READING_NOT_INNER,
                          now_reading=itp.READING_INNER, basis=basis, actor="他")
    itp.record_transition(d, "b1", was=itp.READING_INNER,
                          now_reading=itp.READING_NOT_INNER, basis=basis, actor="她")

    assert itp.was_ever(d, "b1", itp.READING_INNER), \
        "the memory was promoted and the record does not know it"
    rows = itp.history(d, "b1")
    assert [r["now"] for r in rows] == [itp.READING_INNER, itp.READING_NOT_INNER]


def test_a_transition_records_who_and_from_where(tmp_path):
    d = str(tmp_path)
    itp.record_transition(d, "b1", was=itp.READING_UNDECIDED,
                          now_reading=itp.READING_INNER, basis={}, actor="他",
                          endpoint="cli")
    row = itp.history(d, "b1")[0]
    assert row["actor"] == "他" and row["endpoint"] == "cli"
    assert row["authority"] == itp.AUTHORITY


def test_a_transition_carries_the_evidence_that_justified_it(tmp_path):
    """Recording that a reading changed without recording what changed it
    leaves a verdict with no case behind it.
    只记「读法变了」而不记「凭什么变的」，留下的是一个没有案卷的判决。"""
    d = str(tmp_path)
    basis = itp.standing_reading([_mark(itp.RECOGNIZED, day=i) for i in range(3)])
    itp.record_transition(d, "b1", was=itp.READING_UNDECIDED,
                          now_reading=itp.READING_INNER, basis=basis)
    row = itp.history(d, "b1")[0]
    assert row["basis"]["counts"][itp.RECOGNIZED] == 3
    assert row["basis"]["thresholds"]["recognition"] == itp.RECOGNITION_THRESHOLD


def test_history_is_per_memory_and_append_only(tmp_path):
    d = str(tmp_path)
    for i in range(3):
        itp.record_transition(d, "b1", was="", now_reading=itp.READING_INNER, basis={})
    itp.record_transition(d, "b2", was="", now_reading=itp.READING_NOT_INNER, basis={})

    assert len(itp.history(d, "b1")) == 3
    assert len(itp.history(d, "b2")) == 1
    assert len(itp.history(d)) == 4
    assert itp.was_ever(d, "b2", itp.READING_INNER) is False


def test_two_readers_disagreeing_is_not_a_contradiction(tmp_path):
    """Recognition is a fact about a reader, not about the memory. Both
    records are true.
    「认」是关于读者的事实，不是关于那条记忆的。两条记录都为真。"""
    rows = [_mark(itp.RECOGNIZED, day=d, actor="他") for d in range(3)]
    rows += [_mark(itp.REJECTED, day=d, actor="她") for d in (4, 5)]

    by_him = itp.standing_reading([r for r in rows if r["actor"] == "他"])
    by_her = itp.standing_reading([r for r in rows if r["actor"] == "她"])
    assert by_him["reading"] == itp.READING_INNER
    assert by_her["reading"] == itp.READING_NOT_INNER
    # and both sets of marks survive intact
    assert len(rows) == 5


# ---------------------------------------------------------
# The valve: evidence produces the reading, never the reverse
# 阀门：证据产生读法，绝不反过来
# ---------------------------------------------------------

def test_a_memory_read_as_inner_before_this_layer_is_carried_forward(tmp_path):
    """Closing the valve must not silently demote promotions that predate the
    marks table. That would be a behaviour loss disguised as a refactor.
    封阀门不能静默降级掉早于 marks 表的升级。那是一次伪装成重构的行为丢失。"""
    d = str(tmp_path)
    assert itp.standing_reading([])["reading"] == itp.READING_UNDECIDED
    assert itp.standing_reading([], legacy_inner=True)["reading"] == itp.READING_INNER

    assert itp.grandfather_inner(d, "b1") is True
    assert itp.legacy_inner_ids(d) == {"b1"}


def test_grandfathering_happens_once(tmp_path):
    """Restarts are free; the log must not grow a row per boot."""
    d = str(tmp_path)
    assert itp.grandfather_inner(d, "b1") is True
    assert itp.grandfather_inner(d, "b1") is False
    assert itp.grandfather_inner(d, "b1") is False
    assert len(itp.history(d, "b1")) == 1


def test_a_carried_forward_reading_is_evidence_not_an_override(tmp_path):
    """It used to be that the stored field won: two rejections could not turn
    a memory around while the field still said inner. Carried-forward status
    must not reinstate that.
    以前是存下来的字段赢：只要字段还写着 inner，两次「不认」就翻不动它。
    带过来的状态不许把那个毛病重新装回去。"""
    rows = [_mark(itp.REJECTED, day=1), _mark(itp.REJECTED, day=2)]
    assert itp.standing_reading(rows, legacy_inner=True)["reading"] == itp.READING_NOT_INNER


def test_grandfathering_says_why(tmp_path):
    d = str(tmp_path)
    itp.grandfather_inner(d, "b1")
    row = itp.history(d, "b1")[0]
    assert row["actor"] == itp.LEGACY_ACTOR
    assert row["basis"]["source"] == "domain_field"


def test_a_direct_inner_mark_still_sets_the_reading():
    """Older marks set the reading outright instead of voting toward it."""
    assert itp.standing_reading([_mark(itp.MARK_INNER)])["reading"] == itp.READING_INNER
    assert itp.standing_reading(
        [_mark(itp.MARK_INNER), _mark(itp.MARK_REMOVE_INNER)]
    )["reading"] == itp.READING_NOT_INNER


def test_direct_marks_are_reported_separately_from_votes():
    """A reading set outright and a reading voted up are different provenance
    and must stay distinguishable.
    被直接指定的读法和被投票投上去的读法，来路不同，必须分得清。"""
    r = itp.standing_reading([_mark(itp.MARK_INNER), _mark(itp.RECOGNIZED, day=1)])
    assert r["direct"][itp.MARK_INNER] == 1
    assert r["counts"][itp.RECOGNIZED] == 1
