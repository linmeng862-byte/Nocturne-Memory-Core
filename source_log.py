from __future__ import annotations

# ============================================================
# Module: Source log (source_log.py)
# 模块：原始话语账
#
# What was actually said. Nothing else lives here.
# 真的说过的话。别的什么都不放在这里。
#
# The residue ledger already stored utterances, but it stored them in the same
# row, at the same level, as the drive classification derived from them —
# primary_drive, intensity, territorial_alarm. Same schema, no boundary. A
# later layer reading that row cannot tell which half is a fact and which half
# is somebody's reading of the fact.
# 残留账本来就存了话语，但它把话语跟从话语推出来的 drive 判断存在同一行、同一层级 ——
# primary_drive、intensity、territorial_alarm。一个 schema，没有边界。
# 后面的层读到那一行，分不出哪半边是事实、哪半边是某人对事实的读法。
#
# So they are split. This file holds the fact. The ledger holds the reading,
# and points back here.
# 所以劈开。这个文件放事实。账本放读法，并指回这里。
#
# ------------------------------------------------------------
# There is deliberately no update(), no delete(), and no merge().
# 这里刻意没有 update()、没有 delete()、也没有 merge()。
#
# Not "there is one but callers shouldn't use it" — there is no function to
# call. Mutation authority is a property of the storage layer, not of caller
# discipline; a rule that lives only in a docstring is a rule that a future
# refactor deletes without noticing.
# 不是「有一个但调用方不该用」—— 是根本没有这个函数可调。
# 改写权限是存储层的性质，不是调用方的自觉；
# 只写在 docstring 里的规矩，将来某次重构会毫无察觉地把它删掉。
#
# This is the one-way valve: interpretation can read source, source can never
# be edited by interpretation. Zero out-edges.
# 这就是单向阀：解释可以读原始，原始永远不能被解释改动。出边为零。
# ============================================================

import os
import json
import hashlib
import logging

from utils import exclusive, LockTimeout

logger = logging.getLogger("ombre_brain.source_log")

LOG_NAME = "utterances.jsonl"

# Utterances arrive through overlapping 2+2 windows, so the same sentence is
# submitted several times. It is one event; it gets one id.
# 话语是通过重叠的 2+2 滑窗提交的，同一句会被交上来好几次。
# 它是一个事件，就只该有一个 id。


def log_path(buckets_dir: str) -> str:
    return os.path.join(buckets_dir, LOG_NAME)


def utterance_id(role: str, text: str, ts: str = "") -> str:
    """Identity of an utterance is its content, not when it was submitted.

    Content identity, never similarity. Two utterances that merely resemble
    each other are two utterances — that is the whole lesson of Phase 1.
    话语的身份是它的内容，不是它什么时候被交上来的。
    内容同一性，绝不是相似度。两句只是像的话就是两句话 —— 这是 Phase 1 的全部教训。
    """
    packed = json.dumps(
        {"role": str(role or ""), "text": str(text or ""), "ts": str(ts or "")},
        ensure_ascii=False, sort_keys=True,
    )
    return hashlib.sha256(packed.encode("utf-8")).hexdigest()[:24]


def _existing_ids(path: str) -> set[str]:
    seen = set()
    if not os.path.exists(path):
        return seen
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                uid = json.loads(line).get("utterance_id")
            except json.JSONDecodeError:
                continue
            if uid:
                seen.add(uid)
    return seen


def record(buckets_dir: str, messages: list[dict], window_id: str = "",
           endpoint: str = "") -> list[str]:
    """Append utterances that have not been recorded before; return every id.

    Returns ids for *all* the messages passed in, already-known ones included,
    so a caller can always say what its interpretation was derived from —
    whether or not this particular call was the one that first stored them.
    返回传进来的**所有**消息的 id，包括早就记过的，
    这样调用方永远说得出自己的解释是从哪些话推出来的 ——
    不管这次调用是不是第一次存下它们的那一次。
    """
    path = log_path(buckets_dir)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    rows, ids = [], []
    for m in messages if isinstance(messages, list) else []:
        if not isinstance(m, dict):
            continue
        text = str(m.get("text") or m.get("content") or "").strip()
        if not text:
            continue
        role = str(m.get("role") or "").strip().lower()
        ts = str(m.get("ts") or m.get("created_at") or "").strip()
        uid = utterance_id(role, text, ts)
        ids.append(uid)
        rows.append({
            "utterance_id": uid,
            "authority": "source",
            "role": role,
            "speaker": str(m.get("speaker") or "").strip(),
            "text": text,
            "ts": ts,
            "window_id": str(window_id or "").strip(),
            "endpoint": str(endpoint or "").strip(),
        })

    if not rows:
        return ids

    try:
        with exclusive(path):
            known = _existing_ids(path)
            fresh = []
            for r in rows:
                if r["utterance_id"] in known:
                    continue
                known.add(r["utterance_id"])  # also dedups within this batch
                fresh.append(r)
            if fresh:
                with open(path, "a", encoding="utf-8") as f:
                    for r in fresh:
                        f.write(json.dumps(r, ensure_ascii=False) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
    except LockTimeout as e:
        # Source is the one thing that must not be lost. Say so loudly rather
        # than return ids for utterances that were never stored.
        # 原始是最不能丢的东西。宁可大声报错，
        # 也不要为一批根本没存下来的话返回 id。
        logger.error(f"Source log gave up waiting for lock / 原始账等锁超时: {e}")
        raise

    return ids


def read_all(buckets_dir: str, utterance_ids: list[str] | None = None) -> list[dict]:
    """Read utterances back, in the order they were first recorded."""
    path = log_path(buckets_dir)
    if not os.path.exists(path):
        return []
    wanted = set(utterance_ids) if utterance_ids else None
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if wanted is None or row.get("utterance_id") in wanted:
                out.append(row)
    return out


def resolve(buckets_dir: str, utterance_ids: list[str]) -> dict:
    """Map ids → utterance rows, so a derived claim can be checked against
    what was actually said.
    id → 话语行，这样一条推导出来的说法可以拿回去跟真的说过的话对一下。"""
    return {r["utterance_id"]: r for r in read_all(buckets_dir, utterance_ids)}
