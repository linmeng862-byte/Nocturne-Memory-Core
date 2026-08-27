# ============================================================
# Regression: nobody rewrites a body silently
# 回归测试：没有人能静默改写正文
#
# Phase 1 stopped the automatic destructive merge. This locks the remaining
# hole: bucket_mgr.update(content=…) could still overwrite any memory in
# place, with no revision, no journal, no way back — and api_bucket_update
# exposed that straight over HTTP.
# Phase 1 关掉了自动的破坏性合并。这里锁住剩下那个口子：
# update(content=…) 仍然能就地覆盖任何记忆，没有版本、没有记录、回不去，
# 而 api_bucket_update 把这个能力直接开在 HTTP 上。
#
# The rule / 规矩：
#   normal runtime            → create only, body edits refused
#   explicit privileged edit  → allowed, journalled, recoverable
# ============================================================

import json

import pytest

from bucket_manager import BucketRevision, HistoryProtected
from utils import content_fingerprint


def _body_on_disk(bucket_mgr, bucket_id):
    import frontmatter as fm
    return fm.load(bucket_mgr._find_bucket_file(bucket_id)).content


# ---------------------------------------------------------
# A revision has to be able to answer "who" and "why"
# ---------------------------------------------------------

def test_revision_requires_actor_and_reason():
    with pytest.raises(HistoryProtected):
        BucketRevision(actor="", reason="typo")
    with pytest.raises(HistoryProtected):
        BucketRevision(actor="粥粥", reason="")
    with pytest.raises(HistoryProtected):
        BucketRevision(actor="  ", reason="  ")
    rev = BucketRevision(actor="粥粥", reason="修掉一个错别字")
    assert rev.actor == "粥粥" and rev.reason == "修掉一个错别字"


# ---------------------------------------------------------
# Runtime is create-only
# ---------------------------------------------------------

@pytest.mark.asyncio
async def test_body_rewrite_without_revision_is_refused(bucket_mgr):
    original = "他说他会一直在。"
    bid = await bucket_mgr.create(content=original, domain=["内心"])

    with pytest.raises(HistoryProtected):
        await bucket_mgr.update(bid, content="他说他不确定。")

    assert _body_on_disk(bucket_mgr, bid).strip() == original, "被拒绝了却还是改到了盘上"
    assert bucket_mgr.revisions_for(bid) == [], "拒绝的修改不该留下记账"


@pytest.mark.asyncio
async def test_metadata_updates_still_work_without_revision(bucket_mgr):
    """Only the body is protected. Tags/importance/resolved are not history.
    只有正文受保护。标签、重要度、resolved 不是历史，照常可改。"""
    bid = await bucket_mgr.create(content="一条普通记忆。", domain=["日常"])
    ok = await bucket_mgr.update(bid, importance=9, tags=["新标签"], resolved=True)
    assert ok is not False
    got = await bucket_mgr.get(bid)
    assert got["metadata"]["importance"] == 9
    assert "新标签" in got["metadata"]["tags"]
    assert got["metadata"]["resolved"] is True


@pytest.mark.asyncio
async def test_noop_content_write_needs_no_revision(bucket_mgr):
    """Writing back the identical body changes no history, so it needs no
    authority — and must not produce a bogus revision record.
    原样写回没有改变任何历史，不需要授权，也不该记出一条假账。"""
    text = "一模一样的正文。"
    bid = await bucket_mgr.create(content=text, domain=["日常"])
    await bucket_mgr.update(bid, content=text)
    assert _body_on_disk(bucket_mgr, bid).strip() == text
    assert bucket_mgr.revisions_for(bid) == []


# ---------------------------------------------------------
# Privileged edits are allowed, journalled, and reversible
# ---------------------------------------------------------

@pytest.mark.asyncio
async def test_privileged_edit_is_journalled_and_recoverable(bucket_mgr):
    original = "我以为他记得的是我。"
    edited = "我以为他记得的是我。（今天补一句：也可能不是。）"
    bid = await bucket_mgr.create(content=original, domain=["内心"])

    await bucket_mgr.update(
        bid, content=edited,
        _revision=BucketRevision(actor="粥粥", reason="她自己补了一句"),
    )

    assert _body_on_disk(bucket_mgr, bid).strip() == edited

    rows = bucket_mgr.revisions_for(bid)
    assert len(rows) == 1
    row = rows[0]
    for field in ("revision_id", "bucket_id", "edited_at", "actor",
                  "reason", "previous_hash", "new_hash", "previous_content"):
        assert field in row, f"流水账缺字段 {field}"
    assert row["actor"] == "粥粥"
    assert row["previous_hash"] == content_fingerprint(original)
    assert row["new_hash"] == content_fingerprint(edited)

    # the whole point: the old wording is still there, not just its hash
    assert row["previous_content"].strip() == original, "旧正文没留下来，回不去"


@pytest.mark.asyncio
async def test_journal_accumulates_and_never_rewrites(bucket_mgr):
    """Three edits leave three records, oldest first. A later edit must not
    erase the earlier one — the journal is history too.
    改三次留三条，最早的在最前。后一次不能抹掉前一次 —— 流水账本身也是历史。"""
    bid = await bucket_mgr.create(content="第 0 版", domain=["日常"])
    for i in range(1, 4):
        await bucket_mgr.update(
            bid, content=f"第 {i} 版",
            _revision=BucketRevision(actor="粥粥", reason=f"第 {i} 次修改"),
        )

    rows = bucket_mgr.revisions_for(bid)
    assert len(rows) == 3
    assert [r["previous_content"] for r in rows] == ["第 0 版", "第 1 版", "第 2 版"]
    assert _body_on_disk(bucket_mgr, bid).strip() == "第 3 版"

    # every version ever written is reachable: journal + current body
    versions = [r["previous_content"] for r in rows] + [_body_on_disk(bucket_mgr, bid).strip()]
    assert versions == ["第 0 版", "第 1 版", "第 2 版", "第 3 版"]


@pytest.mark.asyncio
async def test_journal_is_written_before_the_body_changes(bucket_mgr, monkeypatch):
    """If journalling fails, the body must be left alone. Losing the old text
    is the exact failure this whole layer exists to prevent.
    记账失败就别动正文。丢掉旧文本正是这一层存在的理由。"""
    original = "不能被改掉的那句。"
    bid = await bucket_mgr.create(content=original, domain=["内心"])

    def boom(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(bucket_mgr, "_journal_revision", boom)

    with pytest.raises(OSError):
        await bucket_mgr.update(
            bid, content="改掉了",
            _revision=BucketRevision(actor="粥粥", reason="试试"),
        )

    assert _body_on_disk(bucket_mgr, bid).strip() == original


@pytest.mark.asyncio
async def test_revisions_for_filters_by_bucket(bucket_mgr):
    a = await bucket_mgr.create(content="A 的原文", domain=["日常"])
    b = await bucket_mgr.create(content="B 的原文", domain=["日常"])
    await bucket_mgr.update(a, content="A 改过",
                            _revision=BucketRevision(actor="粥粥", reason="改 A"))
    await bucket_mgr.update(b, content="B 改过",
                            _revision=BucketRevision(actor="粥粥", reason="改 B"))

    assert len(bucket_mgr.revisions_for(a)) == 1
    assert len(bucket_mgr.revisions_for(b)) == 1
    assert len(bucket_mgr.revisions_for()) == 2, "不传 bucket_id 应该给全部"
