# ============================================================
# Regression: a fact and a reading of a fact are not the same row
# 回归测试：事实，和对事实的一种读法，不是同一行
#
# The residue ledger used to store the utterances and the drive classification
# derived from them in one row, at one level, with no boundary — messages
# beside primary_drive beside territorial_alarm. A later layer reading that row
# cannot tell which half is what was said and which half is what somebody made
# of it. Promoting that row wholesale to SOURCE would have promoted the reading
# along with the fact.
# 残留账原本把话语、和从话语推出来的 drive 判断存在同一行、同一层级、中间没有边界 ——
# messages 挨着 primary_drive 挨着 territorial_alarm。
# 后面的层读到那行，分不出哪半边是「说过的话」，哪半边是「有人读出来的东西」。
# 把那一行整体升成 SOURCE，等于把读法跟事实一起升上去了。
#
# Two rules are locked down here.
#
# 1. Source is immutable and has no editing surface at all — not a discouraged
#    one, none. Mutation authority belongs to the storage layer; a rule that
#    lives only in a docstring is one a future refactor deletes unnoticed.
#    原始不可变，而且**根本没有**编辑面 —— 不是「不建议用」，是没有。
#    改写权限属于存储层；只写在 docstring 里的规矩，将来某次重构会毫无察觉地删掉它。
#
# 2. An interpretation must be able to name its evidence and the ruler it was
#    taken with, or it is not checkable and not re-derivable.
#    一条解释必须说得出自己的证据和当时用的尺子，否则它既不可核对也无法重新推导。
# ============================================================

import json

import pytest

import source_log
import dialogue_residue_engine as dre


MSGS = [
    {"role": "user", "text": "你还记得上次那件事吗", "ts": "2026-08-01T10:00:00"},
    {"role": "assistant", "text": "记得，你当时没说完", "ts": "2026-08-01T10:00:05"},
]


# ---------------------------------------------------------
# The one-way valve, enforced by absence
# ---------------------------------------------------------

def test_source_log_exposes_no_way_to_change_anything():
    """The valve is structural: there is no function to call.

    If a later refactor adds update()/delete()/merge() here, this fails — which
    is the point. Reviewing a diff is how that rule stays real.
    阀门是结构性的：没有函数可调。
    如果将来某次重构在这里加了 update()/delete()/merge()，这条会失败 —— 这正是目的。
    """
    surface = {n for n in dir(source_log) if not n.startswith("_")}
    forbidden = {"update", "delete", "merge", "edit", "rewrite", "overwrite",
                 "prune", "compact", "revise"}
    assert not (surface & forbidden), (
        f"source_log grew a mutation surface: {sorted(surface & forbidden)}"
    )


def test_recording_the_same_utterance_twice_keeps_one(tmp_path):
    """Utterances arrive through overlapping 2+2 windows, so the same sentence
    is submitted repeatedly. It is one event; it gets one id."""
    d = str(tmp_path)
    first = source_log.record(d, MSGS, window_id="w1")
    second = source_log.record(d, MSGS, window_id="w2")  # windows overlap

    assert first == second, "same sentence got a different id in a different window"
    assert len(source_log.read_all(d)) == 2, "the overlap duplicated the source"


def test_identity_is_content_not_resemblance(tmp_path):
    """Content identity, never similarity — the whole lesson of Phase 1."""
    d = str(tmp_path)
    a = source_log.record(d, [{"role": "user", "text": "我今天很累", "ts": "t"}])
    b = source_log.record(d, [{"role": "user", "text": "我今天有点累", "ts": "t"}])
    assert a != b, "two different sentences collapsed into one"
    assert len(source_log.read_all(d)) == 2


def test_a_rewritten_utterance_is_a_new_row_not_an_edit(tmp_path):
    """There is no in-place correction. A different wording is a different
    fact about what was said, and both stay.
    没有原地更正。不同的措辞就是关于「说了什么」的不同事实，两份都留着。"""
    d = str(tmp_path)
    source_log.record(d, [{"role": "user", "text": "原话", "ts": "t"}])
    source_log.record(d, [{"role": "user", "text": "改过的话", "ts": "t"}])
    texts = [r["text"] for r in source_log.read_all(d)]
    assert texts == ["原话", "改过的话"]


