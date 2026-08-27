# ============================================================
# Regression: one privacy gate, and it fails closed
# 回归测试：只有一道隐私门，而且它向"关"的方向失败
#
# Privacy used to be decided in three places, each blind to something the
# others caught:
# 隐私以前在三处各自判定，每一处都对另外两处能看见的东西瞎掉：
#
#   _is_private_bucket      marks + domains,  missed tags
#   the analyzer list       domains + tags,   missed marks
#   /api/sanctum/breath     domains + tags,   missed marks
#
# So a tag-private memory stayed visible in wander, and a mark-private one
# reached both the analyzer candidates and an injection path. Each gate leaked
# exactly what the others stopped.
# 于是 tag 标 private 的记忆在 wander 里照样看得见，
# mark 标 private 的记忆同时进了 analyzer 候选和一条注入路径。
# 每一道门漏掉的，正好是另外两道拦住的。
#
# This is deliberately NOT symmetric with the inner reading. A wrong
# "not inner" hides a memory and can be undone. A wrong "not private" says
# something out loud and cannot.
# 这里刻意**不**跟 inner 的读法对称。判错「不是 inner」只是藏起一条记忆，可以撤回；
# 判错「不是 private」是把话说出去了，撤不回来。
# ============================================================

import pytest

import server


def _bucket(bid="b1", domain=None, tags=None, btype="dynamic", pinned=False):
    return {
        "id": bid,
        "content": "一些内容",
        "metadata": {
            "id": bid, "domain": domain or [], "tags": tags or [],
            "type": btype, "pinned": pinned, "created": "2026-08-01T10:00:00",
            "importance": 5, "arousal": 0.3,
        },
    }


def _mark(kind):
    return {"mark": kind, "timestamp": "2026-08-01T10:00:00", "note": ""}


# ---------------------------------------------------------
# Every kind of evidence is enough, on its own
# ---------------------------------------------------------

def test_private_in_domain_is_private():
    assert server._is_private_bucket(_bucket(domain=["private"]), []) is True


def test_private_in_tags_is_private():
    """The wander gate used to look only at domains, so a memory tagged
    private stayed visible there."""
    assert server._is_private_bucket(_bucket(tags=["private"]), []) is True


def test_a_private_mark_is_private():
    """The analyzer and injection gates used to look only at domains and tags,
    so a memory marked private reached both."""
    assert server._is_private_bucket(_bucket(), [_mark("private")]) is True


def test_case_and_whitespace_do_not_open_the_gate():
    assert server._is_private_bucket(_bucket(domain=["Private"]), []) is True
    assert server._is_private_bucket(_bucket(tags=["  PRIVATE  "]), []) is True


def test_an_ordinary_memory_is_not_private():
    assert server._is_private_bucket(_bucket(domain=["日常"], tags=["笔记"]), []) is False
    assert server._is_private_bucket(_bucket(), [_mark("认")]) is False


def test_no_marks_is_not_the_same_as_undecidable():
    """Most memories have no marks at all. Treating absence as privacy would
    hide everything; treating an *error* as privacy is the actual rule.
    绝大多数记忆一条 mark 都没有。把「没有」当成私密会把一切都藏起来；
    把「读不出来」当成私密才是那条规矩。"""
    assert server._is_private_bucket(_bucket(), []) is False
    assert server._is_private_bucket(_bucket(), None) is False


# ---------------------------------------------------------
# Fail closed
# ---------------------------------------------------------

def test_unreadable_evidence_is_treated_as_private():
    """The only safe answer to "is this private" when the evidence cannot be
    read is yes."""
    class Exploding(dict):
        def get(self, *a, **k):
            raise RuntimeError("metadata unreadable")

    assert server._is_private_bucket(Exploding(), []) is True


def test_malformed_mark_rows_do_not_open_the_gate():
    assert server._is_private_bucket(_bucket(), ["not a dict", None, 42]) is False
    assert server._is_private_bucket(_bucket(domain=["private"]), ["junk"]) is True


# ---------------------------------------------------------
# The gate the other paths route through
# ---------------------------------------------------------

def test_wander_domain_reports_private_from_any_evidence():
    for bucket, marks in (
        (_bucket(domain=["private"]), []),
        (_bucket(tags=["private"]), []),
        (_bucket(), [_mark("private")]),
    ):
        assert server._guess_wander_domain(bucket, marks) == "private"


def test_private_outranks_every_other_reading():
    """A memory can be both recognised and private. Privacy wins, always.
    一条记忆可以既被认过又是私密的。永远是隐私赢。"""
    marks = [{"mark": "认", "timestamp": f"2026-08-0{d}T10:00:00"} for d in (1, 2, 3)]
    marks.append(_mark("private"))
    assert server._guess_wander_domain(_bucket(domain=["inner"]), marks) == "private"
