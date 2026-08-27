# ============================================================
# Regression: at this layer, the revision IS the content
# 回归测试：在这一层，修订本身就是内容
#
# Lower down, history matters as evidence — you keep the old wording so a
# later claim can be checked against it. Here it matters as the subject.
# 再往下，历史的意义是证据 —— 留着旧措辞，好让后来的说法可以拿回去对。
# 在这里，历史的意义是主题本身。
#
#     "我以为这是关于我不敢开口"
#     "我现在觉得这是关于我在等一个许可"
#
# How the story someone tells about themselves changed is not metadata about
# the story. It is the most consequential thing this layer records — and the
# store used to write it down as `revision: 2` and drop the earlier sentence.
# A counter that says something changed while keeping nothing of what changed
# is the same shape as a hash with no previous body.
# 一个人讲给自己听的故事是怎么变的，不是关于这个故事的元数据。
# 它是这一层记下的最要紧的东西 —— 而存储层以前把它写成 `revision: 2`，
# 然后把先前那句丢掉。一个「说变过了却什么都没留下」的计数器，
# 跟「只有 hash 没有旧正文」是同一个形状。
# ============================================================

import pytest

import narrative as nar


T1 = {"title": "开口这件事", "core_question": "我为什么不敢开口"}
T2 = {"title": "开口这件事", "core_question": "我是不是在等一个许可"}


def test_a_retelling_keeps_the_earlier_telling(tmp_path):
    d = str(tmp_path)
    nar.record(d, "fam_1", nar.RETOLD, was=T1, now=T2, revision=1)

    assert nar.was_ever_told_as(d, "fam_1", "我为什么不敢开口"), \
        "the thread changed its mind and nothing remembers what it used to say"
    rows = nar.history(d, "fam_1")
    assert rows[0]["was"] == T1 and rows[0]["now"] == T2


def test_tellings_read_oldest_first_with_the_current_one_last(tmp_path):
    d = str(tmp_path)
    nar.record(d, "fam_1", nar.RETOLD, was=T1, now=T2, revision=1)
    nar.record(d, "fam_1", nar.RETOLD, was=T2,
               now={"title": "开口这件事", "core_question": "许可是我自己给的"}, revision=2)

    told = nar.tellings(d, "fam_1", current={"title": "开口这件事",
                                             "core_question": "许可是我自己给的",
                                             "revision": 3})
    assert [t["core_question"] for t in told] == [
        "我为什么不敢开口", "我是不是在等一个许可", "许可是我自己给的",
    ]
    assert told[-1]["until"] == "", "the current telling was reported as ended"


def test_the_current_telling_is_passed_in_not_inferred(tmp_path):
    """The store is the authority on what a thread is now; this module is the
    authority only on what it was. Inferring "now" from the log would make two
    places disagree about the present.
    存储层才是「它现在是什么」的权威，这个模块只是「它曾经是什么」的权威。
    从账本推断「现在」会让两个地方对当下产生分歧。"""
    d = str(tmp_path)
    nar.record(d, "fam_1", nar.RETOLD, was=T1, now=T2, revision=1)
    assert len(nar.tellings(d, "fam_1")) == 1
    assert len(nar.tellings(d, "fam_1", current=T2)) == 2


def test_a_retired_thread_is_not_a_thread_that_never_existed(tmp_path):
    """delete used to remove the family outright. A thread that stopped being
    true is a different thing from one that was never held.
    delete 以前是直接把它删掉。一条不再成立的叙事线，
    跟一条从没被持有过的叙事线，是两回事。"""
    d = str(tmp_path)
    whole = dict(T1, id="fam_1", revision=4, members=[{"node_ref": "bucket:abc"}])
    nar.record(d, "fam_1", nar.RETIRED, was=whole, revision=4)

    row = nar.history(d, "fam_1")[0]
    assert row["kind"] == nar.RETIRED
    assert row["was"]["members"] == [{"node_ref": "bucket:abc"}], \
        "the thread was retired and its members went with it"
    assert nar.was_ever_told_as(d, "fam_1", "我为什么不敢开口")


def test_a_dropped_member_is_remembered_as_having_belonged(tmp_path):
    """A memory removed from a thread was still, for a while, part of that
    story. Nothing else records that it ever was."""
    d = str(tmp_path)
    nar.record(d, "fam_1", nar.MEMBER_DROPPED,
               was={"node_ref": "bucket:abc", "query": "开口"}, revision=2)
    gone = nar.dropped_members(d, "fam_1")
    assert gone == [{"node_ref": "bucket:abc", "query": "开口",
                     "dropped_at": gone[0]["dropped_at"]}]


def test_history_is_per_thread(tmp_path):
    d = str(tmp_path)
    nar.record(d, "fam_1", nar.RETOLD, was=T1, now=T2, revision=1)
    nar.record(d, "fam_2", nar.RETOLD, was=T2, now=T1, revision=1)
    assert len(nar.history(d, "fam_1")) == 1
    assert len(nar.history(d)) == 2
    assert nar.was_ever_told_as(d, "fam_2", "我为什么不敢开口") is False


def test_an_unknown_event_is_refused(tmp_path):
    with pytest.raises(ValueError):
        nar.record(str(tmp_path), "fam_1", "vibes", was=T1)


def test_a_reason_is_optional_but_recorded_when_given(tmp_path):
    """A body edit on a memory has to justify itself because memories are not
    supposed to change. A narrative is supposed to change; demanding a reason
    every time would make the natural act bureaucratic.
    改记忆正文必须自证，因为记忆本来就不该变。叙事本来就该变；
    每次都要求理由会把一件自然的事变成一道手续。"""
    d = str(tmp_path)
    nar.record(d, "fam_1", nar.RETOLD, was=T1, now=T2, revision=1)
    nar.record(d, "fam_1", nar.RETOLD, was=T2, now=T1, revision=2,
               actor="他", reason="又想了想")
    rows = nar.history(d, "fam_1")
    assert rows[0]["reason"] == "" and rows[0]["actor"] == ""
    assert rows[1]["reason"] == "又想了想" and rows[1]["actor"] == "他"


def test_every_entry_declares_its_authority(tmp_path):
    d = str(tmp_path)
    nar.record(d, "fam_1", nar.RETOLD, was=T1, now=T2, revision=1)
    assert nar.history(d, "fam_1")[0]["authority"] == nar.AUTHORITY
