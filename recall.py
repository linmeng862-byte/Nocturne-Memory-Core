from __future__ import annotations

# ============================================================
# Module: Recall (recall.py)
# 模块：召回
#
# Remembering is not a decision. A person walks into a kitchen, smells
# something, and the afternoon is simply there — they did not run a lookup.
# So recall has two modes, and they are different acts:
# 想起来不是一个决定。人走进厨房闻到一个味道，那个下午就已经在那儿了 ——
# 没有谁执行了一次查询。所以召回有两种，而且是两件不同的事：
#
#   INVOLUNTARY  the surrounding system pulls a bundle before the agent
#                sees the turn. The agent does not choose to remember; it
#                arrives already carrying something.
#                不由自主：外面的系统在 agent 看见这一轮之前就取好了。
#                它不是想起来的，它是本来就带着的。
#
#   DELIBERATE   the agent goes looking. "Wait — didn't we talk about this?"
#                刻意：agent 自己去找。「等等，我们是不是聊过这个」
#
# Both read. Neither writes. Touch is recorded only after something actually
# reaches the agent, and the two modes are recorded separately, because
# "it surfaced on me" and "I went back for it" are not the same event and
# should not leave the same mark.
# 两种都只读，都不写。只有真的到达 agent 之后才记账，而且两种分开记 ——
# 「它自己浮上来」和「我特地回去找它」不是同一件事，不该留下同样的痕。
#
# That separation is the raw material for texture later: wear is what
# repeated touching leaves behind, and you cannot tell wear from habit
# unless you know which touches cost something.
# 这个区分是将来质地的原料：磨损是反复触碰留下的东西，
# 而如果分不清哪些触碰是费力的，就分不出磨损和习惯。
#
# Depended on by: server.py
# ============================================================

import re
import math
from datetime import datetime, timedelta

# Recall modes. Kept as plain strings so they survive JSON round-trips.
INVOLUNTARY = "involuntary"
DELIBERATE = "deliberate"
VALID_MODES = {INVOLUNTARY, DELIBERATE}


# ---------------------------------------------------------
# Selection signals
# 选择信号
#
# The old breath took the newest 30, weighted 12, then picked 7 at random.
# Randomness meant two wake-ups saw different pasts for no reason, and
# nothing connected what surfaced to what was actually unfinished.
# 老的 breath 是「最近 30 → 加权 12 → 随机 7」。随机意味着两次醒来
# 看见的过去不一样，而且没有任何东西把浮现的内容和真正未完成的事连起来。
# ---------------------------------------------------------

# Weights are deliberately few. Every extra signal is another thing that can
# quietly decide what the agent gets to remember.
W_UNFINISHED = 3.0
W_QUERY = 2.5
W_SALIENCE = 1.2
W_RECENCY = 1.0
W_NEGLECT = 0.8

RECENCY_HALFLIFE_DAYS = 10.0
NEGLECT_FULL_DAYS = 30.0


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)[:19])
    except (ValueError, TypeError):
        return None


def _terms(text: str) -> set[str]:
    """Crude bilingual tokenisation: latin words plus CJK bigrams.

    Good enough to notice topic overlap and cheap enough to run over every
    bucket. It is a candidate signal, never an identity check.
    够用来看出话题重合，便宜到可以扫全部桶。它只是候选信号，不做同一性判断。
    """
    raw = str(text or "").lower()
    words = set(re.findall(r"[a-z0-9_]{2,}", raw))
    cjk = re.findall(r"[一-鿿]+", raw)
    for run in cjk:
        for i in range(len(run) - 1):
            words.add(run[i:i + 2])
        if len(run) == 1:
            words.add(run)
    return words


def unfinishedness(bucket: dict, mark_rows: list[dict] | None = None) -> float:
    """How much this still has an open end. Highest signal we have.
    这条还有多少没了结。我们手上最强的信号。"""
    meta = bucket.get("metadata", {})
    if meta.get("resolved"):
        return 0.0
    score = 0.0
    labels = {str(d).lower() for d in (meta.get("domain") or [])}
    labels |= {str(t).lower() for t in (meta.get("tags") or [])}
    if "unresolved" in labels:
        score += 1.0
    suspended = sum(1 for r in (mark_rows or [])
                    if str(r.get("mark", "")).strip() == "悬置")
    if suspended:
        score += min(0.6, 0.3 * suspended)
    if not meta.get("digested"):
        score += 0.2
    return min(1.0, score)


