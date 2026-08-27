# ============================================================
# Regression: mood may heal, the record beneath it may not
# 回归测试：心情可以自愈，它底下那本账不可以
#
# It is tempting to read DAMPING / DRIVE_BASELINES as the thing standing
# between this system and wear, and to rip them out. That would be wrong. A
# mood returning to rest is correct; a mood that never settled would be a
# pathology, not a person.
# 很容易把 DAMPING / DRIVE_BASELINES 当成挡在「磨损」前面的东西，一把拆掉。
# 那是错的。心情回落到静息是对的；一个永不平复的情绪不是人，是病理。
#
# The actual gap is that nothing accumulates underneath the healing:
# 真正的缺口是自愈**底下**没有东西在累积：
#
#   1. History one layer down was still being rewritten in place — a thought
#      recorded under "disgust" came back saying "reflection", with nothing
#      anywhere recording it had ever said "disgust".
#      下面一层的历史仍然在被原地改写 —— 一个以 "disgust" 记下的念头
#      回来变成了 "reflection"，而且哪里都没记着它曾经说过 "disgust"。
#
#   2. DRIVE_BASELINES are constants. The resting state after two years is
#      identical to day one. That is precisely the absence of wear.
#      DRIVE_BASELINES 是常量。两年后的静息状态和第一天一模一样。
#      这正好就是「没有磨损」。
# ============================================================

import json
import sqlite3
import time

import pytest

import desire_engine as de
from desire_engine import DesireStore, DRIVE_BASELINES, DRIVE_KEYS


# ---------------------------------------------------------
# Reading an old row vs. accepting a new event
# ---------------------------------------------------------

def test_an_incoming_event_still_cannot_claim_discernment():
    """discernment is a modifier (brain.discernment_alarm), not a drive. This
    is live behaviour and the legacy read map must not have moved it.
    discernment 是修饰符，不是驱动。这是活的行为，兼容读取表不该动到它。"""
    assert de.normalize_drive_key("discernment") == ""
    assert de.normalize_drive_key("disgust") == ""
    assert "discernment" not in DRIVE_KEYS


def test_an_old_stored_row_still_reads():
    """The other question entirely: rows already written under old names."""
    assert de.read_stored_drive_key("discernment") == "reflection"
    assert de.read_stored_drive_key("disgust") == "reflection"
    assert de.read_stored_drive_key("duty") == "stewardship"
    assert de.read_stored_drive_key("curiosity") == "curiosity"


def test_the_two_maps_are_not_the_same_map():
    """If these ever merge, an event claiming "discernment" starts moving a
    drive value — which is the bug this split exists to prevent."""
    assert de.DRIVE_ALIASES != de.LEGACY_STORED_DRIVE_NAMES
    assert "discernment" not in de.DRIVE_ALIASES


def test_startup_does_not_rewrite_stored_drive_names(tmp_path):
    """The destructive migration ran on every startup, wrapped in
    `except: pass`. A thought recorded under "disgust" came back saying
    "reflection" with no trace it had ever said otherwise — the same
    overwrite Phase 1 removed from memory buckets, one layer down.
    那段破坏性迁移每次启动都跑，外面裹着 `except: pass`。
    以 "disgust" 记下的念头回来变成 "reflection"，没有任何痕迹说它曾经不是 ——
    跟 Phase 1 从记忆桶里拿掉的是同一种覆盖，只是活在下面一层。"""
    db = str(tmp_path / "d.db")
    store = DesireStore(db)

    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO thoughts (tid, text, drive, kind, strength, born_at, fed_count) "
            "VALUES ('t1','一个旧念头','disgust','flit',0.5,?,0)", (time.time(),))
        conn.execute("INSERT INTO refusals (drive_key, reason, ts) VALUES ('disgust','不想',?)",
                     (time.time(),))

    DesireStore(db)  # a second startup: this is where the rewrite used to run

    with sqlite3.connect(db) as conn:
        stored_thought = conn.execute("SELECT drive FROM thoughts WHERE tid='t1'").fetchone()[0]
        stored_refusal = conn.execute("SELECT drive_key FROM refusals").fetchone()[0]

    assert stored_thought == "disgust", "startup overwrote the word the row was written with"
    assert stored_refusal == "disgust"

    # and it is still readable as its current equivalent
    assert [t.drive for t in store.load_thoughts() if t.tid == "t1"] == ["reflection"]
    assert store.recent_refusals()[0]["drive_key"] == "reflection"


