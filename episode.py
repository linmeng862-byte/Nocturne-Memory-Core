from __future__ import annotations

# ============================================================
# Module: Episodes (episode.py)
# 模块：经历段
#
# A stretch of lived time with utterances in it. Between SOURCE and
# INTERPRETATION in the authority hierarchy, which constrains what an episode
# is allowed to know.
# 一段有话语在里面的、活过的时间。在权威层级里夹在 SOURCE 和 INTERPRETATION 中间,
# 这限制了一段经历**被允许知道什么**。
#
# ------------------------------------------------------------
# Episodes are cut on structure, never on meaning.
# 经历段切在结构上,绝不切在意思上。
#
# It is tempting to let an analyzer decide "these turns are one episode,
# they're about the same thing". That would make the episode an
# interpretation — and then an interpretation sits *below* INTERPRETATION in
# the hierarchy, and every layer above it inherits an unmarked judgement.
# 让分析器来判「这几轮是同一段,因为在聊同一件事」很诱人。
# 那会让 episode 变成一条解释 —— 于是解释坐在了 INTERPRETATION **下面**,
# 而它上面每一层都继承了一个没被标记出来的判断。
#
# So the only cuts allowed are ones checkable against the source itself:
# a silence longer than the gap threshold, or a change of endpoint.
# 所以只允许两种切法,都能拿原始记录核对:
# 超过阈值的沉默,或者换了一个端。
#
# ------------------------------------------------------------
# An episode never claims it ended.
# 一段经历绝不声称自己结束了。
#
# Structure can tell you an episode was cut off by silence, or that it is
# still open. It cannot tell you anything was settled. "This is finished" is a
# judgement and belongs upstairs.
# 结构能告诉你一段经历被沉默截断了,或者它还开着。
# 它没法告诉你什么事情了结了。「这件事完了」是判断,该在楼上。
#
# ------------------------------------------------------------
# Episodes are computed, not stored.
# 经历段是算出来的,不是存下来的。
#
# They are a deterministic function of the source log, so persisting them
# would create a second authority that can drift from the first. Recompute
# instead; the source is append-only, so recomputation is cheap and safe.
# 它们是原始账的确定性函数,存下来等于造出第二个权威,会跟第一个飘开。
# 改成重算;原始只增不改,所以重算既便宜又安全。
# ============================================================

import hashlib
import logging
from datetime import datetime, timezone

import source_log

logger = logging.getLogger("ombre_brain.episode")

# How long a silence has to be before it counts as the end of a stretch.
# 沉默要多久才算一段的结束。
DEFAULT_GAP_SECONDS = 30 * 60

# Why a stretch ended. All structural; none of them mean "resolved".
# 一段为什么结束。全是结构性的;没有一个的意思是「了结了」。
CUT_GAP = "silence"            # a silence longer than the threshold
CUT_ENDPOINT = "endpoint_change"  # the conversation moved to another body
CUT_OPEN = "open"              # still running, or we simply have no more source
VALID_CUTS = {CUT_GAP, CUT_ENDPOINT, CUT_OPEN}

# An endpoint nobody declared. Not a body — an absence of information about
# which body.
# 没人声明的 endpoint。它不是一个身体,是「关于是哪个身体」这件事的缺失。
ENDPOINT_UNKNOWN = "unknown"


def _known_endpoint(value: str) -> bool:
    return bool(value) and value != ENDPOINT_UNKNOWN


def _parse_ts(value) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


def episode_id(first_utterance_id: str, started_at: str) -> str:
    """Identity is the episode's beginning, not its contents.

    Hashing the whole member list would change the id every time the episode
    grows — so an interpretation written mid-conversation would end up citing
    an episode that no longer exists by the time the conversation ends.
    身份是这一段的**开头**,不是它的内容。
    把全部成员一起 hash 的话,这段每长一句 id 就变一次 ——
    于是对话进行到一半写下的解释,等对话结束时会引用到一个已经不存在的 episode。
    """
    packed = f"{first_utterance_id}|{started_at}"
    return "ep_" + hashlib.sha256(packed.encode("utf-8")).hexdigest()[:20]


