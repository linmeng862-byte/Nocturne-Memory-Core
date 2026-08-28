from __future__ import annotations

# ============================================================
# Module: Recall journal (recall_journal.py)
# 模块：召回流水账
#
# Retrieval is not a touch. A memory that was scored, ranked, and then cut
# from the bundle was never remembered — nothing happened to it. Recording a
# touch at search time would mean the system's own housekeeping slowly
# rewrites which memories look important.
# 被检索到不等于被触碰。一条记忆被打了分、排了序、然后没进最终 bundle，
# 那它根本没有被想起来 —— 什么都没发生在它身上。
# 如果在检索阶段就记账，系统自己的日常维护就会慢慢改写「哪些记忆看起来重要」。
#
# So the journal is append-only, idempotent, and only ever written after
# something actually reached the agent.
# 所以这本账是 append-only、幂等的，而且只在东西真的到达 agent 之后才写。
#
# It records involuntary and deliberate touches separately. That distinction
# is the only honest raw material for texture: wear comes from being returned
# to, and going back on purpose is not the same as having something surface
# on you.
# 不由自主和刻意分开记。这个区分是质地唯一诚实的原料：
# 磨损来自被反复回去找，而特地回去找，跟它自己浮上来，不是一回事。
# ============================================================

import os
import json
import logging
from datetime import datetime

from utils import exclusive, write_atomic, LockTimeout, LOCK_TIMEOUT_SECONDS

logger = logging.getLogger("ombre_brain.recall_journal")

JOURNAL_NAME = "recall_events.jsonl"
ENCOUNTER_NAME = "last_encounter.json"

# An endpoint that says it is not a body. Maintenance, audits, rehearsals and
# probes all read the same tools a real endpoint does, with the same
# credentials — the server cannot tell them apart and must not pretend it can.
# So the honest tool declares itself, and the ledger takes it at its word.
#
# 一个声明「我不是一个身体」的 endpoint。运维、审计、排练、探针,用的是跟真身体
# 一样的工具和一样的凭据 —— 服务端**分辨不出来**,也不该假装分辨得出。
# 所以由诚实的那一方自己声明,账本采信它的说法。
#
# The touch is still journalled either way: what a probe read is a fact, and
# hiding it would make the audit trail lie in the other direction. Only
# `last_encounter` is withheld, because that one answers a different question
# — "when was he last here" — and a probe was never here.
# 流水账照记:探针读了什么是事实,瞒下来等于让审计记录朝另一个方向说谎。
# 只有 `last_encounter` 不写,因为它回答的是另一个问题 ——「他上次在场是什么时候」,
# 而探针从来不在场。
PROBE_PREFIX = "probe:"


def is_presence(endpoint: str) -> bool:
    """Whether this touch means the agent was actually here."""
    return not str(endpoint or "").strip().lower().startswith(PROBE_PREFIX)


def journal_path(buckets_dir: str) -> str:
    return os.path.join(buckets_dir, JOURNAL_NAME)


def encounter_path(buckets_dir: str) -> str:
    return os.path.join(buckets_dir, ENCOUNTER_NAME)


def _seen_recall_ids(path: str) -> set[str]:
    seen = set()
    if not os.path.exists(path):
        return seen
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            rid = row.get("recall_id")
            if rid:
                seen.add(rid)
    return seen


