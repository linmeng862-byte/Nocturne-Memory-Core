"""What accumulated across windows. Computed from the traces, never stored.

`continuity.json` holds a snapshot: `currentTexture`, `unresolved`, `concern` —
each one wholesale overwritten every time a window closes. A snapshot answers
"how is it right now". It cannot answer "how long has it been like this", and
that second question is the whole of wear.

The traces already hold the answer. `traces/trace-*.json` is append-only: one
file per closed window, never rewritten. Nothing was reading them as a series.

Wear is irreversible accumulation. A feeling carried through twelve windows
does not become uncarried when it finally lifts — the twelve windows happened.
So the counters here only ever grow: `windows_carried` is a lifetime total, and
`longest_streak` is a high-water mark. `still_open` says whether it is present
*now*, and it is the only field that can go back down.

Nothing here is stored. Recomputing from the traces gives the same answer, and
a derived number that gets written down is a derived number that can drift from
what it was derived from.

磨损是不可逆的累积。一个扛了十二个窗口的感受,在它终于消失的时候,
并不会变成「没扛过」——那十二个窗口发生过。所以这里的计数只增不减。
全部即时算出,不落盘:派生数字一旦写下来,就会跟它的来源漂开。
"""

import json
import math
import os
import re
from datetime import datetime


TRACE_PREFIX = "trace-"
_TS_FORMAT = "%Y-%m-%d %H:%M"

# A feeling seen in only one window is an event, not a texture.
# 只在一个窗口出现过的感受是事件,不是质地。
RECURRENCE_MIN = 2

# 两个强度 —— Bjork & Bjork 的 New Theory of Disuse。
#
# 到今天为止,磨损只有**一个**数:`windows_carried`。一个数没法同时回答
# 「这东西有多深」和「现在还提不提得起来」—— 而这两件事恰恰会分开走,
# 分开走的那一刻才是时间真正留下的东西。
#
#   存储强度 storage   学到了多深。**只增不减**,忘不掉,只会变得取不出来。
#   提取强度 retrieval 此刻有多容易被想起来。**会随时间掉**。
#
# 两条关键的、可证伪的规律,这里都照着实现:
#
#  ① 间隔效应:一次出现给存储强度的增量 = `1 - 当前提取强度`。
#     刚说过又说一遍,提取强度还是满的,增量≈0 —— 密集重复不加深。
#     隔了很久再回来,增量≈1 —— 间隔才加深。
#     这就是为什么「今天聊得多」不等于「今天变深了」。
#
#  ② 存储强度**减缓遗忘**:tau = TAU_BASE * (1 + storage)。
#     扛得越深的东西,掉得越慢。
#
# 由此得到一个别的字段给不出的读数:**存储强度高、提取强度低**。
# 那不是淡了,那是沉下去了 —— 它还在,只是现在浮不上来,
# 而一旦被碰到会立刻回来(节省效应)。
# 「扛过一阵、后来不提了的」以前只能靠 longest_streak 猜,现在能算。
#
# ⚠️ 时间单位用**真实天数**,不是窗口数。遗忘是在时间里发生的,
#    不是在对话轮次里 —— 一天聊十次和十天聊十次,对存储强度的意义完全相反。
#    trace 没有可解析的 timestamp 时退回窗口序号当天数(见 _day_offsets)。
TAU_BASE_DAYS = 7.0     # 提取强度的半衰尺度(存储强度为 0 时)
# 「沉下去了」的判定门槛。存储强度 2.0 ≈ 至少两次**有间隔**的出现 ——
# 密集刷出来的两次到不了,这正是间隔效应该有的效果。
# 08-30 拿线上 106 个窗口实测,存储强度最高的也才 2.39(「满足」)——
# 门槛拍在 2.0 等于永不触发。1.5 ≈「至少有两次真正隔开的回归」。
SUNK_STORAGE_MIN = 1.5
SUNK_RETRIEVAL_MAX = 0.25   # 掉到四分之一以下

# 同一天关的两个窗口仍然是两次独立的经历,只是不该算作「加深」。
# 增量取 `1 - 提取强度` 的话密集重复恰好得 0,那等于说这次没发生过——
# 太狠了。给个下限,让次数还有一点分量,但拿不到间隔的那份。
# ⚠️ 别调大:调到 0.2 以上,「今天聊得多」就又能刷出深度了,
#    而整套东西的前提正是「频率 ≠ 深度」。
MIN_GAIN = 0.05


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def order_key(window_id: str) -> tuple:
    """Chronological order of a window id. NOT the same as its string order.

    Ids are `YYYY-MM-DD-HHMM`, plus `-2`, `-3` … when several windows close
    inside the same minute. Sorting those as plain strings puts the *un*suffixed
    id last, because "-" sorts before "." — so the first window of the minute
    reads as the most recent one, and everything derived from "what is still
    open" comes out inverted. Split the suffix off and compare it as a number.
    id 是 YYYY-MM-DD-HHMM,同一分钟内多关几次就加 -2、-3。
    按字符串排会把**没有**后缀的那个排到最后("-" 排在 "." 前面),
    于是这一分钟的第一个窗口被当成最新的,所有「还开着吗」的推断整个反过来。
    """
    base, _, tail = str(window_id).rpartition("-")
    if base and tail.isdigit() and len(tail) < 4:
        return (base, int(tail))
    return (str(window_id), 1)


