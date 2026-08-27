# ============================================================
# Regression: one memory, many bodies
# 回归测试：一份记忆，多个身体
#
# Every guarantee made in the earlier phases was written under an unstated
# assumption: that there is exactly one writer. The moment this is deployed as
# a URL and a phone, a terminal and a chat window all reach the same store,
# that assumption stops holding — and the guarantees fail quietly rather than
# loudly, which is the worst way for them to fail.
# 前几个阶段做出的每一条保证,底下都藏着一个没说出口的前提:只有一个写入者。
# 一旦部署成网址,手机、终端、聊天窗同时够到同一份存储,这个前提就不成立了 ——
# 而这些保证是「悄悄失效」而不是「大声报错」,那是最糟的失效方式。
#
# These tests use real forked processes on purpose. Threads would share the
# interpreter and could pass while the cross-process behaviour is still broken;
# flock is a property of processes, so the test has to be one too.
# 这些测试刻意用真实的多进程。多线程共享解释器,可能测试过了而跨进程行为其实还是坏的;
# flock 是进程层面的性质,所以测试也必须是。
# ============================================================

import os
import json
import multiprocessing as mp

import pytest

import utils
import recall
import recall_journal


# multiprocessing must not inherit a half-initialized interpreter here
_ctx = mp.get_context("fork")


# ---------------------------------------------------------
# The lock itself
# ---------------------------------------------------------

def _hammer_counter(path, rounds):
    """Read-increment-write with no protection except the lock under test."""
    for _ in range(rounds):
        with utils.exclusive(path):
            n = int(open(path).read() or 0) if os.path.exists(path) else 0
            utils.write_atomic(path, str(n + 1))


