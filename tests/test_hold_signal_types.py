"""hold 的五个 signal：既收四档词（无/有一点/明显/很强），也收 0-1 数字。

回归：类型原来标成 `str`，而 docstring 写「0-1」，前端又把四档映射成数字发过来
（backend.js 的 SIGNAL_LEVELS）。于是模型一填 signal 就送来一个 float，pydantic
按 str 直接拒 —— **整条 hold 崩、content 一起丢**。这正是「五个 signal 一直 0%」
的真因：不是不填，是一填就崩。改成 Union[str, float] + 让 normalizer 认中文四档。
"""
import server


def _validate(**kw):
    """走 FastMCP 给 hold 建的那个真·参数模型，不做任何 I/O。"""
    tm = server.mcp._tool_manager.get_tool("hold")
    return tm.fn_metadata.arg_model.model_validate({"content": "x", **kw}).model_dump()


def test_float_signal_no_longer_rejected():
    # 她报的那个 case：clutch=0.6（float）曾经直接 pydantic ValueError。
    d = _validate(clutch=0.6)
    assert d["clutch"] == 0.6


def test_four_tier_words_accepted():
    for word in ("无", "有一点", "明显", "很强"):
        d = _validate(discernment=word)
        assert d["discernment"] == word


def test_normalize_maps_tiers_and_numbers():
    f = server._normalize_signal_value
    assert f("无") == 0.0
    assert f("有一点") == 0.3
    assert f("明显") == 0.6
    assert f("很强") == 0.9
    assert f(0.6) == 0.6            # 前端映射后发来的数字
    assert f("0.6") == 0.6          # 数字字符串
    assert f("high") == 0.86        # 老的 low/mid/high 仍然认
    assert f("") == 0.0
    assert f("乱码") == 0.0          # 认不出来 → 0，不崩


def test_all_five_signals_take_both_shapes():
    d = _validate(discernment="明显", territorial=0.3, clutch="很强",
                  strain=0.9, charge="有一点")
    assert d["discernment"] == "明显" and d["clutch"] == "很强"
    assert d["territorial"] == 0.3 and d["strain"] == 0.9