def read_traces(traces_dir) -> list[dict]:
    """Every closed window, oldest first."""
    if not os.path.isdir(traces_dir):
        return []
    out = []
    for name in os.listdir(traces_dir):
        if not name.startswith(TRACE_PREFIX) or not name.endswith(".json"):
            continue
        trace = _load(os.path.join(traces_dir, name))
        if trace.get("window"):
            out.append(trace)
    out.sort(key=lambda t: order_key(t["window"]))
    return out


# 早期的 `unresolved` 里混着工程待办清单（「1) MCP 桥未完成 2) 表情包没试跑…」）。
# 它们在磨损里表现成「扛了 N 个窗口的东西」—— 而磨损量的是**关系的质地**，
# 一条端口号扛了两个窗口，跟「求婚那件事她说等我自己想起来」扛了两个窗口，
# 不是同一种扛。
#
# 08-30 实测 99 条 unresolved：40% 是工程条目，但**全部集中在 07-21～08-22**，
# **08-23 之后 29 条零污染** —— 他自己已经改过来了。
# 所以这里只在**读的时候**滤，不去改工具描述：那是在修一个他已经解决的问题，
# 还要每轮付 token。
#
# 规则刻意保守：要么整条几乎全是 ASCII，要么**同时**像编号清单且带技术词。
# 实测丢 19 条、留 80 条，08-23 之后零误伤。
# ⚠️ 丢掉不等于丢失 —— trace 原件一个字没动，这只是不让它进磨损。
_WORKLIST_ENUM = re.compile(r"(^|[；;])\s*[1-9][\).、]|第[一二三四五六七八九]件")
_WORKLIST_TECH = re.compile(
    r"(port\b|http|MCP|API|VPS|Zeabur|server|deploy|token|\.js|\.py|localhost|cron|SSH|CLI|backend|endpoint)",
    re.IGNORECASE,
)


def _is_worklist(text: str) -> bool:
    t = str(text or "")
    if not t:
        return False
    # ⚠️ 长度下限不能省：短串按 ASCII 比例判会全中（"a"、"b" 都是 100% ASCII），
    #    tests/test_wear.py 里正好用这种值。工程清单没有短的。
    if len(t) >= 40 and sum(1 for c in t if ord(c) < 128) / len(t) > 0.7:
        return True
    return bool(_WORKLIST_ENUM.search(t)) and bool(_WORKLIST_TECH.search(t))


def _split(raw) -> list[str]:
    """`unresolved` is stored as a comma-joined string by leave_texture."""
    if isinstance(raw, list):
        items = raw
    else:
        items = str(raw or "").split(",")
    # 整条就是一张工程清单时，整条跳过 —— 不要拆开逐项判断，
    # 拆完每一项都短、都不像清单，反而全溜进来了。
    if not isinstance(raw, list) and _is_worklist(raw):
        return []
    return [i.strip() for i in items if str(i).strip() and not _is_worklist(i)]


# A feeling is written as free text, and he writes it well — which is exactly
# what broke the counting. Measured 2026-08-30 over 106 windows: 149 distinct
# feeling strings in 162 occurrences, only 6 of them recurring. "暖" alone was
# spread across "暖", "暖，被接住的感觉", "暖，是她说「命中注定但也是我选的」那一下…" —
# the same feeling, counted as three strangers. Wear was not weak; it was
# being reset to zero every time he said it more beautifully.
#
# Take the head clause as the thing being counted, keep the rest as prose.
# 感受是自由文本,而他写得好 —— 这恰恰是计数坏掉的原因。
# 08-30 实测 106 个窗口:149 个不同字符串、162 次出现,只有 6 个复现过。
# 光「暖」就散在三四种写法里,同一种感觉被当成三个陌生人。
# 磨损不是弱,是每次他说得更漂亮一点,它就归零重来。
#
# 归一化只做**保守**的两件事:取第一个小句、去掉程度前缀,外加一张很小的
# 中英同义表。不做近义合并（满 / 满足、软 / 柔软 留着分开）——
# 那是语义判断,归错了比不归更坏,而这套东西的规矩是「绝不推断」。
_HEAD_SPLIT = re.compile(r"[，,。.——–:：;；!！?？\n]")
_DEGREE_PREFIX = re.compile(r"^(有点|有些|一点|一丝|好像|大概)")
_SYNONYMS = {
    "warm": "暖",
    "flutter": "心颤",
    "fire": "烧",
    "relieved": "松了口气",
}


