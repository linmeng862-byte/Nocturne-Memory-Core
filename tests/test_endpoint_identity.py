# ============================================================
# Regression: which body spoke is declared, never inferred
# 回归测试：是哪个身体说的，由声明得来，绝不推断
#
# This field reaches the source log, which is the one place a judgement must
# never arrive. Guessing an endpoint from User-Agent or IP would put a
# plausible-looking inference into the record as though it were a fact, and
# every layer above would inherit it unmarked.
# 这个字段会进原始账，那是最不该让判断到达的地方。
# 从 User-Agent 或 IP 猜 endpoint，等于把一个看起来合理的推断当成事实写进记录，
# 上面每一层都会无标记地继承它。
#
# "unknown" is a real answer. Not knowing which body spoke is itself a fact
# worth keeping; a guess in its place is not.
# "unknown" 是一个真的答案。「不知道是哪个身体说的」本身就值得留下；
# 拿一个猜测顶替它，不值得。
# ============================================================

import pytest


@pytest.fixture
def declared():
    import server
    return server._declared_endpoint, server.ENDPOINT_UNKNOWN


class _Req:
    def __init__(self, headers=None):
        self.headers = headers or {}


def test_a_declared_endpoint_is_taken_at_its_word(declared):
    fn, _ = declared
    assert fn({"endpoint": "chat-c"}, _Req()) == "chat-c"
    assert fn({}, _Req({"x-nocturne-endpoint": "cli"})) == "cli"


def test_body_wins_over_header(declared):
    fn, _ = declared
    assert fn({"endpoint": "chat-c"}, _Req({"x-nocturne-endpoint": "cli"})) == "chat-c"


def test_saying_nothing_is_recorded_as_unknown(declared):
    fn, unknown = declared
    assert fn({}, _Req()) == unknown
    assert fn(None, None) == unknown
    assert fn({"endpoint": "   "}, _Req()) == unknown


def test_a_malformed_endpoint_is_not_stored_as_given(declared):
    """This string lands in an append-only log that has no editing surface, so
    it cannot be cleaned up later.
    这个字符串会落进一本没有编辑面的只增账，事后清不掉。"""
    fn, unknown = declared
    for bad in ["../../etc", "chat c", "x" * 200, "chat\nc", "<script>", ""]:
        assert fn({"endpoint": bad}, _Req()) == unknown, f"accepted {bad!r}"


def test_nothing_is_inferred_from_the_request_itself(declared):
    """A rich, very guessable request that declares nothing still yields
    unknown."""
    fn, unknown = declared
    req = _Req({"user-agent": "Mozilla/5.0 Chat-C/1.0", "x-forwarded-for": "1.2.3.4",
                "referer": "https://chat-c.example/app"})
    assert fn({"messages": []}, req) == unknown
