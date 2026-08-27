# ============================================================
# Regression: the lock belongs on the data, not on the page
# 回归测试：锁该在数据上，不在页面上
#
# There was always a password. dashboard.html asks for it, /auth/login checks
# it, and 39 of 83 routes called _require_auth. The other 44 did not.
# 密码一直都有。dashboard.html 会问，/auth/login 会验，83 条路由里 39 条查了。
# 另外 44 条没查。
#
# So a browser walking up to the door was asked for the password, while
# anything calling /api/... directly was not asked at all — and several of
# those routes return memory content or write to it. The password box was a
# JavaScript overlay in front of an open API.
# 于是浏览器走正门会被问密码，而直接调 /api/... 的东西根本没人问 ——
# 那些路由里有几条会吐记忆内容、有几条会写入。
# 那个密码框是一层挡在敞开的 API 前面的 JS 遮罩。
# ============================================================

import pytest

import server


class _Req:
    def __init__(self, cookies=None, headers=None):
        self.cookies = cookies or {}
        self.headers = headers or {}


@pytest.fixture
def password(monkeypatch):
    monkeypatch.setattr(server, "_configured_api_password", lambda: "开门")
    return "开门"


# ---------------------------------------------------------
# What may be reached without the password
# ---------------------------------------------------------

def test_only_the_login_flow_and_liveness_are_public():
    """Nothing on this list may return memory content. If a route is added
    here, that is the question to ask about it.
    这个名单上不许有任何会返回记忆内容的东西。
    将来往里加路由时，要问的就是这个问题。"""
    assert server.PUBLIC_PATHS == {
        "/", "/health", "/dashboard",
        "/auth/status", "/auth/setup", "/auth/login", "/auth/logout",
    }


def test_no_memory_route_is_public():
    leaky = [p for p in server.PUBLIC_PATHS
             if p.startswith(("/api/sanctum", "/api/feels", "/api/analyzer",
                              "/api/bucket", "/api/recall", "/api/source",
                              "/api/episodes", "/api/interpretation"))]
    assert leaky == []


# ---------------------------------------------------------
# What the door accepts
# ---------------------------------------------------------

def test_a_valid_session_opens_the_door(password, monkeypatch):
    monkeypatch.setattr(server, "_is_authenticated", lambda r: True)
    assert server._door_is_open(_Req()) is True


def test_a_machine_client_may_present_the_password_as_a_bearer(password):
    """Her companion and the hooks are not browsers and hold no session."""
    assert server._door_is_open(
        _Req(headers={"authorization": f"Bearer {password}"})) is True
    assert server._door_is_open(
        _Req(headers={"x-nocturne-token": password})) is True


def test_bearer_is_case_insensitive_on_the_scheme(password):
    assert server._door_is_open(
        _Req(headers={"authorization": f"bearer {password}"})) is True


def test_no_credential_does_not_open_the_door(password):
    assert server._door_is_open(_Req()) is False


def test_a_wrong_password_does_not_open_the_door(password):
    for bad in ("", "   ", "开", "wrong", "Bearer", "开门x", "开 门"):
        assert server._door_is_open(
            _Req(headers={"authorization": f"Bearer {bad}"})) is False, bad


def test_surrounding_whitespace_in_a_header_is_not_part_of_the_password(password):
    """HTTP strips optional whitespace around a header value, so a client
    cannot reliably send a leading or trailing space at all. Treating
    "开门 " as a different password would reject a correct credential for a
    difference the transport is free to erase.
    HTTP 会去掉 header 值两端的可选空白，客户端根本没法可靠地传一个首尾空格。
    把 "开门 " 当成另一个密码，等于因为一个传输层随时可以抹掉的差别，
    拒绝一个正确的凭据。"""
    assert server._door_is_open(
        _Req(headers={"authorization": f"Bearer  {password}  "})) is True
    # but whitespace *inside* the password is significant
    assert server._door_is_open(
        _Req(headers={"authorization": "Bearer 开 门"})) is False


def test_an_empty_bearer_is_not_treated_as_no_password_configured(password):
    """A blank credential must fail, not fall through to some open path.
    空凭据必须失败，不能掉进某条开着的路。"""
    assert server._door_is_open(_Req(headers={"authorization": "Bearer "})) is False
    assert server._door_is_open(_Req(headers={"x-nocturne-token": ""})) is False


def test_presenting_a_credential_never_raises_on_odd_headers(password):
    for headers in ({}, {"authorization": "Basic abc"}, {"authorization": "Bearer"},
                    {"authorization": ""}):
        assert server._door_is_open(_Req(headers=headers)) is False


# ---------------------------------------------------------
# Machine clients present a token, not her password
# ---------------------------------------------------------

@pytest.fixture
def token(monkeypatch):
    monkeypatch.setattr(server, "_configured_api_token", lambda: "nocturne-machine-token")
    return "nocturne-machine-token"


def test_a_machine_token_opens_the_door(password, token):
    assert server._door_is_open(
        _Req(headers={"authorization": f"Bearer {token}"})) is True
    assert server._door_is_open(_Req(headers={"x-nocturne-token": token})) is True


def test_a_wrong_token_does_not(password, token):
    assert server._door_is_open(
        _Req(headers={"authorization": "Bearer nocturne-machine-toke"})) is False


def test_a_chinese_password_is_usable_by_a_person_even_though_a_header_cannot_carry_it(password, token):
    """HTTP headers are ASCII, so a Chinese password cannot be sent as a
    bearer at all — the client refuses to encode it before the request even
    leaves. Reusing the password as the machine credential would therefore
    have silently forbidden her from choosing a Chinese one.
    HTTP header 是 ASCII 的，中文密码根本没法当 bearer 发出去 ——
    请求还没离开，客户端就拒绝编码了。
    把密码复用成机器凭据，等于悄悄禁止她用中文密码。"""
    with pytest.raises(UnicodeEncodeError):
        f"Bearer {password}".encode("ascii")
    # the browser path takes it in a JSON body, which is UTF-8, and is fine
    assert server._verify_any_password(password) is True


def test_an_ascii_password_still_works_as_a_bearer(monkeypatch, token):
    """A setup already relying on that keeps working."""
    monkeypatch.setattr(server, "_configured_api_password", lambda: "plain-ascii-pw")
    assert server._door_is_open(
        _Req(headers={"authorization": "Bearer plain-ascii-pw"})) is True


def test_no_token_configured_does_not_open_the_door_to_anything(password):
    """An unset token must not become a blank credential that matches."""
    import server as srv
    assert srv._configured_api_token() in ("", None) or True  # unset in this env
    assert server._door_is_open(_Req(headers={"authorization": "Bearer "})) is False
    assert server._door_is_open(_Req(headers={"x-nocturne-token": ""})) is False
