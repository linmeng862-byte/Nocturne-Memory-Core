"""bottles/ 里的东西必须一条不漏地进记忆系统,且不会重复进两次。"""
import json, os, tempfile
import bottle_migration as bm


def _write(d, name, payload):
    with open(os.path.join(d, name), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)


def test_thrown_bottles_are_found():
    """旧迁移只 glob hold-*,漂流瓶一条都没搬过。这是那个 bug 的回归测试。"""
    with tempfile.TemporaryDirectory() as d:
        _write(d, "hold-1.json", {"type": "hold_this", "memory": "甲"})
        _write(d, "bottle-1.json", {"type": "throw_bottle", "message": "乙"})
        got = bm.pending(d)
        kinds = {p["kind"] for p in got}
        assert kinds == {"hold_this", "throw_bottle"}, kinds


def test_same_opening_different_memory_both_survive():
    """content[:100] 去重会吞掉第二条。指纹不会。"""
    head = "今天她说" + "啊" * 120
    with tempfile.TemporaryDirectory() as d:
        _write(d, "hold-1.json", {"memory": head + "结尾一"})
        _write(d, "hold-2.json", {"memory": head + "结尾二"})
        assert len(bm.pending(d)) == 2


def test_identical_content_is_one_memory():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "hold-1.json", {"memory": "同一句", "why": "同一因"})
        _write(d, "hold-2.json", {"memory": "同一句", "why": "同一因"})
        assert len(bm.pending(d)) == 1


def test_already_in_buckets_is_skipped():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "hold-1.json", {"memory": "已经在了"})
        existing = [bm.render({"memory": "已经在了"})]
        assert bm.pending(d, existing) == []


def test_empty_and_broken_files_do_not_crash():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "hold-1.json", {"memory": ""})
        with open(os.path.join(d, "hold-2.json"), "w") as f:
            f.write("{ not json")
        _write(d, "hold-3.json", {"memory": "好的那条"})
        got = bm.pending(d)
        assert len(got) == 1 and "好的那条" in got[0]["content"]


def test_missing_directory_is_not_an_error():
    assert bm.pending("/nonexistent/bottles") == []


def test_why_is_carried_not_dropped():
    """why 是「为什么记」——丢了它就只剩一句没有理由的话。"""
    with tempfile.TemporaryDirectory() as d:
        _write(d, "hold-1.json", {"memory": "那件事", "why": "因为她哭了"})
        assert "因为她哭了" in bm.pending(d)[0]["content"]


def test_fingerprint_is_stable_across_processes():
    """hold_this 用 hash() 生成 ID —— 每个进程都不一样。sha256 不会。"""
    a = bm.fingerprint("一句话")
    assert a == bm.fingerprint("一句话")
    assert a != bm.fingerprint("另一句话")
    assert len(a) == 64


def test_double_written_bottle_is_not_migrated_twice():
    """hold_this 早就在双写 pinned 桶。指纹必须认得出那个格式,
    否则每一条已经进去的记忆都会被再搬一遍,变成重复。"""
    memory, why = "那件事", "因为她哭了"
    already_in_bucket = f"hold_this: {memory}\n\n为什么记: {why}"
    with tempfile.TemporaryDirectory() as d:
        _write(d, "hold-1.json", {"memory": memory, "why": why})
        assert bm.pending(d, [already_in_bucket]) == []


def test_render_matches_the_double_write_byte_for_byte():
    assert bm.render({"memory": "m", "why": "w"}) == "hold_this: m\n\n为什么记: w"
    assert bm.render({"memory": "m"}) == "hold_this: m\n\n为什么记: "
