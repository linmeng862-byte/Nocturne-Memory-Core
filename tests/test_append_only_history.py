# ============================================================
# Regression: history is append-only
# 回归测试：历史只增不改
#
# The bug these lock down / 这些测试锁住的那个 bug：
#   hold() used to hand a similar existing bucket and the new content to an
#   LLM and write the rewritten text back over the old body. The merge prompt
#   said 新内容与旧记忆冲突时以新内容为准 — so "I believed X" followed by
#   "I no longer believe X" collapsed into just the second one, and the first
#   was gone from disk. No revision, no backup.
#   越是「同一件事的重新理解」，相似度越高，越容易被吃掉。
#
# The rule / 规矩：
#   1. New experience != update to old history.
#   2. Similarity may suggest association; it may NOT authorize overwrite.
#   3. A → B keeps both A and B.
#   4. Exact duplicates (the same write retried) may still be deduped —
#      keyed on content identity, never on similarity.
# ============================================================

import hashlib

import pytest

from utils import content_fingerprint, find_exact_duplicate


# ---------------------------------------------------------
# Identity helpers — the dedup key must be the bytes, not a similarity score
# 指纹本身：去重必须认字节，不能认相似度
# ---------------------------------------------------------

def test_fingerprint_ignores_only_whitespace():
    """Trailing/surrounding whitespace is noise; wording is not."""
    assert content_fingerprint("我以为他记得我") == content_fingerprint("  我以为他记得我  ")
    assert content_fingerprint("第一行  \n第二行") == content_fingerprint("第一行\n第二行")


def test_fingerprint_separates_similar_but_different():
    """Two memories about the same thing are NOT the same memory."""
    a = "我以为他记得的是我"
    b = "我现在觉得他记得的就是我"
    assert content_fingerprint(a) != content_fingerprint(b)


def test_find_exact_duplicate_matches_only_identical():
    candidates = [
        {"id": "b1", "content": "我以为他记得的是我", "metadata": {}},
        {"id": "b2", "content": "我现在觉得他记得的就是我", "metadata": {}},
    ]
    assert find_exact_duplicate(candidates, "我以为他记得的是我")["id"] == "b1"
    # a near-miss must not be treated as a duplicate
    assert find_exact_duplicate(candidates, "我以为他记得的是我。") is None
    assert find_exact_duplicate([], "任何内容") is None


# ---------------------------------------------------------
# Storage-level: a second write never mutates the first bucket's file
# 存储层：第二次写入绝不能动到第一条的文件
# ---------------------------------------------------------

async def _create(bucket_mgr, content, **kw):
    kw.setdefault("domain", ["内心"])
    return await bucket_mgr.create(content=content, **kw)


def _body_on_disk(bucket_mgr, bucket_id):
    """Read the body straight off the .md file, bypassing any caching."""
    import frontmatter as fm
    path = bucket_mgr._find_bucket_file(bucket_id)
    assert path, f"bucket file missing for {bucket_id}"
    return fm.load(path).content


@pytest.mark.asyncio
async def test_belief_reversal_keeps_both(bucket_mgr):
    """
    "I believe X" → "I no longer believe X".
    Both survive; the first body is byte-for-byte unchanged.
    两条都在，第一条正文一个字都不能变。
    """
    first_text = "我相信他记得的是我。"
    second_text = "我不再相信他记得的是我。"

    first_id = await _create(bucket_mgr, first_text)
    before = _body_on_disk(bucket_mgr, first_id)
    before_hash = hashlib.sha256(before.encode("utf-8")).hexdigest()

    second_id = await _create(bucket_mgr, second_text)

    assert second_id != first_id, "反转后的判断被写进了同一个桶"

    after = _body_on_disk(bucket_mgr, first_id)
    assert after == before, "旧记忆的正文被改写了"
    assert hashlib.sha256(after.encode("utf-8")).hexdigest() == before_hash

    all_buckets = await bucket_mgr.list_all(include_archive=False)
    bodies = [b["content"].strip() for b in all_buckets]
    assert first_text in bodies, "「我相信」那条不见了"
    assert second_text in bodies, "「我不再相信」那条不见了"