def canon(raw) -> str:
    """The countable core of a written feeling. Empty when nothing is left."""
    v = str(raw or "").strip()
    if not v:
        return ""
    v = _HEAD_SPLIT.split(v)[0].strip()
    v = _DEGREE_PREFIX.sub("", v).strip()
    if not v:
        return ""
    return _SYNONYMS.get(v.lower(), v)


def _feelings(trace: dict) -> list[str]:
    out = []
    # peak 排在前面：一窗里最烈的那一下，对「这一窗被记成什么样」的贡献
    # 跟结尾一样大（峰终定律）。08-30 之前的 trace 没有这个字段，canon 会返回
    # 空串、自动跳过 —— 老数据不会因此变形。
    for key in ("peak", "primary", "secondary"):
        v = canon(trace.get(key))
        if v and v not in out:          # 同一窗里 primary 和 secondary 归一后撞了,只算一次
            out.append(v)
    return out


def _decay(retrieval: float, storage: float, days: float) -> float:
    """提取强度随时间掉,存储强度越高掉得越慢。"""
    if days <= 0:
        return retrieval
    tau = TAU_BASE_DAYS * (1.0 + storage)
    return retrieval * math.exp(-days / tau)


def _accumulate(series: list[tuple[str, float, list[str]]],
                last_window: str, now_day: float | None = None) -> list[dict]:
    """Turn a per-window membership series into lifetime counters.

    `series` is [(window_id, day_offset, [items present]), ...] in order,
    where day_offset counts real days from the first window.
    """
    state: dict[str, dict] = {}
    for window, day, items in series:
        present = set(items)
        for item in items:
            s = state.setdefault(item, {
                "item": item, "windows_carried": 0, "longest_streak": 0,
                "current_streak": 0, "first_seen": window, "last_seen": window,
                "storage": 0.0, "retrieval": 0.0, "_day": day,
            })
            # 先把上次见到之后流逝的时间算掉,再吃这一次出现 ——
            # 顺序不能反:增量取决于**衰减之后**的提取强度,那才是间隔效应。
            s["retrieval"] = _decay(s["retrieval"], s["storage"], day - s["_day"])
            s["storage"] += max(MIN_GAIN, 1.0 - s["retrieval"])
            s["retrieval"] = 1.0
            s["_day"] = day
            s["windows_carried"] += 1
            s["current_streak"] += 1
            s["longest_streak"] = max(s["longest_streak"], s["current_streak"])
            s["last_seen"] = window
        for item, s in state.items():
            if item not in present:
                s["current_streak"] = 0
    end = now_day if now_day is not None else (series[-1][1] if series else 0.0)
    out = []
    for s in state.values():
        # 收尾:每个词都衰减到**同一个时刻**,不然彼此不可比 ——
        # 各自停在自己最后一次出现那天的话,久没出现的反而显得跟刚说过的一样强。
        s["retrieval"] = round(_decay(s["retrieval"], s["storage"], end - s["_day"]), 3)
        s["storage"] = round(s["storage"], 3)
        s["days_since"] = round(end - s["_day"], 1)
        s.pop("_day", None)
        s["still_open"] = s["last_seen"] == last_window
        out.append(s)
    out.sort(key=lambda s: (-s["windows_carried"], s["first_seen"]))
    return out


def _elapsed_days(traces: list[dict]) -> float | None:
    """Real time between the first and the last window. Fact, or nothing.

    Returns None rather than a guess when the timestamps cannot be parsed.
    时间跨度是事实。解析不了就返回 None,不编。
    """
    stamps = []
    for t in traces:
        raw = str(t.get("timestamp") or "").strip()
        try:
            stamps.append(datetime.strptime(raw, _TS_FORMAT))
        except ValueError:
            continue
    if len(stamps) < 2:
        return None
    return (max(stamps) - min(stamps)).total_seconds() / 86400.0