def test_exclusive_serializes_read_modify_write(tmp_path):
    """The canonical lost-update shape. Without the lock this lands well short
    of 200 — that shortfall is exactly what update() and touch() were doing."""
    path = str(tmp_path / "counter")
    utils.write_atomic(path, "0")
    procs = [_ctx.Process(target=_hammer_counter, args=(path, 50)) for _ in range(4)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(30)
    assert int(open(path).read()) == 200


def test_exclusive_times_out_instead_of_hanging(tmp_path):
    """A wedged endpoint must not be able to freeze every other endpoint."""
    path = str(tmp_path / "held")

    def _hold(p, ready, release):
        with utils.exclusive(p):
            ready.set()
            release.wait(20)

    ready, release = _ctx.Event(), _ctx.Event()
    holder = _ctx.Process(target=_hold, args=(path, ready, release))
    holder.start()
    try:
        assert ready.wait(10), "helper never took the lock"
        with pytest.raises(utils.LockTimeout):
            with utils.exclusive(path, timeout=0.3):
                pass
    finally:
        release.set()
        holder.join(20)


def test_lock_is_released_when_the_body_raises(tmp_path):
    """An endpoint that crashes mid-write must not wedge the store forever."""
    path = str(tmp_path / "x")
    with pytest.raises(ValueError):
        with utils.exclusive(path):
            raise ValueError("boom")
    with utils.exclusive(path, timeout=1.0):
        pass  # still acquirable


# ---------------------------------------------------------
# Atomic replacement
# ---------------------------------------------------------

def test_write_atomic_never_leaves_a_truncated_memory(tmp_path):
    """open(path,"w") truncates first. A crash between truncate and write does
    not corrupt a memory — it deletes it, leaving zero bytes where it was."""
    path = str(tmp_path / "m.md")
    utils.write_atomic(path, "原始正文")

    class Boom(Exception):
        pass

    def exploding_dumps(_):
        raise Boom()

    with pytest.raises(Boom):
        utils.write_atomic(path, exploding_dumps(None))

    assert open(path).read() == "原始正文"
    # and no debris left behind
    assert [f for f in os.listdir(tmp_path) if f.endswith(".tmp")] == []


def test_append_jsonl_lines_never_interleave(tmp_path):
    """Two bodies journalling at once must produce two whole lines, not one
    spliced one. A corrupt line is a silently dropped event."""
    path = str(tmp_path / "j.jsonl")

    def _writer(p, tag, n):
        for i in range(n):
            utils.append_jsonl(p, {"tag": tag, "i": i, "pad": "填" * 400})

    procs = [_ctx.Process(target=_writer, args=(path, t, 40)) for t in "abcd"]
    for p in procs:
        p.start()
    for p in procs:
        p.join(30)

    rows = [json.loads(l) for l in open(path) if l.strip()]
    assert len(rows) == 160
    assert sorted(r["tag"] for r in rows) == sorted("abcd" * 40)


# ---------------------------------------------------------
# Touch idempotence across processes
# ---------------------------------------------------------

def _hold_journal(d, ready, release):
    with utils.exclusive(recall_journal.journal_path(d)):
        ready.set()
        release.wait(20)


def test_dedup_and_append_happen_under_one_lock(tmp_path):
    """The property that matters, proved deterministically.

    The old code read "have I seen this recall_id" and appended afterwards.
    Two endpoints confirming the same recall_id both read "not seen yet" and
    both wrote — and a memory that looks twice as touched as it really was
    poisons the only honest evidence a texture layer is ever going to have.
    旧代码先读「这个 recall_id 见过没」,再追加。两个端确认同一个 recall_id 时
    都读到「没见过」,于是都写 —— 而一条看起来被触碰了两次的记忆,
    会污染将来质地唯一诚实的证据。

    A stampede of forked processes does not reliably reproduce that window —
    process start-up jitter is wider than the race itself, so such a test
    passes on broken code and proves nothing. Instead: hold the journal lock
    from outside and assert record_touch cannot get past it. That can only be
    true if the check and the append are both inside it.
    多进程混战无法稳定复现那个窗口 —— 进程启动抖动比竞态本身还宽,
    这种测试在坏代码上照样绿,什么也证明不了。改成:从外面攥住这本账的锁,
    断言 record_touch 过不去。只有当查重和追加都在锁里面,这才可能成立。
    """
    d = str(tmp_path)
    ready, release = _ctx.Event(), _ctx.Event()
    holder = _ctx.Process(target=_hold_journal, args=(d, ready, release))
    holder.start()
    try:
        assert ready.wait(10), "helper never took the journal lock"
        with pytest.raises(utils.LockTimeout):
            recall_journal.record_touch(
                d, "r1", recall.INVOLUNTARY, ["b1"],
                lock_timeout=0.3,
            )
        # nothing was written while it could not hold the lock
        assert recall_journal.read_events(d) == []
    finally:
        release.set()
        holder.join(20)

    # and once the lock is free the same touch goes through exactly once
    assert recall_journal.record_touch(d, "r1", recall.INVOLUNTARY, ["b1"])["recorded"]
    assert recall_journal.record_touch(d, "r1", recall.INVOLUNTARY, ["b1"])["duplicate"]
    assert len(recall_journal.read_events(d)) == 1


def _confirm_same_recall(d, barrier, n):
    barrier.wait(20)
    for _ in range(n):
        try:
            recall_journal.record_touch(d, "same-recall", recall.INVOLUNTARY, ["b1"])
        except Exception:
            pass


def test_repeated_concurrent_confirms_stay_single(tmp_path):
    """Smoke test over the real code path. It cannot reliably reproduce the
    race on its own — see the test above for the deterministic proof — but it
    would catch a regression that breaks dedup outright.
    这条是跑真实代码路径的冒烟测试。它自己无法稳定复现竞态(确定性的证明在上一条),
    但如果查重被整个改坏,它会抓到。"""
    d = str(tmp_path)
    workers = 8
    barrier = _ctx.Barrier(workers)
    procs = [_ctx.Process(target=_confirm_same_recall, args=(d, barrier, 40))
             for _ in range(workers)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(30)

    rows = recall_journal.read_events(d)
    assert len(rows) == 1, f"same recall_id recorded {len(rows)} times"
    assert recall_journal.touch_summary(d)["b1"]["involuntary"] == 1


def _confirm_distinct(d, i, barrier):
    barrier.wait(20)
    try:
        recall_journal.record_touch(d, f"r{i}", recall.DELIBERATE, [f"b{i}"],
                                    endpoint=f"end-{i}")
    except Exception:
        pass


def test_distinct_touches_all_survive_the_stampede(tmp_path):
    """Serializing must not silently drop real events either."""
    d = str(tmp_path)
    n = 8
    barrier = _ctx.Barrier(n)
    procs = [_ctx.Process(target=_confirm_distinct, args=(d, i, barrier)) for i in range(n)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(30)

    rows = recall_journal.read_events(d)
    assert sorted(r["recall_id"] for r in rows) == sorted(f"r{i}" for i in range(n))
    assert recall_journal.last_encounter(d)


# ---------------------------------------------------------
# What the lock buys the memory store itself
# ---------------------------------------------------------

@pytest.mark.asyncio
async def test_touch_does_not_lose_activations(bucket_mgr):
    """touch() is a read-increment-write on the hottest path in the system —
    it fires on every recall hit from every endpoint. Undercounting here makes
    decay treat the best-used memories as neglected."""
    import asyncio
    bid = await bucket_mgr.create(content="被反复想起的一条", tags=[], importance=5,
                                  domain=["日常"], valence=0.5, arousal=0.3)
    await asyncio.gather(*(bucket_mgr.touch(bid) for _ in range(20)))
    assert (await bucket_mgr.get(bid))["metadata"]["activation_count"] == 20


@pytest.mark.asyncio
async def test_stale_edit_is_refused_not_silently_applied(bucket_mgr):
    """An editor who read the body first can pin what they saw. If someone else
    edited in between, this must refuse — overwriting a wording you never read
    is exactly the destructive merge from Phase 1 wearing a different hat.
    先读过正文的人可以钉住自己看到的那份。中间被别人改过就必须拒绝 ——
    覆盖一份你从没读过的说法，就是 Phase 1 那个破坏性合并换了张脸。"""
    from bucket_manager import BucketRevision, StaleWrite

    bid = await bucket_mgr.create(content="第一版", tags=[], importance=5,
                                  domain=["日常"], valence=0.5, arousal=0.3)
    saw = utils.content_fingerprint("第一版")

    # somebody else edits first
    await bucket_mgr.update(bid, content="第二版",
                            _revision=BucketRevision(actor="另一个端", reason="先到先改"))

    with pytest.raises(StaleWrite):
        await bucket_mgr.update(
            bid, content="基于第一版写的第三版",
            _revision=BucketRevision(actor="我", reason="我以为还是第一版",
                                     expected_hash=saw))

    assert (await bucket_mgr.get(bid))["content"].strip() == "第二版"


@pytest.mark.asyncio
async def test_journal_records_the_text_that_was_really_overwritten(bucket_mgr):
    """The recoverability promise from Phase 1.5, restated for many writers:
    previous_content must be the body that this edit actually displaced.
    Phase 1.5 那个「换得回来」的承诺，在多写入者下重述一遍：
    previous_content 必须是这次修改真正顶掉的那份正文。"""
    from bucket_manager import BucketRevision

    bid = await bucket_mgr.create(content="甲", tags=[], importance=5,
                                  domain=["日常"], valence=0.5, arousal=0.3)
    for old, new in (("甲", "乙"), ("乙", "丙"), ("丙", "丁")):
        await bucket_mgr.update(bid, content=new,
                                _revision=BucketRevision(actor="她", reason="改口"))

    rows = bucket_mgr.revisions_for(bid)
    assert [r["previous_content"].strip() for r in rows] == ["甲", "乙", "丙"]
    # every recorded hash matches the text stored beside it
    for r in rows:
        assert r["previous_hash"] == utils.content_fingerprint(r["previous_content"])
