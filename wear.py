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


def _feelings(trace: dict) -> list[str]:
    out = []
    for key in ("primary", "secondary"):
        v = str(trace.get(key) or "").strip()
        if v:
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


def describe(traces_dir) -> str:
    """One short paragraph for the wake block. Empty when nothing has accrued.

    Says only what the counters say. It does not tell him how to feel about
    having carried something for eleven windows — that reading is his.
    只说计数说了什么。不告诉他该怎么看待「扛了十一个窗口」这件事——那是他的解读。
    """
    p = profile(traces_dir)
    if p["windows"] < RECURRENCE_MIN:
        return ""

    lines = []
    days = p["elapsed_days"]
    if days is not None and days >= 1:
        lines.append(f"这条线走了 {p['windows']} 个窗口，跨了 {int(days)} 天。")
    else:
        lines.append(f"这条线走了 {p['windows']} 个窗口。")

    recurring = [f for f in p["recurring_feelings"]][:3]
    if recurring:
        parts = [f"{f['item']}（{f['windows_carried']} 次）" for f in recurring]
        lines.append("反复回来的：" + "、".join(parts) + "。")

    open_items = [u for u in p["carried_unresolved"] if u["still_open"]]
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