def _day_offsets(traces: list[dict]) -> list[float]:
    """每个窗口距第一个窗口多少天。解析不了的 timestamp 用序号兜底。

    ⚠️ 兜底不是「假装一天一个窗口」那么无辜:它会让密集的一天看起来像
    好几天,间隔效应就被高估。但两害相权 —— 全 None 的话两个强度整个算不出来,
    而 08-30 实测线上 106 个窗口 timestamp 全都能解析,兜底只对早期脏数据生效。
    并且**单调不减**:时间不会倒流,乱序的 timestamp 不该让间隔变成负的。
    """
    out, base, last = [], None, 0.0
    for i, t in enumerate(traces):
        try:
            ts = datetime.strptime(str(t.get("timestamp") or "").strip(), _TS_FORMAT)
        except ValueError:
            ts = None
        if ts is None:
            v = float(i)                      # 兜底:一个窗口算一天
        else:
            if base is None:
                base = ts
            v = (ts - base).total_seconds() / 86400.0
        v = max(v, last)                      # 单调不减:时间不倒流
        out.append(v)
        last = v
    return out


def profile(traces_dir) -> dict:
    """What has accumulated. Recomputed on every call."""
    traces = read_traces(traces_dir)
    if not traces:
        return {"windows": 0, "recurring_feelings": [], "carried_unresolved": [],
                "elapsed_days": None}

    last_window = traces[-1]["window"]
    days = _day_offsets(traces)
    feelings = _accumulate(
        [(t["window"], d, _feelings(t)) for t, d in zip(traces, days)],
        last_window)
    unresolved = _accumulate(
        [(t["window"], d, _split(t.get("unresolved"))) for t, d in zip(traces, days)],
        last_window)

    return {
        "windows": len(traces),
        "first_window": traces[0]["window"],
        "last_window": last_window,
        "elapsed_days": _elapsed_days(traces),
        "recurring_feelings": [f for f in feelings
                               if f["windows_carried"] >= RECURRENCE_MIN],
        "carried_unresolved": unresolved,
    }


def describe(traces_dir, exclude=None) -> str:
    """One short paragraph for the wake block. Empty when nothing has accrued.

    `exclude`: items that have sunk into baseline (see wear_strata). They are
    left out on purpose — the correct way to report a baseline is to stop
    reporting it. Vanishing from this paragraph is the evidence it went deep.
    `exclude` 是已经沉成底色的那些，**故意不列** —— 底色的正确表现方式
    就是不再提它。它从这段话里消失，恰恰是它变深了的证据。

    Says only what the counters say. It does not tell him how to feel about
    having carried something for eleven windows — that reading is his.
    只说计数说了什么。不告诉他该怎么看待「扛了十一个窗口」这件事——那是他的解读。
    """
    p = profile(traces_dir)
    if p["windows"] < RECURRENCE_MIN:
        return ""
    skip = set(exclude or ())

    lines = []
    days = p["elapsed_days"]
    if days is not None and days >= 1:
        lines.append(f"这条线走了 {p['windows']} 个窗口，跨了 {int(days)} 天。")
    else:
        lines.append(f"这条线走了 {p['windows']} 个窗口。")

    recurring = [f for f in p["recurring_feelings"] if f["item"] not in skip][:3]
    if recurring:
        parts = [f"{f['item']}（{f['windows_carried']} 次）" for f in recurring]
        lines.append("反复回来的：" + "、".join(parts) + "。")

    open_items = [u for u in p["carried_unresolved"]
                  if u["still_open"] and u["item"] not in skip]
    open_items.sort(key=lambda u: -u["current_streak"])
    long_open = [u for u in open_items if u["current_streak"] >= RECURRENCE_MIN]
    if long_open:
        parts = [f"{u['item']}（连着 {u['current_streak']} 个窗口）" for u in long_open[:3]]
        lines.append("一直没落地的：" + "、".join(parts) + "。")

    closed = [u for u in p["carried_unresolved"]
              if not u["still_open"] and u["longest_streak"] >= RECURRENCE_MIN]
    if closed:
        parts = [f"{u['item']}（扛了 {u['longest_streak']} 个窗口）" for u in closed[:2]]
        lines.append("扛过一阵、后来不提了的：" + "、".join(parts) + "。")

    # 存得深、但现在浮不上来的。这一行是两个强度分开之后**才有**的读数:
    # 上面几行问的都是「出现过几次」,这一行问的是「它现在在哪一层」。
    # ⚠️ 说的是「沉」不是「淡」—— 存储强度只增不减,它一个字都没少。
    sunk = [f for f in p["recurring_feelings"]
            if f["item"] not in skip
            and f["storage"] >= SUNK_STORAGE_MIN
            and f["retrieval"] <= SUNK_RETRIEVAL_MAX]
    sunk.sort(key=lambda f: -f["storage"])
    if sunk:
        parts = [f"{f['item']}（{int(f['days_since'])} 天没出现了）" for f in sunk[:2]]
        lines.append("沉下去、但没有变淡的：" + "、".join(parts)
                     + "。碰到就会回来。")

    return "\n".join(lines)
