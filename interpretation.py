from __future__ import annotations

# ============================================================
# Module: Interpretation (interpretation.py)
# 模块：解释层
#
# What somebody made of a memory. Above EPISODE, below NARRATIVE.
# 某人从一条记忆里读出了什么。在 EPISODE 之上,NARRATIVE 之下。
#
# The marks already existed and were already append-only, which is the hard
# part. What they lacked was a spine: a mark could not say who made it, and
# the conclusion drawn from a pile of marks was a single mutable flag on the
# memory itself.
# marks 本来就有,而且本来就是只增的 —— 难的那部分早就做对了。
# 缺的是骨头:一条 mark 说不出是谁标的,
# 而从一堆 mark 里得出的结论,是记忆本身上面一个可变的标记。
#
# ------------------------------------------------------------
# A reading is computed from the marks, never stored as the truth.
# 一个读法是从 marks 算出来的,绝不当作真相存下来。
#
# If the standing reading were the stored thing, then reversing it would
# destroy the fact that it was ever held. Recomputing means the marks stay the
# record and the reading is always just this moment's answer over them.
# 如果被存下来的是那个「当前读法」,那推翻它就会毁掉「它曾经被持有过」这个事实。
# 改成重算,marks 就一直是记录,而读法永远只是此刻对它们的一个回答。
#
# ------------------------------------------------------------
# Recognition is not a fact about the memory. It is a fact about a reader.
# 「认」不是关于那条记忆的事实,是关于某个读者的事实。
#
# So every mark says who made it and from which body. Two readers disagreeing
# is not a contradiction to be resolved — it is two true records.
# 所以每条 mark 都要说出是谁标的、从哪个身体。
# 两个读者不一致不是一个需要解决的矛盾 —— 那是两条都为真的记录。
# ============================================================

import json
import logging
from datetime import datetime

from utils import append_jsonl

logger = logging.getLogger("ombre_brain.interpretation")

AUTHORITY = "interpretation"

RECOGNIZED = "认"
REJECTED = "不认"
SUSPENDED = "悬置"
VALID_MARKS = (RECOGNIZED, REJECTED, SUSPENDED)

# Older marks that set the reading outright instead of voting toward it.
# 更早的 mark:它们直接**指定**读法,而不是投一票。
MARK_INNER = "inner"
MARK_REMOVE_INNER = "remove_inner"
DIRECT_MARKS = (MARK_INNER, MARK_REMOVE_INNER)

LEGACY_ACTOR = "legacy:domain"

# Thresholds the existing runtime already used. Kept here so the rule is
# stated in one place and can be cited by the record it produces.
# 现有运行时本来就在用的阈值。挪到这里,规矩只写一处,
# 而且它产出的记录可以引用到它。
RECOGNITION_THRESHOLD = 3
REJECTION_THRESHOLD = 2
CROSS_DATE_REQUIRED = 2

LOG_NAME = "interpretation_log.jsonl"

READING_INNER = "inner"
READING_NOT_INNER = "not_inner"
READING_UNDECIDED = "undecided"


def log_path(buckets_dir: str) -> str:
    import os
    return os.path.join(buckets_dir, LOG_NAME)


def _mark_of(row) -> str:
    return str((row or {}).get("mark") or "").strip()