# ---------------------------------------------------------
# The resting log: raw material for a baseline that can move
# ---------------------------------------------------------

def _settled_state(store, idle_hours):
    state = store.load_state()
    state.last_user_message_at = time.time() - idle_hours * 3600
    return state


def test_baselines_are_still_constants_today(tmp_path):
    """Documents the boundary of this phase. Nothing here makes a resting
    point move yet — that needs lived evidence, and the evidence starts being
    collected now. If this ever fails, the drift landed and the test should be
    replaced by one that checks how it drifts.
    这条记录本阶段的边界。这里没有任何东西让静息点动起来 ——
    那需要活出来的证据，而证据从现在开始收集。
    这条哪天失败了，说明漂移落地了，那时该换成一条检查「它怎么漂」的测试。"""
    store = DesireStore(str(tmp_path / "d.db"))
    store.save_state(_settled_state(store, idle_hours=100))
    assert de.DRIVE_BASELINES == DRIVE_BASELINES
    assert all(isinstance(v, float) for v in DRIVE_BASELINES.values())


def test_a_settled_state_is_recorded(tmp_path):
    store = DesireStore(str(tmp_path / "d.db"))
    assert store.record_resting_observation(_settled_state(store, idle_hours=10)) is True
    rows = store.read_resting_observations()
    assert len(rows) == 1
    assert rows[0]["idle_seconds"] >= 10 * 3600 - 60


def test_a_busy_state_is_not_mistaken_for_a_resting_one(tmp_path):
    """Where the drives sit mid-conversation is not where they rest."""
    store = DesireStore(str(tmp_path / "d.db"))
    assert store.record_resting_observation(_settled_state(store, idle_hours=1)) is False
    assert store.read_resting_observations() == []


def test_one_quiet_stretch_is_not_sampled_over_and_over(tmp_path):
    store = DesireStore(str(tmp_path / "d.db"))
    state = _settled_state(store, idle_hours=50)
    assert store.record_resting_observation(state) is True
    assert store.record_resting_observation(state) is False
    assert len(store.read_resting_observations()) == 1


def test_an_observation_records_the_ruler_it_was_taken_with(tmp_path):
    """If the baselines ever move, an old observation must stay interpretable
    instead of silently becoming a measurement against a ruler nobody has any
    more. Same lesson as rubric_version in Phase 2.8.
    基线万一动了，一条旧观测要仍然读得懂，
    而不是悄悄变成「用一把谁也没有了的尺子量出来的数」。"""
    store = DesireStore(str(tmp_path / "d.db"))
    store.record_resting_observation(_settled_state(store, idle_hours=10))
    row = store.read_resting_observations()[0]
    assert row["baselines"] == DRIVE_BASELINES
    assert set(row["drives"]) >= set(DRIVE_BASELINES)


def test_the_resting_log_only_grows(tmp_path):
    store = DesireStore(str(tmp_path / "d.db"))
    now = time.time()
    for i in range(4):
        state = store.load_state()
        state.last_user_message_at = now + i * 86400 - 10 * 3600
        store.record_resting_observation(state, now=now + i * 86400)
    rows = store.read_resting_observations()
    assert len(rows) == 4
    assert [r["ts"] for r in rows] == sorted(r["ts"] for r in rows)


def test_observing_can_never_break_saving(tmp_path, monkeypatch):
    """The state is live behaviour; the resting log is evidence for a layer
    that does not exist yet. Evidence collection must not be able to take the
    behaviour down.
    状态是活的行为；静息账是给一个还不存在的层用的证据。
    收证据绝不能把行为搞挂。"""
    store = DesireStore(str(tmp_path / "d.db"))

    def boom(*a, **k):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(DesireStore, "record_resting_observation", boom)
    store.save_state(_settled_state(store, idle_hours=10))  # must not raise
    assert store.load_state() is not None