def record_touch(buckets_dir: str, recall_id: str, mode: str,
                 bucket_ids: list[str], endpoint: str = "",
                 session_id: str = "", now: datetime | None = None,
                 lock_timeout: float | None = None) -> dict:
    """Record that these memories actually reached the agent.

    Idempotent on recall_id: a retried confirm from a flaky client must not
    make a memory look twice as touched as it was.
    对 recall_id 幂等：客户端网络抖动重发一次确认，
    不能让一条记忆看起来被触碰了两次。
    """
    from recall import VALID_MODES

    recall_id = str(recall_id or "").strip()
    if not recall_id:
        raise ValueError("recall_id is required — touch must be idempotent")
    if mode not in VALID_MODES:
        raise ValueError(f"unknown recall mode: {mode}")

    bucket_ids = [str(b).strip() for b in (bucket_ids or []) if str(b).strip()]
    path = journal_path(buckets_dir)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    stamp = (now or datetime.now()).isoformat(timespec="seconds")
    row = {
        "recall_id": recall_id,
        "mode": mode,
        "at": stamp,
        "bucket_ids": bucket_ids,
        # Which body was this said to. Matters once the same memory is shared
        # by a phone, a terminal and a chat window.
        # 这次是对哪个身体说的。等同一份记忆被手机、终端和聊天窗共用时就要紧了。
        "endpoint": str(endpoint or "").strip(),
        "session_id": str(session_id or "").strip(),
    }
    # The dedup check and the append must be one indivisible step. Checking
    # first and appending after is a race: two endpoints confirming the same
    # recall_id both read "not seen yet", and both write. Idempotence that only
    # holds inside a single process is not idempotence once this is a URL.
    # 查重和追加必须是同一个不可分的步骤。先查后写是竞态：
    # 两个端同时确认同一个 recall_id，都读到「没见过」，于是都写。
    # 只在单进程里成立的幂等，等它变成一个网址之后就不叫幂等了。
    try:
        with exclusive(path, timeout=LOCK_TIMEOUT_SECONDS if lock_timeout is None
                       else lock_timeout):
            if recall_id in _seen_recall_ids(path):
                logger.info(f"Duplicate confirm ignored / 重复确认已忽略: {recall_id}")
                return {"recorded": False, "duplicate": True, "recall_id": recall_id}
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
            # 「他上次在场」只由真的在场的那一方改写。见 PROBE_PREFIX。
            if is_presence(endpoint):
                _write_encounter(buckets_dir, stamp, endpoint, session_id)
    except LockTimeout as e:
        # Losing a touch is survivable; double-counting one corrupts the only
        # honest evidence a texture layer will ever have. Fail loudly.
        # 丢一次触碰还能活;重复计一次会污染将来质地唯一诚实的证据。宁可报错。
        logger.error(f"Touch gave up waiting for lock / 记账等锁超时: {recall_id}: {e}")
        raise
    return {"recorded": True, "duplicate": False, "recall_id": recall_id,
            "touched": len(bucket_ids)}


def _write_encounter(buckets_dir: str, stamp: str, endpoint: str, session_id: str) -> None:
    path = encounter_path(buckets_dir)
    payload = {"at": stamp, "endpoint": endpoint, "session_id": session_id}
    write_atomic(path, json.dumps(payload, ensure_ascii=False))


def last_encounter(buckets_dir: str) -> str:
    """When the agent was last actually present. Empty string if never."""
    path = encounter_path(buckets_dir)
    if not os.path.exists(path):
        return ""
    try:
        with open(path, encoding="utf-8") as f:
            return str(json.load(f).get("at") or "")
    except (OSError, json.JSONDecodeError, AttributeError):
        return ""


def read_events(buckets_dir: str, bucket_id: str = "") -> list[dict]:
    path = journal_path(buckets_dir)
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
            if not bucket_id or bucket_id in (row.get("bucket_ids") or []):
                rows.append(row)
    return rows


def touch_summary(buckets_dir: str) -> dict:
    """Per-bucket touch counts, split by mode, plus the last touch time.

    This is what a future texture layer would grow out of — and deliberately
    nothing more. It counts what happened; it does not say what it means.
    将来的质地就从这里长出来 —— 而且有意到此为止。
    它只数发生了什么，不解释那意味着什么。
    """
    summary: dict[str, dict] = {}
    for row in read_events(buckets_dir):
        mode = row.get("mode", "")
        at = row.get("at", "")
        for bid in row.get("bucket_ids") or []:
            entry = summary.setdefault(bid, {
                "involuntary": 0, "deliberate": 0, "last_touch": "",
            })
            if mode in entry:
                entry[mode] += 1
            if at > entry["last_touch"]:
                entry["last_touch"] = at
    return summary