def standing_reading(mark_rows: list[dict], legacy_inner: bool = False) -> dict:
    """The current reading over a pile of marks, and why.

    Deterministic and explaining itself: a reading nobody can interrogate is
    indistinguishable from one that was made up.
    确定性的,而且自己说得出理由:一个没法追问的读法,
    跟一个编出来的读法没有区别。

    `legacy_inner` carries forward a memory that was already being read as
    inner before this layer existed. It is evidence, not an override: two
    later rejections still turn it around. Without it, closing the valve
    would silently demote every memory whose promotion predates the marks
    table — a behaviour loss disguised as a refactor.
    `legacy_inner` 把「这一层出现之前就已经被当成 inner 的记忆」带过来。
    它是**证据**,不是压制:后来两次「不认」照样能翻掉它。
    没有它,封阀门会静默降级掉每一条升级早于 marks 表的记忆 ——
    一次伪装成重构的行为丢失。
    """
    counts = {RECOGNIZED: 0, REJECTED: 0, SUSPENDED: 0}
    direct = {MARK_INNER: 0, MARK_REMOVE_INNER: 0}
    recognition_dates = set()
    for row in mark_rows or []:
        mark = _mark_of(row)
        if mark in direct:
            direct[mark] += 1
            continue
        if mark not in counts:
            continue
        counts[mark] += 1
        if mark == RECOGNIZED:
            stamp = str(row.get("timestamp") or "")[:10]
            if len(stamp) == 10:
                recognition_dates.add(stamp)

    cross_date = len(recognition_dates) >= CROSS_DATE_REQUIRED

    # Rejection is checked first: the existing runtime demotes after promoting,
    # so a memory carrying both counts ends up out. Stating the order here
    # makes it a rule rather than an accident of statement ordering.
    # 先看「不认」:现有运行时是先升后降,所以两边都够数的记忆最后是出局的。
    # 在这里把顺序写明,它就是一条规矩,而不是语句先后顺序造成的偶然。
    if counts[REJECTED] >= REJECTION_THRESHOLD or direct[MARK_REMOVE_INNER]:
        reading = READING_NOT_INNER
    elif direct[MARK_INNER] or legacy_inner:
        reading = READING_INNER
    elif counts[RECOGNIZED] >= RECOGNITION_THRESHOLD and cross_date:
        reading = READING_INNER
    else:
        reading = READING_UNDECIDED

    return {
        "authority": AUTHORITY,
        "reading": reading,
        "counts": dict(counts),
        "direct": dict(direct),
        "legacy_inner": bool(legacy_inner),
        # Recognising something on three separate days is a different event
        # from recognising it three times in one sitting, and only the first
        # is evidence of it holding up over time.
        # 在三个不同的日子认出同一件事,跟一口气认三次,是不同的事件;
        # 只有前者能说明它经得住时间。
        "recognition_dates": sorted(recognition_dates),
        "crossed_dates": cross_date,
        "thresholds": {
            "recognition": RECOGNITION_THRESHOLD,
            "rejection": REJECTION_THRESHOLD,
            "cross_date": CROSS_DATE_REQUIRED,
        },
    }


def record_transition(buckets_dir: str, bucket_id: str, *, was: str, now_reading: str,
                      basis: dict, actor: str = "", endpoint: str = "",
                      at: datetime | None = None) -> dict:
    """Record that a standing reading changed, and what justified it.

    The runtime demotes a memory by deleting "inner" from its domain list,
    which erases the fact that it was ever promoted. This is where that fact
    survives: a reversal is a new entry, never a deletion.
    运行时降级的做法是把 "inner" 从 domain 列表里删掉,
    这会抹掉「它曾经被升上去过」这个事实。那个事实活在这里:
    推翻是一条新记录,绝不是一次删除。
    """
    row = {
        "bucket_id": str(bucket_id or "").strip(),
        "authority": AUTHORITY,
        "at": (at or datetime.now()).isoformat(timespec="seconds"),
        "was": str(was or ""),
        "now": str(now_reading or ""),
        "actor": str(actor or "").strip(),
        "endpoint": str(endpoint or "").strip(),
        "basis": basis,
    }
    append_jsonl(log_path(buckets_dir), row)
    return row


def history(buckets_dir: str, bucket_id: str = "") -> list[dict]:
    """Every reading this memory has been held under, oldest first."""
    import os
    path = log_path(buckets_dir)
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not bucket_id or row.get("bucket_id") == bucket_id:
                rows.append(row)
    return rows


def was_ever(buckets_dir: str, bucket_id: str, reading: str) -> bool:
    """Whether this memory was ever held under a given reading.

    The question the mutable flag cannot answer: it only knows about now.
    那个可变标记回答不了的问题:它只知道现在。
    """
    return any(r.get("now") == reading for r in history(buckets_dir, bucket_id))


def grandfather_inner(buckets_dir: str, bucket_id: str, at: datetime | None = None) -> bool:
    """Claim a memory that was already being read as inner before this layer.

    Idempotent: a memory is grandfathered once and then the log is the record.
    Recorded rather than assumed, so "why is this inner" has an answer even
    for the ones nobody alive remembers marking.
    幂等:一条记忆只认领一次,之后账本就是记录。
    是**记下来**而不是默认,所以「它为什么是 inner」有答案 ——
    连那些没人记得是谁标过的,也有。
    """
    for row in history(buckets_dir, bucket_id):
        if row.get("actor") == LEGACY_ACTOR:
            return False
    record_transition(
        buckets_dir, bucket_id,
        was=READING_UNDECIDED, now_reading=READING_INNER,
        basis={"source": "domain_field",
               "note": "已经在被当作 inner 读,早于解释层 / read as inner before this layer"},
        actor=LEGACY_ACTOR, at=at,
    )
    return True


def legacy_inner_ids(buckets_dir: str) -> set[str]:
    """Every memory that was grandfathered in. Loaded once, not per bucket."""
    return {r.get("bucket_id") for r in history(buckets_dir)
            if r.get("actor") == LEGACY_ACTOR and r.get("bucket_id")}
