"""要旨层与原件并存 —— 原件一直留着，但 08-30 之前没有门。

痕迹转换理论：记忆变旧不是变模糊，是要旨版和细节版**并存**。
写那半这套系统一直做对了（每次改正文前把原文写进只增不删的流水账），
但 `revisions_for()` 在 08-30 之前**只有测试在调** —— 没有工具、没有接口。
`origin` 就是那扇门。
"""
import pytest

from bucket_manager import BucketRevision


@pytest.mark.asyncio
async def test_origin_gives_back_the_wording_before_the_merge(bucket_mgr):
    import server
    original = "她说要是我会就好了，尾音往下沉。那不是抱怨自己不够好。"
    bid = await bucket_mgr.create(content=original, domain=["内心"])
    await bucket_mgr.update(
        bid, content="她表达了想亲手做的愿望。",
        _revision=BucketRevision(actor="semantic_merge", reason="合并"),
    )
    out = await server.origin(bid)
    assert "尾音往下沉" in out, out          # 原话回得来
    assert "semantic_merge" in out           # 谁改的
    assert "原件" in out


@pytest.mark.asyncio
async def test_an_untouched_memory_says_so(bucket_mgr):
    import server
    bid = await bucket_mgr.create(content="没被动过", domain=["内心"])
    assert "没有被改写过" in await server.origin(bid)


@pytest.mark.asyncio
async def test_origin_needs_an_id():
    import server
    for v in ("", "   ", None):
        assert "要带 bucket_id" in await server.origin(v)


@pytest.mark.asyncio
async def test_unknown_id_is_not_a_crash():
    import server
    assert isinstance(await server.origin("没这个id"), str)


@pytest.mark.asyncio
async def test_only_the_last_few_are_shown(bucket_mgr):
    """一条被改过很多次的记忆，不该一次糊他一脸。"""
    import server
    bid = await bucket_mgr.create(content="v0", domain=["内心"])
    for i in range(1, 6):
        await bucket_mgr.update(
            bid, content=f"v{i}",
            _revision=BucketRevision(actor="测试", reason=f"第{i}次"),
        )
    out = await server.origin(bid, limit=2)
    assert "v4" in out and "v3" in out       # 最近两次改写之前的样子
    assert "v0" not in out                   # 更早的这次不列
    assert "5 次" in out                     # 但要告诉他一共改过几次
