"""褪色偏差：负面记忆的**情绪强度**褪得比正面快——但事件本身不许因此被忘掉。"""
from datetime import datetime, timedelta

from decay_engine import DecayEngine


def _eng():
    return DecayEngine({}, bucket_mgr=None)


def _meta(valence=0.5, arousal=0.8, days=0, **kw):
    d = dict(importance=5, activation_count=1, arousal=arousal, valence=valence,
             last_active=(datetime.now() - timedelta(days=days)).isoformat())
    d.update(kw)
    return d


def test_negative_affect_fades_faster_than_positive():
    """FAB 本身。同样烈、同样久，负价剩下的情绪强度更少。"""
    e = _eng()
    assert e.affect_retention(0.1, 60) < e.affect_retention(0.9, 60)


def test_neutral_sits_between():
    e = _eng()
    r = [e.affect_retention(v, 60) for v in (0.1, 0.5, 0.9)]
    assert r[0] < r[1] < r[2], r


def test_nothing_fades_on_day_zero():
    e = _eng()
    for v in (0.0, 0.5, 1.0):
        assert e.affect_retention(v, 0) == 1.0


def test_the_event_is_not_forgotten_faster_because_it_hurt():
    """闸门。这是整套 FAB 唯一可能出大错的地方——

    如果一个痛的桶因为痛而掉到归档线以下，那不是褪色偏差，是选择性失明。
    情绪强度可以褪光，但事件的存活由 importance 决定,不由它还疼不疼决定。
    """
    e = _eng()
    # 情绪强度全褪光（arousal=0 等价于 retention=0）之后剩下的分
    floor = e.calculate_score(_meta(valence=0.5, arousal=0.0, days=90))
    hurt = e.calculate_score(_meta(valence=0.05, arousal=0.9, days=90))
    assert hurt >= floor, (hurt, floor)


def test_a_painful_memory_still_outranks_a_trivial_one():
    """重要的痛事，褪了三个月的情绪之后，仍然该排在不重要的开心事前面。"""
    e = _eng()
    painful = e.calculate_score(_meta(valence=0.05, arousal=0.9, days=90, importance=9))
    trivial = e.calculate_score(_meta(valence=0.95, arousal=0.2, days=90, importance=2))
    assert painful > trivial, (painful, trivial)


def test_urgency_expires():
    """刚发生的高唤醒未处理事项该插队；扛了半年的不该还在插队。"""
    e = _eng()
    fresh = e.calculate_score(_meta(valence=0.2, arousal=0.9, days=0, resolved=False))
    stale = e.calculate_score(_meta(valence=0.2, arousal=0.9, days=200, resolved=False))
    assert fresh > stale


def test_default_valence_is_symmetric_not_punished():
    """78% 的桶 valence 还是默认 0.5（从没被赋值）。
    默认值必须走中性,绝不能被当成负价惩罚——不然一次改动就误伤大半个库。"""
    e = _eng()
    assert e.affect_retention(0.5, 30) == e.affect_retention(0.5, 30)
    mid = e.affect_retention(0.5, 30)
    assert e.affect_retention(0.0, 30) < mid < e.affect_retention(1.0, 30)


def test_pinned_still_never_decays():
    e = _eng()
    assert e.calculate_score(_meta(valence=0.0, arousal=1.0, days=999, pinned=True)) == 999.0


def test_bad_valence_does_not_crash():
    e = _eng()
    assert e.calculate_score(_meta(valence="坏掉的", days=10)) > 0
