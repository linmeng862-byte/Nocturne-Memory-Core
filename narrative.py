from __future__ import annotations

# ============================================================
# Module: Narrative threads (narrative.py)
# 模块：叙事线
#
# A through-line somebody drew across memories: a title, a core question, and
# the memories held to belong to it. Above INTERPRETATION, below CURRENT
# BELIEF.
# 某人在一堆记忆之间画出的一条贯穿线:一个标题、一个核心问题、
# 以及被认为属于它的那些记忆。在 INTERPRETATION 之上,CURRENT BELIEF 之下。
#
# ------------------------------------------------------------
# At this layer the revision IS the content.
# 在这一层,修订本身就是内容。
#
# Lower down, history matters as evidence — you keep the old wording so a
# later claim can be checked against it. Here it matters as the subject. How
# the story someone tells about themselves changed is not metadata about the
# story; it is the most consequential thing this layer records.
# 再往下,历史的意义是**证据** —— 留着旧措辞,好让后来的说法可以拿回去对。
# 在这里,历史的意义是**主题本身**。
# 一个人讲给自己听的故事是怎么变的,不是关于这个故事的元数据;
# 它是这一层记下的最要紧的东西。
#
#   "我以为这是关于我不敢开口"
#   "我现在觉得这是关于我在等一个许可"
#
# That change is an event in a life. The store used to record it as
# `revision: 2` and drop the earlier sentence — a counter that says something
# changed while keeping nothing of what changed, which is the same shape as a
# hash with no previous body.
# 那个变化是一件人生事件。存储层以前把它记成 `revision: 2`,然后把先前那句丢掉 ——
# 一个「说变过了却什么都没留下」的计数器,跟「只有 hash 没有旧正文」是同一个形状。
#
# ------------------------------------------------------------
# Retelling is not an edit to be authorised. It is the point.
# 重讲不是一次需要授权的修改。它就是这一层存在的意义。
#
# A body edit on a memory has to justify itself, because memories are not
# supposed to change. A narrative is supposed to change; demanding a reason
# every time would make the natural act bureaucratic. What is required is only
# that the earlier telling survives.
# 改一条记忆的正文必须自证,因为记忆本来就不该变。
# 叙事本来就该变;每次都要求给理由,会把一件自然的事变成一道手续。
# 唯一的要求是:先前那个讲法要活下来。
# ============================================================

import os
import json
import logging
from datetime import datetime

from utils import append_jsonl

logger = logging.getLogger("ombre_brain.narrative")

AUTHORITY = "narrative"
LOG_NAME = "narrative_log.jsonl"

# What happened to a thread.
RETOLD = "retold"            # its title or core question changed
RETIRED = "retired"          # it stopped being a thread somebody holds
MEMBER_DROPPED = "member_dropped"   # a memory stopped belonging to it
QUERY_DROPPED = "query_dropped"     # a way of finding it stopped being kept
VALID_KINDS = {RETOLD, RETIRED, MEMBER_DROPPED, QUERY_DROPPED}


def log_path(buckets_dir: str) -> str:
    return os.path.join(buckets_dir, LOG_NAME)


def record(buckets_dir: str, family_id: str, kind: str, *,
           was, now=None, actor: str = "", reason: str = "",
           revision: int = 0, at: datetime | None = None) -> dict:
    """Keep what a thread used to be, before it becomes something else.

    Called inside the store's lock and before the change lands, so a failure
    here means the earlier telling is still the current one rather than gone.
    在存储层的锁**内**、改动落盘**之前**调用,
    所以这里失败的结果是「先前那个讲法仍然是当前的」,而不是「它没了」。
    """
    if kind not in VALID_KINDS:
        raise ValueError(f"unknown narrative event: {kind}")
    row = {
        "family_id": str(family_id or "").strip(),
        "authority": AUTHORITY,
        "kind": kind,
        "at": (at or datetime.now()).isoformat(timespec="seconds"),
        "revision": int(revision or 0),
        "actor": str(actor or "").strip(),
        "reason": str(reason or "").strip(),
        "was": was,
        "now": now,
    }
    append_jsonl(log_path(buckets_dir), row)
    return row


def history(buckets_dir: str, family_id: str = "") -> list[dict]:
    """Everything that has happened to a thread, oldest first."""
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
            if not family_id or row.get("family_id") == family_id:
                rows.append(row)
    return rows


def tellings(buckets_dir: str, family_id: str, current: dict | None = None) -> list[dict]:
    """Every way this thread has been told, oldest first.

    The current telling is passed in rather than read, because the store is
    the authority on what it is now and this module is the authority only on
    what it was.
    当前的讲法是**传进来**的而不是读出来的:
    存储层才是「它现在是什么」的权威,这个模块只是「它曾经是什么」的权威。
    """
    out = []
    for row in history(buckets_dir, family_id):
        if row.get("kind") not in (RETOLD, RETIRED):
            continue
        was = row.get("was") or {}
        out.append({
            "title": was.get("title", ""),
            "core_question": was.get("core_question", ""),
            "until": row.get("at", ""),
            "revision": row.get("revision", 0),
            "ended_by": row.get("kind"),
        })
    if current:
        out.append({
            "title": current.get("title", ""),
            "core_question": current.get("core_question", ""),
            "until": "",
            "revision": current.get("revision", 0),
            "ended_by": "",
        })
    return out


def dropped_members(buckets_dir: str, family_id: str) -> list[dict]:
    """Memories that were once held to belong to this thread.

    A memory removed from a thread was still, for a while, part of that story.
    Nothing else records that it ever was.
    一条被从叙事线里移走的记忆,曾经有一段时间**是**那个故事的一部分。
    没有别的地方记着它曾经是。
    """
    return [
        {"node_ref": (r.get("was") or {}).get("node_ref", ""),
         "query": (r.get("was") or {}).get("query", ""),
         "dropped_at": r.get("at", "")}
        for r in history(buckets_dir, family_id)
        if r.get("kind") == MEMBER_DROPPED
    ]


def was_ever_told_as(buckets_dir: str, family_id: str, text: str) -> bool:
    """Whether this thread ever carried a given title or core question."""
    needle = str(text or "").strip()
    if not needle:
        return False
    for row in history(buckets_dir, family_id):
        was = row.get("was") or {}
        if needle in (str(was.get("title") or "").strip(),
                      str(was.get("core_question") or "").strip()):
            return True
    return False