@pytest.mark.asyncio
async def test_similar_but_not_identical_both_kept(bucket_mgr):
    """High semantic similarity is not permission to overwrite.
    很像，但不是同一条 —— 相似不构成覆盖的理由。"""
    a = "今天下午在阳台上坐了很久，风有点凉。"
    b = "今天下午在阳台上坐了一会儿，风凉得刚好。"

    id_a = await _create(bucket_mgr, a, domain=["日常"])
    body_a = _body_on_disk(bucket_mgr, id_a)

    id_b = await _create(bucket_mgr, b, domain=["日常"])

    assert id_a != id_b
    assert _body_on_disk(bucket_mgr, id_a) == body_a
    assert _body_on_disk(bucket_mgr, id_b).strip() == b


@pytest.mark.asyncio
async def test_contradiction_both_kept(bucket_mgr):
    """A flat contradiction is two facts about two moments, not one correction.
    直接矛盾的两句，是两个时刻的两件事，不是一次更正。"""
    yes = "他说他会一直在。"
    no = "他说他不确定还能在多久。"

    id_yes = await _create(bucket_mgr, yes)
    id_no = await _create(bucket_mgr, no)

    assert id_yes != id_no
    assert _body_on_disk(bucket_mgr, id_yes).strip() == yes
    assert _body_on_disk(bucket_mgr, id_no).strip() == no


@pytest.mark.asyncio
async def test_first_bucket_untouched_after_many_writes(bucket_mgr):
    """The oldest memory must stay put no matter how much lands on top of it.
    不管后面压进来多少条，最早那条必须原样待着。"""
    origin = "最开始我什么都不确定。"
    origin_id = await _create(bucket_mgr, origin)
    origin_body = _body_on_disk(bucket_mgr, origin_id)
    origin_meta_created = (await bucket_mgr.get(origin_id))["metadata"]["created"]

    for i in range(5):
        await _create(bucket_mgr, f"后来我越来越确定了，第 {i} 次这样想。")

    assert _body_on_disk(bucket_mgr, origin_id) == origin_body
    assert (await bucket_mgr.get(origin_id))["metadata"]["created"] == origin_meta_created


# ---------------------------------------------------------
# The write path itself: default config must never call dehydrator.merge
# 写入路径：默认配置下绝不能调用 dehydrator.merge
# ---------------------------------------------------------

@pytest.mark.asyncio
async def test_importer_default_never_merges(test_config, bucket_mgr, mock_dehydrator,
                                             mock_embedding_engine):
    """
    The importer had its own copy of the destructive merge. With the default
    config it must create instead, and must not touch dehydrator.merge at all.
    导入那条路以前也会毁历史，默认配置下必须改成新建，且完全不碰 merge。
    """
    from import_memory import ImportEngine

    importer = ImportEngine(test_config, bucket_mgr, mock_dehydrator, mock_embedding_engine)
    assert importer.semantic_merge_enabled is False, "导入器默认打开了语义合并"

    original = "我们第一次一起把服务器跑起来的那天。"
    first_id = await _create(bucket_mgr, original, domain=["日常"])
    body_before = _body_on_disk(bucket_mgr, first_id)

    # near-identical item — exactly the shape that used to trigger a merge
    merged = await importer._merge_or_create_item({
        "content": "我们第一次一起把服务器跑起来的那一天。",
        "domain": ["日常"],
        "tags": [],
        "importance": 5,
        "valence": 0.7,
        "arousal": 0.4,
        "name": "",
    })

    assert merged is False, "导入仍然报告发生了合并"
    mock_dehydrator.merge.assert_not_awaited()
    assert _body_on_disk(bucket_mgr, first_id) == body_before, "导入改写了已有记忆"


@pytest.mark.asyncio
async def test_importer_dedups_identical_reimport(test_config, bucket_mgr, mock_dehydrator,
                                                  mock_embedding_engine):
    """Re-importing the very same item must not pile up copies.
    同一条重复导入不该堆出好几份 —— 这是幂等，不是语义合并。"""
    from import_memory import ImportEngine

    importer = ImportEngine(test_config, bucket_mgr, mock_dehydrator, mock_embedding_engine)
    text = "把审计报告写完的那个早上。"
    item = {
        "content": text, "domain": ["日常"], "tags": [], "importance": 5,
        "valence": 0.6, "arousal": 0.3, "name": "",
    }

    await importer._merge_or_create_item(item)
    await importer._merge_or_create_item(dict(item))

    all_buckets = await bucket_mgr.list_all(include_archive=False)
    same = [b for b in all_buckets if b["content"].strip() == text]
    assert len(same) == 1, f"同一条被存了 {len(same)} 份"
    mock_dehydrator.merge.assert_not_awaited()
