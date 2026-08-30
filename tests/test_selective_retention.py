"""Kensinger 的情绪权衡：唤醒度高时中心细节记得更牢，边缘细节反而更差。

合并到此之前是**均匀降分辨率**——不管当时多烈，一视同仁地压。
结果是最该留原话的地方被概括成了「她表达了在乎」。

⚠️ 这是提示词层面的改动，**输出质量没法单测**。这里测的只有分档逻辑本身。
"""
import dehydrator as d


def test_high_arousal_asks_for_verbatim():
    c = d._retention_clause(0.9)
    assert "保留原话" in c and "边缘细节" in c


def test_low_arousal_stays_uniform():
    c = d._retention_clause(0.1)
    assert "均匀压缩" in c and "保留原话" not in c


def test_middle_gets_nothing_extra():
    """中间档不加指示——没有依据就别塞话进提示词。"""
    assert d._retention_clause(0.45) == ""


def test_unknown_arousal_does_not_guess():
    for v in (None, "", "烈", [], {}):
        assert d._retention_clause(v) == ""


def test_high_arousal_does_not_relax_the_length_cap():
    """闸门。Kensinger 说的是「留中心、丢边缘」，是一次**再分配**，
    不是「留得更多」。放宽长度上限的话烈的记忆会无限膨胀。"""
    c = d._retention_clause(0.9)
    assert "总长度上限不变" in c
    assert "120" not in c          # 不许在这里另开一个长度规则
    assert "更长" not in c and "放宽" not in c


def test_the_base_prompt_still_caps_length():
    assert "120%" in d.MERGE_PROMPT


def test_boundaries():
    assert d._retention_clause(0.6) == d._retention_clause(0.9)     # >=0.6 高
    assert d._retention_clause(0.59) == ""                          # 中间
    assert d._retention_clause(0.34) == d._retention_clause(0.0)    # <0.35 低