def test_every_source_row_declares_its_authority(tmp_path):
    d = str(tmp_path)
    source_log.record(d, MSGS)
    assert all(r["authority"] == "source" for r in source_log.read_all(d))


# ---------------------------------------------------------
# An interpretation has to be able to cite itself
# ---------------------------------------------------------

def test_interpretation_points_back_at_its_evidence(tmp_path):
    d = str(tmp_path)
    saved = dre.save_dialogue_residue_state(
        d, {"primary_drive": "reflection", "intensity": 0.1, "confidence": 0.8,
            "messages": MSGS},
        ledger_stage="test",
    )
    assert saved["authority"] == "interpretation"
    assert saved["derived_from"], "an interpretation with no evidence pointer"

    resolved = source_log.resolve(d, saved["derived_from"])
    assert len(resolved) == len(saved["derived_from"])
    assert {r["text"] for r in resolved.values()} == {m["text"] for m in MSGS}


def test_interpretation_records_the_reading_before_the_rules_voted(tmp_path):
    """The rule layer can override what the analyzer said — a territorial cue
    forces primary_drive to possessiveness. Recording rubric_version without
    recording the rule layer's input means knowing a reading was taken with an
    old ruler and having no way to measure again with a new one.
    规则层可以推翻分析器说的话。只记 rubric_version 而不记规则层的输入，
    等于知道「这是用旧尺子量的」却没法用新尺子重量一遍。"""
    d = str(tmp_path)
    cue = dre.TERRITORIAL_CUES[0]
    saved = dre.save_dialogue_residue_state(
        d, {"primary_drive": "reflection", "intensity": 0.10, "confidence": 0.9,
            "messages": [{"role": "user", "text": f"{cue}", "ts": "t1"},
                         {"role": "assistant", "text": "嗯", "ts": "t2"}]},
        ledger_stage="test",
    )
    assert saved["rules_fired"], "a rule fired but left no trace of having fired"
    # what the rule layer produced
    assert saved["primary_drive"] == "possessiveness"
    # and what it was handed, still recoverable
    assert saved["analyzer_reading"]["primary_drive"] == "reflection"
    assert saved["rubric_version"] == dre.RUBRIC_VERSION


def test_untouched_readings_say_so_explicitly(tmp_path):
    """"No rule touched this" must be a recorded fact, not something inferred
    from an absence."""
    d = str(tmp_path)
    saved = dre.save_dialogue_residue_state(
        d, {"primary_drive": "curiosity", "intensity": 0.08, "confidence": 0.9,
            "messages": MSGS},
        ledger_stage="test",
    )
    assert saved["rules_fired"] == []
    assert saved["analyzer_reading"]["primary_drive"] == saved["primary_drive"]


def test_source_is_written_before_any_interpretation(tmp_path, monkeypatch):
    """An interpretation whose evidence was never stored is worse than none —
    it still looks citable.
    一条证据从没存下来的解释比没有解释更糟 —— 它看起来照样可以引用。"""
    d = str(tmp_path)

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(source_log, "record", boom)
    with pytest.raises(OSError):
        dre.save_dialogue_residue_state(
            d, {"primary_drive": "reflection", "intensity": 0.1,
                "confidence": 0.8, "messages": MSGS},
            ledger_stage="test",
        )
    assert not (tmp_path / "dialogue_residue_ledger.jsonl").exists(), \
        "an interpretation was stored while its evidence was not"


def test_ledger_row_carries_the_authority_label(tmp_path):
    d = str(tmp_path)
    dre.save_dialogue_residue_state(
        d, {"primary_drive": "social", "intensity": 0.05, "confidence": 0.9,
            "messages": MSGS}, ledger_stage="test")
    rows = [json.loads(l) for l
            in open(tmp_path / "dialogue_residue_ledger.jsonl") if l.strip()]
    assert rows and all(r["event"]["authority"] == "interpretation" for r in rows)
