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
import os
import re
from datetime import datetime


TRACE_PREFIX = "trace-"
_TS_FORMAT = "%Y-%m-%d %H:%M"

# A feeling seen in only one window is an event, not a texture.
# 只在一个窗口出现过的感受是事件,不是质地。
RECURRENCE_MIN = 2


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


def _split(raw) -> list[str]:
    """`unresolved` is stored as a comma-joined string by leave_texture."""
    if isinstance(raw, list):
        items = raw
    else:
        items = str(raw or "").split(",")
    return [i.strip() for i in items if str(i).strip()]


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
    for key in ("primary", "secondary"):
        v = canon(trace.get(key))
        if v and v not in out:          # 同一窗里 primary 和 secondary 归一后撞了,只算一次
            out.append(v)
    return out


def _accumulate(series: list[tuple[str, list[str]]], last_window: str) -> list[dict]:
    """Turn a per-window membership series into lifetime counters.

    `series` is [(window_id, [items present in that window]), ...] in order.
    """
    state: dict[str, dict] = {}
    for window, items in series:
        present = set(items)
        for item in items:
            s = state.setdefault(item, {
                "item": item, "windows_carried": 0, "longest_streak": 0,
                "current_streak": 0, "first_seen": window, "last_seen": window,
            })
            s["windows_carried"] += 1
            s["current_streak"] += 1
            s["longest_streak"] = max(s["longest_streak"], s["current_streak"])
            s["last_seen"] = window
        for item, s in state.items():
            if item not in present:
                s["current_streak"] = 0
    out = []
    for s in state.values():
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


def profile(traces_dir) -> dict:
    """What has accumulated. Recomputed on every call."""
    traces = read_traces(traces_dir)
    if not traces:
        return {"windows": 0, "recurring_feelings": [], "carried_unresolved": [],
                "elapsed_days": None}

    last_window = traces[-1]["window"]
    feelings = _accumulate(
        [(t["window"], _feelings(t)) for t in traces], last_window)
    unresolved = _accumulate(
        [(t["window"], _split(t.get("unresolved"))) for t in traces], last_window)

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

    return "\n".join(lines)