def salience(bucket: dict) -> float:
    meta = bucket.get("metadata", {})
    try:
        importance = max(1, min(10, int(meta.get("importance", 5))))
    except (TypeError, ValueError):
        importance = 5
    try:
        arousal = max(0.0, min(1.0, float(meta.get("arousal", 0.3))))
    except (TypeError, ValueError):
        arousal = 0.3
    return 0.7 * (importance / 10.0) + 0.3 * arousal


def recency(bucket: dict, now: datetime) -> float:
    created = _parse_dt(bucket.get("metadata", {}).get("created"))
    if not created:
        return 0.0
    days = max(0.0, (now - created).total_seconds() / 86400.0)
    return math.pow(0.5, days / RECENCY_HALFLIFE_DAYS)


def neglect(bucket: dict, now: datetime, last_touch: datetime | None) -> float:
    """Rewards what has not been looked at in a long time.

    Without this the same handful of recent memories would surface every
    single wake-up and the rest of the past would go dark while still being
    on disk. This is the counterweight to recency, not a duplicate of it.
    没有这个的话，每次醒来浮上来的永远是那几条最近的，
    其余的过去明明还在盘上却再也不出现。它是 recency 的对重，不是它的重复。
    """
    reference = last_touch or _parse_dt(bucket.get("metadata", {}).get("created"))
    if not reference:
        return 0.0
    days = max(0.0, (now - reference).total_seconds() / 86400.0)
    return min(1.0, days / NEGLECT_FULL_DAYS)


def query_overlap(bucket: dict, query_terms: set[str]) -> float:
    if not query_terms:
        return 0.0
    meta = bucket.get("metadata", {})
    hay = _terms(bucket.get("content", ""))
    hay |= _terms(" ".join(str(t) for t in (meta.get("tags") or [])))
    hay |= _terms(str(meta.get("name") or ""))
    if not hay:
        return 0.0
    hits = len(query_terms & hay)
    return min(1.0, hits / max(3.0, len(query_terms) ** 0.5))


def score_bucket(bucket: dict, now: datetime, query_terms: set[str],
                 mark_rows: list[dict] | None = None,
                 last_touch: datetime | None = None) -> dict:
    """Return the score together with its parts.

    The breakdown is returned, not just the number, so that "why did I get
    this memory?" always has an answer. A recall you cannot interrogate is
    indistinguishable from one that made it up.
    返回分数连同它的构成。这样「为什么给我这条」永远答得出来。
    一个问不出理由的召回，跟编出来的没有区别。
    """
    parts = {
        "unfinished": unfinishedness(bucket, mark_rows),
        "query": query_overlap(bucket, query_terms),
        "salience": salience(bucket),
        "recency": recency(bucket, now),
        "neglect": neglect(bucket, now, last_touch),
    }
    total = (
        parts["unfinished"] * W_UNFINISHED
        + parts["query"] * W_QUERY
        + parts["salience"] * W_SALIENCE
        + parts["recency"] * W_RECENCY
        + parts["neglect"] * W_NEGLECT
    )
    return {"score": round(total, 4), "parts": {k: round(v, 4) for k, v in parts.items()}}


def select(buckets: list[dict], now: datetime, query: str = "", limit: int = 7,
           marks_by_bucket: dict | None = None,
           touches_by_bucket: dict | None = None) -> list[dict]:
    """Deterministic selection. Same inputs, same past.

    Ties break on bucket id so two wake-ups a second apart cannot disagree
    about what mattered.
    确定性选择：输入一样，看到的过去就一样。
    并列时按 id 决胜，免得相隔一秒的两次醒来对「什么重要」给出不同答案。
    """
    marks_by_bucket = marks_by_bucket or {}
    touches_by_bucket = touches_by_bucket or {}
    query_terms = _terms(query)

    scored = []
    for bucket in buckets:
        bid = bucket.get("id", "")
        result = score_bucket(
            bucket, now, query_terms,
            mark_rows=marks_by_bucket.get(bid),
            last_touch=_parse_dt(touches_by_bucket.get(bid)),
        )
        scored.append({
            "bucket": bucket,
            "id": bid,
            "score": result["score"],
            "parts": result["parts"],
        })

    scored.sort(key=lambda row: (-row["score"], row["id"]))
    return scored[:max(0, int(limit))]


