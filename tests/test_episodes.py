# ============================================================
# Regression: an episode is a stretch of time, not a topic
# 回归测试：一段经历是一段时间，不是一个话题
#
# Episodes sit between SOURCE and INTERPRETATION, and that position is the
# whole constraint. Two rules follow from it, and both are easy to break by
# accident later:
# 经历段夹在 SOURCE 和 INTERPRETATION 中间，这个位置就是全部的约束。
# 由它推出两条规矩，而且两条将来都很容易在无意中被破坏：
#
# 1. Cuts are structural. If an analyzer ever gets to say "these turns belong
#    together because they're about the same thing", the episode becomes an
#    interpretation — and then an interpretation sits below INTERPRETATION,
#    and every layer above inherits an unmarked judgement.
#    切分是结构性的。一旦让分析器来说「这几轮该在一起，因为在聊同一件事」，
#    episode 就变成了解释 —— 于是解释坐在了 INTERPRETATION 下面，
#    上面每一层都继承了一个没被标记的判断。
#
# 2. An episode never claims closure. Structure knows a stretch was cut off by
#    silence. It does not know anything was settled.
#    经历段绝不声称了结。结构知道一段被沉默截断了。它不知道有什么事情了结了。
# ============================================================

from datetime import datetime, timedelta

import pytest

import episode
import source_log


T0 = datetime(2026, 8, 1, 10, 0, 0)


def _u(uid, minutes, endpoint="", text="话"):
    return {
        "utterance_id": uid,
        "role": "user",
        "text": text,
        "ts": (T0 + timedelta(minutes=minutes)).isoformat(timespec="seconds"),
        "endpoint": endpoint,
    }


# ---------------------------------------------------------
# Where the cuts fall
# ---------------------------------------------------------

def test_a_silence_ends_a_stretch():
    eps = episode.segment([_u("a", 0), _u("b", 5), _u("c", 200), _u("d", 205)],
                          gap_seconds=30 * 60, now=T0 + timedelta(minutes=206))
    assert len(eps) == 2
    assert [e["utterance_ids"] for e in eps] == [["a", "b"], ["c", "d"]]
    assert eps[0]["ended_by"] == episode.CUT_GAP


def test_continuous_talk_stays_one_stretch():
    eps = episode.segment([_u(c, i * 5) for i, c in enumerate("abcdef")],
                          gap_seconds=30 * 60, now=T0 + timedelta(minutes=26))
    assert len(eps) == 1
    assert eps[0]["utterance_count"] == 6


def test_switching_endpoint_ends_a_stretch():
    """The same minute on a phone and in a terminal is not one stretch — this
    is the multi-endpoint case the whole project exists for."""
    eps = episode.segment([_u("a", 0, "chat-c"), _u("b", 1, "chat-c"),
                           _u("c", 2, "cli")],
                          now=T0 + timedelta(minutes=3))
    assert len(eps) == 2
    assert eps[0]["ended_by"] == episode.CUT_ENDPOINT
    assert eps[1]["endpoint"] == "cli"


def test_topic_change_does_not_cut():
    """The rule that keeps this layer beneath INTERPRETATION. Nothing about
    what was said may move a boundary."""
    eps = episode.segment(
        [_u("a", 0, text="我们聊聊记忆库的架构"),
         _u("b", 2, text="晚饭吃什么"),
         _u("c", 4, text="猫又把杯子推下去了")],
        now=T0 + timedelta(minutes=5))
    assert len(eps) == 1, "an episode boundary moved because the subject changed"


def test_segmentation_is_deterministic():
    stream = [_u("a", 0), _u("b", 5), _u("c", 200)]
    now = T0 + timedelta(minutes=201)
    assert episode.segment(stream, now=now) == episode.segment(stream, now=now)


def test_untimestamped_utterances_are_kept_not_dropped():
    """Losing a real utterance to tidy up the timeline is not a trade this
    layer gets to make."""
    stream = [_u("a", 0), {"utterance_id": "b", "role": "user", "text": "没时间戳"},
              _u("c", 3)]
    eps = episode.segment(stream, now=T0 + timedelta(minutes=4))
    assert [u for e in eps for u in e["utterance_ids"]] == ["a", "b", "c"]


# ---------------------------------------------------------
# What an episode is allowed to say about itself
# ---------------------------------------------------------

