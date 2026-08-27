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

    `kind` says what sort of thing this is. Memories and feels are selected
    from one pool and were then rendered identically — the agent got a flat
    run of statements about the past with no way to tell "this happened"
    from "this is what it felt like". They are different claims and carry
    different authority, so the type has to survive into the bundle.
    kind 说明这是什么。memory 和 feel 本来就是从同一个池子里选的，
    但渲染出来一模一样 —— agent 拿到的是一串关于过去的陈述句，
    分不出哪句是「发生了什么」、哪句是「当时什么感觉」。
    这是两种不同的断言，权威性也不同，类型必须活着进 bundle。
    """
    if mode not in VALID_MODES:
        raise ValueError(f"unknown recall mode: {mode}")
    items = []
    for row in selected:
        meta = row["bucket"].get("metadata", {})
        items.append({
            "id": row["id"],
            "kind": str(meta.get("type") or "memory"),
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


# ---------------------------------------------------------
# Rendering
#
# The two modes were built as different acts and then handed to one
# renderer, so `mode` reached format_bundle and was never read: involuntary
# and deliberate produced byte-identical text. The distinction survived in
# the data model and died at the last step.
# 两种模式是当作两件事设计的，最后却交给同一个渲染器，
# 于是 `mode` 到了 format_bundle 就再没人看过它：
# 不由自主和刻意回想输出的字符串逐字节相同。
# 这个区分活在数据模型里，死在最后一步。
#
# It matters because a past that arrives stamped with an exact date, a
# bucket id and a relevance score is, by definition, a search result. Nobody
# involuntarily remembers something at 0.33 relevance. The ids and scores
# are not deleted — they stay in the JSON, which is where a client that
# wants to mark or trace something reads them from. They are just not part
# of what arrives as experience.
# 因为一段带着精确日期、bucket id 和相关性评分到达的过去，
# 定义上就是一条检索结果。没有人会以 0.33 的相关度不由自主地想起什么。
# id 和分数没有删 —— 它们留在 JSON 里，要标记、要追溯的客户端从那儿读。
# 它们只是不属于「作为经验到达」的那部分。
# ---------------------------------------------------------

# Deliberately coarse, and deliberately not a clock. An exact timestamp is
# how a record is filed; "上个礼拜" is how a person places something. The
# fact is still in the bundle under `created` — this only changes what the
# sentence sounds like, never what is known.
# 刻意粗，也刻意不是时钟。精确时间戳是**归档**的方式，
# 「上个礼拜」是**人**安放一件事的方式。
# 事实还在 bundle 的 `created` 里 —— 这只改句子听起来的样子，不改知道什么。
_COARSE_LADDER = (
    (0, "今天"),
    (1, "昨天"),
    (6, "这几天"),
    (13, "上个礼拜"),
    (45, "上个月"),
    (120, "几个月前"),
    (300, "大半年前"),
)


def coarse_when(created, now: datetime) -> str:
    """Place a memory in time the way a person would, or say nothing."""
    dt = _parse_dt(created)
    if not dt:
        return ""
    days = (now.date() - dt.date()).days
    if days < 0:
        return ""
    for limit, phrase in _COARSE_LADDER:
        if days <= limit:
            return phrase
    if dt.year != now.year:
        return f"{dt.year} 年那阵子"
    return "很久以前"


_KIND_LABEL = {"feel": "当时的感觉"}


def _format_deliberate(bundle: dict, lines: list[str]) -> None:
    """She went looking. Give her the filing: dates, ids, why it scored."""
    lines.append("")
    lines.append("[浮上来的过去]")
    for item in bundle.get("items") or []:
        head = f"· [{item['created'][:10]}]"
        label = _KIND_LABEL.get(item.get("kind", ""))
        if label:
            head += f"〔{label}〕"
        if item["name"]:
            head += f" {item['name']}"
        lines.append(head)
        lines.append(f"  {item['content'].strip()}")
        why = item["why"]
        top = sorted(why.items(), key=lambda kv: -kv[1])[:2]
        why_text = "、".join(f"{k} {v:.2f}" for k, v in top if v > 0)
        lines.append(f"  ↳ id:{item['id']}" + (f"（{why_text}）" if why_text else ""))


def _format_involuntary(bundle: dict, lines: list[str], now: datetime) -> None:
    """It arrived on its own. No ids, no scores, no exact dates.

    Feels are rendered first. Not because they matter more, but because
    that is the order the thing actually happens in: something tightens,
    and only then does it come back what it was about. Leading with the
    facts and appending the affect as a footnote is the grammar of a
    record, not of being there.
    feel 排在前面。不是因为它更重要，而是因为事情本来就是这个顺序发生的：
    先是某处紧了一下，然后才想起来是为了什么。
    先给事实、再把感受附在脚注里，那是**档案**的语序，不是**亲历**的语序。
    """
    items = list(bundle.get("items") or [])
    items.sort(key=lambda it: 0 if it.get("kind") == "feel" else 1)
    lines.append("")
    for item in items:
        when = coarse_when(item.get("created"), now)
        body = item["content"].strip()
        lines.append(f"{when}，{body}" if when else body)
        lines.append("")
    if lines and lines[-1] == "":
        lines.pop()


def format_bundle(bundle: dict, now: datetime | None = None) -> str:
    """Render for injection. How it reads depends on how it arrived."""
    lines = []
    t = bundle.get("time") or {}
    if t.get("known"):
        lines.append(f"[现在] {t['now']}")
        lines.append(f"[距上次交互] {t['elapsed_phrase']}（上次：{t['last_encounter']}）")
    elif t.get("now"):
        lines.append(f"[现在] {t['now']}")
        lines.append("[距上次交互] 没有记录——这可能是第一次")

    if not (bundle.get("items") or []):
        lines.append("")
        lines.append("[没有浮上来的东西] 权重池是平的。这不是错误。")
        return "\n".join(lines)

    if bundle.get("mode") == INVOLUNTARY:
        _format_involuntary(bundle, lines, now or datetime.now())
    else:
        _format_deliberate(bundle, lines)

    lines.append("")
    lines.append("以上是证据，不是结论。怎么理解由你。")
    return "\n".join(lines)