# ---------------------------------------------------------
# Elapsed time — a fact, not a feeling
# 过去了多久 —— 事实，不是感受
# ---------------------------------------------------------

def describe_elapsed(last_encounter, now: datetime) -> dict:
    """Say how long it has been. Say nothing about what it was like.

    "12 days have passed" is something the system knows. "I spent those 12
    days thinking about you" is something it made up. The first belongs in
    the bundle; the second must never be generated from a clock.
    「过去了 12 天」是系统知道的。「这 12 天我一直在想你」是它编的。
    前者进 bundle；后者绝不许从一个时钟里生出来。
    """
    last = last_encounter if isinstance(last_encounter, datetime) else _parse_dt(last_encounter)
    if not last:
        return {"known": False, "now": now.isoformat(timespec="seconds")}
    delta = now - last
    seconds = max(0.0, delta.total_seconds())
    days, rem = divmod(int(seconds), 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        phrase = f"{days} 天 {hours} 小时"
    elif hours:
        phrase = f"{hours} 小时 {minutes} 分钟"
    else:
        phrase = f"{minutes} 分钟"
    return {
        "known": True,
        "now": now.isoformat(timespec="seconds"),
        "last_encounter": last.isoformat(timespec="seconds"),
        "elapsed_seconds": int(seconds),
        "elapsed_phrase": phrase,
    }


# ---------------------------------------------------------
# The bundle
# ---------------------------------------------------------

def build_bundle(selected: list[dict], elapsed: dict, mode: str,
                 recall_id: str = "", query: str = "") -> dict:
    """Assemble what the agent gets. Typed, sourced, and small.

    Every item carries its bucket id and the reason it was chosen, so the
    agent can go back to it (wander_mark, trace) or disagree with it.
    每条都带 bucket id 和入选理由，agent 可以回去找它、标记它，也可以不认它。
    """
    if mode not in VALID_MODES:
        raise ValueError(f"unknown recall mode: {mode}")
    items = []
    for row in selected:
        meta = row["bucket"].get("metadata", {})
        items.append({
            "id": row["id"],
            "name": meta.get("name") or "",
            "created": str(meta.get("created") or "")[:19],
            "content": row["bucket"].get("content", ""),
            "score": row["score"],
            "why": row["parts"],
        })
    return {
        "recall_id": recall_id,
        "mode": mode,
        "query": query,
        "time": elapsed,
        "items": items,
        # Contract with every caller: reading the past changes nothing.
        # 对所有调用方的契约：读过去不改变任何东西。
        "read_only": {"wrote_anything": False, "touch_recorded": False},
    }


def format_bundle(bundle: dict) -> str:
    """Render for injection. Sections are labelled by what they are."""
    lines = []
    t = bundle.get("time") or {}
    if t.get("known"):
        lines.append(f"[现在] {t['now']}")
        lines.append(f"[距上次交互] {t['elapsed_phrase']}（上次：{t['last_encounter']}）")
    elif t.get("now"):
        lines.append(f"[现在] {t['now']}")
        lines.append("[距上次交互] 没有记录——这可能是第一次")

    items = bundle.get("items") or []
    if not items:
        lines.append("")
        lines.append("[没有浮上来的东西] 权重池是平的。这不是错误。")
        return "\n".join(lines)

    lines.append("")
    lines.append("[浮上来的过去]")
    for item in items:
        head = f"· [{item['created'][:10]}]"
        if item["name"]:
            head += f" {item['name']}"
        lines.append(head)
        lines.append(f"  {item['content'].strip()}")
        why = item["why"]
        top = sorted(why.items(), key=lambda kv: -kv[1])[:2]
        why_text = "、".join(f"{k} {v:.2f}" for k, v in top if v > 0)
        lines.append(f"  ↳ id:{item['id']}" + (f"（{why_text}）" if why_text else ""))
    lines.append("")
    lines.append("以上是证据，不是结论。怎么理解由你。")
    return "\n".join(lines)