def test_an_episode_never_claims_it_was_resolved():
    eps = episode.segment([_u("a", 0), _u("b", 5)], now=T0 + timedelta(days=3))
    ep = eps[0]
    assert ep["ended_by"] in episode.VALID_CUTS
    # every reason a stretch can end is structural; none of them mean settled
    blob = " ".join(str(v) for v in ep.values())
    for judgement in ("resolved", "finished", "settled", "了结", "完成", "解决"):
        assert judgement not in blob


def test_an_open_stretch_is_not_reported_as_over():
    """Running out of source is not the same as the conversation ending."""
    eps = episode.segment([_u("a", 0), _u("b", 1)], gap_seconds=30 * 60,
                          now=T0 + timedelta(minutes=2))
    assert eps[-1]["ended_by"] == episode.CUT_OPEN


def test_a_long_silence_after_the_last_turn_is_reported_as_silence():
    eps = episode.segment([_u("a", 0), _u("b", 1)], gap_seconds=30 * 60,
                          now=T0 + timedelta(days=2))
    assert eps[-1]["ended_by"] == episode.CUT_GAP
    assert eps[-1]["silence_since_seconds"] >= 2 * 86400 - 120


def test_gap_before_measures_the_silence_between_stretches():
    """The number a texture layer will want: coming back after an hour and
    coming back after a month are not the same return."""
    eps = episode.segment([_u("a", 0), _u("b", 5), _u("c", 5 + 60 * 24 * 7)],
                          now=T0 + timedelta(days=8))
    assert eps[0]["gap_before_seconds"] is None  # nothing came before the first
    assert eps[1]["gap_before_seconds"] == 7 * 86400


def test_first_stretch_does_not_invent_a_silence_before_it():
    eps = episode.segment([_u("a", 0)], now=T0 + timedelta(minutes=1))
    assert eps[0]["gap_before_seconds"] is None


def test_new_stretch_does_not_measure_against_the_previous_one():
    """A fresh stretch's internal timing must not be judged against the tail of
    the one before it."""
    eps = episode.segment([_u("a", 0, "cli"), _u("b", 1, "chat-c"), _u("c", 2, "chat-c")],
                          gap_seconds=30 * 60, now=T0 + timedelta(minutes=3))
    assert len(eps) == 2
    assert eps[1]["utterance_ids"] == ["b", "c"]


# ---------------------------------------------------------
# Identity has to survive the episode still happening
# ---------------------------------------------------------

def test_id_is_stable_while_the_stretch_is_still_growing():
    """An interpretation written mid-conversation must still be able to name
    the episode it was about once the conversation ends.
    对话进行到一半写下的解释，等对话结束后仍然要叫得出它讲的是哪一段。"""
    mid = episode.segment([_u("a", 0), _u("b", 2)], now=T0 + timedelta(minutes=3))
    later = episode.segment([_u("a", 0), _u("b", 2), _u("c", 4), _u("d", 6)],
                            now=T0 + timedelta(minutes=7))
    assert mid[0]["episode_id"] == later[0]["episode_id"]
    assert later[0]["utterance_count"] == 4


def test_different_stretches_get_different_ids():
    eps = episode.segment([_u("a", 0), _u("b", 500)], now=T0 + timedelta(minutes=501))
    assert eps[0]["episode_id"] != eps[1]["episode_id"]


def test_episode_of_finds_the_stretch_an_utterance_belongs_to():
    eps = episode.segment([_u("a", 0), _u("b", 500)], now=T0 + timedelta(minutes=501))
    assert episode.episode_of(eps, "b")["episode_id"] == eps[1]["episode_id"]
    assert episode.episode_of(eps, "nope") is None


# ---------------------------------------------------------
# Derived, never authoritative
# ---------------------------------------------------------

def test_rebuild_reads_source_and_writes_nothing(tmp_path):
    """Episodes are a function of the source log. Persisting them would create
    a second authority that can drift from the first."""
    d = str(tmp_path)
    source_log.record(d, [{"role": "user", "text": "第一句", "ts": T0.isoformat()},
                          {"role": "assistant", "text": "第二句",
                           "ts": (T0 + timedelta(minutes=1)).isoformat()}])
    before = sorted(p.name for p in tmp_path.iterdir())
    eps = episode.rebuild(d)
    assert len(eps) == 1 and eps[0]["utterance_count"] == 2
    assert sorted(p.name for p in tmp_path.iterdir()) == before, \
        "rebuilding episodes wrote something"