def segment(utterances: list[dict], gap_seconds: float = DEFAULT_GAP_SECONDS,
            now: datetime | None = None) -> list[dict]:
    """Cut a stream of utterances into episodes. Pure, deterministic, offline.

    Utterances with no usable timestamp are kept in whatever episode they are
    adjacent to rather than dropped or given a guessed time. Losing a real
    utterance to tidy up the timeline is not a trade this layer gets to make.
    没有可用时间戳的话语,归到它挨着的那一段里,而不是丢掉、也不是猜一个时间。
    为了让时间线整齐而丢掉一句真的说过的话,不是这一层有权做的取舍。
    """
    episodes: list[dict] = []
    current: dict | None = None
    prev_dt: datetime | None = None

    for u in utterances or []:
        if not isinstance(u, dict):
            continue
        uid = str(u.get("utterance_id") or "").strip()
        if not uid:
            continue
        dt = _parse_ts(u.get("ts"))
        endpoint = str(u.get("endpoint") or "").strip()

        # Silence is checked first on purpose. When a stretch both went quiet
        # for six days and then resumed on another endpoint, reporting only
        # "endpoint_change" hides the six days — and the length of a silence is
        # the more consequential fact of the two.
        # 刻意先查沉默。如果一段先安静了六天、然后在另一个端接上,
        # 只报「换端」会把那六天藏起来 —— 而两者之中,沉默有多长是更要紧的那个事实。
        silent = (dt is not None and prev_dt is not None
                  and (dt - prev_dt).total_seconds() > gap_seconds)
        cut_reason = None
        if current is None:
            cut_reason = "first"
        elif silent:
            cut_reason = CUT_GAP
        elif endpoint != current["endpoint"] and \
                _known_endpoint(endpoint) and _known_endpoint(current["endpoint"]):
            # Only cut when two *known* bodies differ. An undeclared endpoint
            # is missing information, not evidence of a different body, and
            # cutting on it would manufacture boundaries out of a gap in the
            # records rather than out of anything that happened.
            # 只有当两个**已知**的身体不同才切。没声明的 endpoint 是信息缺失,
            # 不是「换了个身体」的证据;拿它来切,是在用记录里的一个空洞
            # 而不是真的发生过的事,造出边界来。
            cut_reason = CUT_ENDPOINT

        if cut_reason is not None:
            if current is not None:
                current["ended_by"] = cut_reason
                episodes.append(current)
            current = {
                "episode_id": "",  # filled once the start time is known
                "authority": "episode",
                "endpoint": endpoint,
                "utterance_ids": [],
                "started_at": "",
                "ended_at": "",
                "ended_by": CUT_OPEN,
            }
            # Do not measure the first silence of a new stretch against the
            # last utterance of the previous one — they are different stretches.
            # 新一段的第一次沉默,不该拿上一段的最后一句来量 —— 它们不是同一段。
            prev_dt = None

        current["utterance_ids"].append(uid)
        if dt is not None:
            stamp = dt.isoformat(timespec="seconds")
            if not current["started_at"]:
                current["started_at"] = stamp
            current["ended_at"] = stamp
            prev_dt = dt

    if current is not None:
        episodes.append(current)

    # Fill derived fields once each episode's shape is final.
    # 每段形状定下来之后再填派生字段。
    prev_end: datetime | None = None
    for ep in episodes:
        ep["episode_id"] = episode_id(ep["utterance_ids"][0], ep["started_at"])
        ep["utterance_count"] = len(ep["utterance_ids"])

        start_dt, end_dt = _parse_ts(ep["started_at"]), _parse_ts(ep["ended_at"])
        ep["duration_seconds"] = (
            int((end_dt - start_dt).total_seconds())
            if start_dt and end_dt else None
        )
        # The silence before this stretch. This is the number a texture layer
        # will want: coming back after an hour and coming back after a month
        # are not the same return.
        # 这一段之前的沉默。将来做质地的那层要的就是这个数:
        # 隔一小时回来和隔一个月回来,不是同一种「回来」。
        ep["gap_before_seconds"] = (
            int((start_dt - prev_end).total_seconds())
            if start_dt and prev_end else None
        )
        if end_dt:
            prev_end = end_dt

    # The final episode is only "open" in the sense that no later utterance has
    # closed it. If the threshold has already elapsed, say so — but say it as
    # a silence, never as a conclusion.
    # 最后一段的「开着」只是说没有更晚的话语把它关上。
    # 如果阈值已经过去了,就说出来 —— 但要说成一段沉默,绝不说成一个结论。
    if episodes:
        last = episodes[-1]
        end_dt = _parse_ts(last["ended_at"])
        ref = now or datetime.now()
        if end_dt and (ref - end_dt).total_seconds() > gap_seconds:
            last["ended_by"] = CUT_GAP
        last["silence_since_seconds"] = (
            int((ref - end_dt).total_seconds()) if end_dt else None
        )

    return episodes


def rebuild(buckets_dir: str, gap_seconds: float = DEFAULT_GAP_SECONDS,
            now: datetime | None = None) -> list[dict]:
    """Recompute every episode from source. Reads only; writes nothing."""
    return segment(source_log.read_all(buckets_dir), gap_seconds, now=now)


def episode_of(episodes: list[dict], utterance_id: str) -> dict | None:
    """Which stretch a given utterance belongs to."""
    for ep in episodes or []:
        if utterance_id in ep.get("utterance_ids", []):
            return ep
    return None


def describe_gap(seconds) -> str:
    """State a duration as a duration. Nothing about how it felt.

    The system knows how long the silence was. It does not know what that was
    like, and must never say.
    把时长说成时长。不带任何关于「那是什么滋味」的话。
    系统知道沉默有多久。它不知道那是什么感觉,也绝不许说。
    """
    if seconds is None:
        return "未知"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} 秒"
    if seconds < 3600:
        return f"{seconds // 60} 分钟"
    if seconds < 86400:
        hours, mins = divmod(seconds // 60, 60)
        return f"{hours} 小时 {mins} 分钟" if mins else f"{hours} 小时"
    days, rem = divmod(seconds, 86400)
    hours = rem // 3600
    return f"{days} 天 {hours} 小时" if hours else f"{days} 天"


def last_stretch(buckets_dir: str, gap_seconds: float = DEFAULT_GAP_SECONDS,
                 now: datetime | None = None) -> dict | None:
    """The most recent stretch of talk, from source.

    Distinct from recall_journal.last_encounter(), which records the last time
    a recall bundle was handed over — that measures the system's own
    bookkeeping, not the conversation. Both are facts; they are not the same
    fact, and reporting one as the other would be a quiet lie about when you
    two last actually spoke.
    跟 recall_journal.last_encounter() 不是一回事:那个记的是上次把召回包递出去的时间 ——
    量的是系统自己的簿记,不是对话。两个都是事实,但不是同一个事实;
    把其中一个当成另一个来报,等于在「你们上次真的说过话是什么时候」上悄悄撒了个谎。
    """
    episodes = rebuild(buckets_dir, gap_seconds, now=now)
    return episodes[-1] if episodes else None