def test_rebuild_is_reproducible_from_source_alone(tmp_path):
    d = str(tmp_path)
    for i in range(4):
        source_log.record(d, [{"role": "user", "text": f"第 {i} 句",
                               "ts": (T0 + timedelta(minutes=i)).isoformat()}])
    now = T0 + timedelta(minutes=5)
    assert episode.rebuild(d, now=now) == episode.rebuild(d, now=now)


def test_changing_the_threshold_is_a_different_view_not_a_rewrite(tmp_path):
    """Re-cutting with a different gap yields a different segmentation without
    destroying anything — the source is untouched either way."""
    d = str(tmp_path)
    stamps = [0, 5, 45, 50]
    source_log.record(d, [{"role": "user", "text": f"t{m}",
                           "ts": (T0 + timedelta(minutes=m)).isoformat()}
                          for m in stamps])
    now = T0 + timedelta(minutes=51)
    coarse = episode.rebuild(d, gap_seconds=60 * 60, now=now)
    fine = episode.rebuild(d, gap_seconds=10 * 60, now=now)
    assert len(coarse) == 1 and len(fine) == 2
    assert len(source_log.read_all(d)) == 4


# ---------------------------------------------------------
# Time is stated as time
# ---------------------------------------------------------

@pytest.mark.parametrize("seconds,expected", [
    (None, "未知"), (30, "30 秒"), (300, "5 分钟"),
    (7200, "2 小时"), (86400 * 3, "3 天"),
])
def test_describe_gap_states_duration_only(seconds, expected):
    assert episode.describe_gap(seconds) == expected


def test_describe_gap_never_says_what_the_silence_was_like():
    """The system knows how long the silence was. It does not know what that
    was like, and must never say."""
    for s in (0, 90, 3600, 86400 * 40):
        out = episode.describe_gap(s)
        for invented in ("想", "等", "久违", "终于", "一直", "wait", "miss", "long"):
            assert invented not in out


def test_a_silence_is_not_hidden_behind_an_endpoint_switch():
    """When a stretch went quiet for days and then resumed somewhere else,
    reporting only "endpoint_change" hides the days. The length of a silence is
    the more consequential of the two facts.
    如果一段安静了好几天、然后在别处接上，只报「换端」会把那几天藏起来。
    两个事实里，沉默有多长是更要紧的那个。"""
    eps = episode.segment(
        [_u("a", 0, "chat-c"), _u("b", 2, "chat-c"),
         _u("c", 60 * 24 * 6, "cli")],
        gap_seconds=30 * 60, now=T0 + timedelta(days=6, minutes=5))
    assert len(eps) == 2
    assert eps[0]["ended_by"] == episode.CUT_GAP, "six days of silence reported as a device switch"
    assert eps[1]["gap_before_seconds"] >= 5 * 86400
    assert eps[1]["endpoint"] == "cli"  # the switch is still visible


# ---------------------------------------------------------
# An undeclared endpoint is missing information, not a body
# ---------------------------------------------------------

def test_an_undeclared_endpoint_does_not_manufacture_a_boundary():
    """Cutting on unknown->chat-c would build a boundary out of a gap in the
    records rather than out of anything that happened.
    在 unknown->chat-c 上切段，是拿记录里的一个空洞、而不是真发生过的事，造出边界。"""
    eps = episode.segment(
        [_u("a", 0, "unknown"), _u("b", 1, "chat-c"), _u("c", 2, "unknown")],
        gap_seconds=30 * 60, now=T0 + timedelta(minutes=3))
    assert len(eps) == 1, "a boundary appeared where only information was missing"


def test_two_known_bodies_still_cut():
    eps = episode.segment([_u("a", 0, "chat-c"), _u("b", 1, "cli")],
                          gap_seconds=30 * 60, now=T0 + timedelta(minutes=2))
    assert len(eps) == 2
    assert eps[0]["ended_by"] == episode.CUT_ENDPOINT


def test_empty_endpoint_is_treated_like_unknown():
    eps = episode.segment([_u("a", 0, ""), _u("b", 1, "cli"), _u("c", 2, "")],
                          gap_seconds=30 * 60, now=T0 + timedelta(minutes=3))
    assert len(eps) == 1
