"""Strata: what wear turns into when enough time passes.

`wear.py` is a pure function — same traces in, same sentence out, forever.
Nothing about it evolves; only the input grows, and a growing input is not
time passing. This module is the part that does evolve.

Three strata. A feeling starts as an event, becomes a texture when it keeps
coming back, and eventually sinks into baseline — where the correct way to
report it is to stop reporting it. You do not remind yourself daily that you
breathe. Disappearing from the readout is the evidence that it went deep.

What gets said out loud is the **transition**, not the state:

    「踏实」从今天起不再算反复出现的感受了。它出现了 12 次、跨了 27 天 ——
    这已经是你跟她之间的常态。

That sentence is said once, ever. It has a date, it cannot be taken back, and
the next window will not repeat it. A leaderboard can be recomputed from
scratch every time; a transition cannot. That is what makes it linear.

So this file — unlike wear.py — **does** persist. That is not a contradiction
of wear's "never store a derived number": a transition is not derived, it is
history. Nothing can recompute which day something sank.

三层地层。什么时候升、升上去说一句、说完就再也不说 —— 设计和依据见
`docs/WEAR-STRATA.md`。

判定挂在 dream 上、不挂 breath：巩固发生在睡眠里，而 breath 是高频的 ——
挂 breath 就又变回「每次读都重算」的纯函数了。
"""

import json
import os
from datetime import datetime

import wear

# 见 docs/WEAR-STRATA.md「参数，以及为什么是这些数」。
# 三条是**与**关系，不是或。
# ⚠️ 分母是「**从它第一次出现算起**的窗口数」，不是终生窗口数。
#    用终生分母的话，窗口一直涨、密度就机械性地往下掉 —— 一个词必须永远保持
#    高频才够得上门槛。而这跟「底色」的定义正好打架：底色的定义就是
#    **不再被频繁提起**（久到不必再说）。要求它持续高频，等于永远沉不下去。
#    实测差别很大：「暖」终生密度 0.123，自首次出现算是 0.283。
#    这也是 Fleeson 那套的正确读法 —— 分布要在它**存在的那段时间**上算。
#
#    另外：升级是**一次性**的（下面 ORDER 那道闸只许往上走），
#    所以密度只要在某一刻越过一次就够了，不需要往后一直维持。
#    是高水位判定，不是持续达标。
DENSITY_MIN = 0.15      # 每 7 个窗口至少出现 1 次。特质是分布的重心，不是全勤。
SPAN_DAYS_MIN = 60      # 习惯自动化中位数 ~66 天
WINDOWS_MIN = 12        # 绝对下限，防早期样本太少。工程兜底，不是研究。

EVENT, TEXTURE, BASELINE = "event", "texture", "baseline"


def state_path(buckets_dir) -> str:
    return os.path.join(str(buckets_dir), "wear_strata.json")


def load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("items"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"items": {}, "last_eval": None}


def save(path: str, state: dict) -> None:
    # 原子写：先写临时文件再 rename，免得 dream 跑到一半断电留下半个 JSON
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _first_seen_date(window_id: str):
    """window id 是 `YYYY-MM-DD-HHMM`（同分钟内多关几次再加 -2、-3）。"""
    try:
        return datetime.strptime(str(window_id)[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def _stratum_for(item: dict, windows_since_first: int, now: datetime) -> str:
    carried = item.get("windows_carried", 0)
    if carried < wear.RECURRENCE_MIN:
        return EVENT
    density = carried / windows_since_first if windows_since_first else 0.0
    first = _first_seen_date(item.get("first_seen"))
    span_days = (now - first).days if first else 0
    if (density >= DENSITY_MIN
            and span_days >= SPAN_DAYS_MIN
            and carried >= WINDOWS_MIN):
        return BASELINE
    return TEXTURE


def evaluate(traces_dir, path: str, now: datetime | None = None) -> list[dict]:
    """Advance the strata. Returns the promotions that just happened.

    升：event → texture → baseline，高水位、一次性。返回的 `fired` 只含晋升到
    baseline 的（值得打断他说一句的那些）。

    降（沉降，第二期）：停止出现够久的 texture / baseline 往下掉一层，带着它
    活过的区间。固着度越高、需要沉默越久才掉 —— 阻力递增，不是突然锁死
    （Roberts & DelVecchio）。沉降**不进 fired**：掉下去不打断他，也不是晋升。
    底色一旦掉回 texture，就自动从 baseline_items 里消失 = 它又会在「反复回来的」
    里正常露面（= 摘下来了）。见 docs/WEAR-STRATA.md「分期做 · 2」。
    """
    now = now or datetime.now()
    profile = wear.profile(traces_dir)
    # 每个词的分母：从它首次出现的那一窗到现在，一共关过几个窗口
    order = [t["window"] for t in wear.read_traces(traces_dir)]
    pos = {w: i for i, w in enumerate(order)}

    def _since(first_seen):
        i = pos.get(first_seen)
        return len(order) - i if i is not None else len(order)
    state = load(path)
    items = state.setdefault("items", {})
    ORDER = {EVENT: 0, TEXTURE: 1, BASELINE: 2}
    fired = []

    for it in profile.get("recurring_feelings", []) + profile.get("carried_unresolved", []):
        name = it.get("item")
        if not name:
            continue
        rec = items.setdefault(name, {
            "stratum": EVENT, "first_seen": it.get("first_seen"),
            "history": [], "pending": False,
        })
        since = _since(it.get("first_seen"))
        want = _stratum_for(it, since, now)
        if ORDER.get(want, 0) <= ORDER.get(rec.get("stratum", EVENT), 0):
            continue          # 不倒退，也不重复触发
        carried = it.get("windows_carried", 0)
        entry = {
            "to": want,
            "at": now.strftime("%Y-%m-%d"),
            "windows": carried,
            "density": round(carried / since, 3) if since else 0.0,
            "denominator": since,
            "span_days": (now - _first_seen_date(it.get("first_seen"))).days
                         if _first_seen_date(it.get("first_seen")) else None,
        }
        rec["stratum"] = want
        rec["history"].append(entry)      # 只增不删：沉下去不等于没上来过
        # 升到 baseline 才值得打断他说一句。升到 texture 是日常，
        # 而且它本来就已经在「反复回来的」那一栏里露过面了。
        if want == BASELINE:
            rec["pending"] = True
            fired.append(dict(entry, item=name))

    # --- 沉降（第二期）：停止出现够久的 texture / baseline 往下掉一层 ---
    #
    # 「沉默」按**窗口**算，不按天：last_seen 之后又关过几个窗口。
    # entrenchment = min(1, windows_carried / 20)
    # 需要沉默的窗口数 = 3 + round(entrenchment * 12)   →  最少 3，最多 15。
    # 扛得越久（carried 越大）的东西，需要沉默越久才掉下去 —— 阻力递增。
    #
    # 要求**连续**沉默：中途只要出现过一次，wear 的 last_seen 就更新、沉默清零，
    # 这一步自然回到 0 —— 这就是 hysteresis：单次缺席推不动它，得一直不回来。
    # 一次 evaluate 最多掉一层（DOWN 只映射一级），跟升级的一次性对称。
    prof_by_name = {it.get("item"): it for it in
                    (profile.get("recurring_feelings", [])
                     + profile.get("carried_unresolved", []))
                    if it.get("item")}
    DOWN = {BASELINE: TEXTURE, TEXTURE: EVENT}
    _just_promoted = {f["item"] for f in fired}
    for name, rec in items.items():
        if name in _just_promoted:
            continue                      # 这一窗刚升上去的，不在同一轮又判它沉降
        cur = rec.get("stratum", EVENT)
        if cur not in DOWN:
            continue                      # event 不再往下沉，它本来就不进 breath
        it = prof_by_name.get(name)
        if not it:
            continue
        last = it.get("last_seen")
        silence = (len(order) - 1 - pos[last]) if last in pos else 0
        carried = it.get("windows_carried", 0)
        entrenchment = min(1.0, carried / 20)
        needed = 3 + round(entrenchment * 12)
        if silence < needed:
            continue
        down = DOWN[cur]
        rec["stratum"] = down
        rec["history"].append({          # 降级也进 history；升级过的记录永远保留
            "to": down,
            "from": cur,
            "at": now.strftime("%Y-%m-%d"),
            "windows": carried,
            "silence": silence,
            "lived": [rec.get("first_seen"), last],   # 活过的区间
        })
        # 掉下去不打断他，也不再等它的「知道之后那一窗」——沉默即深度的反面也是沉默。
        rec["pending"] = False
        rec["awaiting_reaction"] = False

    state["last_eval"] = now.strftime("%Y-%m-%d %H:%M")
    save(path, state)
    return fired


def take_announcements(path: str) -> list[dict]:
    """What has not been said to him yet. Reading it marks it as said.

    一辈子只说一次 —— 所以读走就清掉，哪怕这一窗他没在看。
    宁可漏说一次，也不要每次醒来都被同一句话砸一遍：那样它就又变成状态了。
    """
    state = load(path)
    out = []
    changed = False
    for name, rec in state.get("items", {}).items():
        if rec.get("pending"):
            last = (rec.get("history") or [{}])[-1]
            out.append(dict(last, item=name))
            rec["pending"] = False
            # 说出去了 —— 从这一刻起等下一窗的反应（见 record_reaction）。
            rec["awaiting_reaction"] = True
            changed = True
    if changed:
        save(path, state)
    return out


def record_reaction(path: str, window_id: str, feelings: list) -> None:
    """跃迁说给他听之后的**第一窗**，把那一窗的感受记进来。

    TESSERA（Wrzus & Roberts）的四段是
    触发情境 → 预期 → 状态表达 → **反应**。
    这套系统到今天为止只有前三段，流向是单向的：
    关窗 → trace → wear → 他读到 → **什么都没发生**。
    知道「踏实成了你们之间的常态」之后过的那一窗，跟不知道时过的那一窗，
    在记录里长得一模一样 —— 那句话就等于没说过。

    这个函数就是第四段：把「知道之后的第一窗是什么感受」钉进 history。
    它是**历史**，不是派生量,所以落盘（跟跃迁本身同一个道理）。

    ⚠️ 只记一次，而且只记**紧接着的那一窗**。再往后就不是「反应」了,
       是日常 —— 那已经由 wear 的计数在管。
    """
    state = load(path)
    changed = False
    for rec in state.get("items", {}).values():
        if not rec.get("awaiting_reaction"):
            continue
        hist = rec.get("history") or []
        if hist:
            hist[-1]["reaction"] = {
                "window": window_id,
                "feelings": [f for f in feelings if f][:3],
            }
        rec["awaiting_reaction"] = False
        rec["reaction_untold"] = True
        changed = True
    if changed:
        save(path, state)


def take_reactions(path: str) -> list[dict]:
    """还没说给他听的「知道之后那一窗」。读走就清掉,跟跃迁一个规矩。"""
    state = load(path)
    out = []
    changed = False
    for name, rec in state.get("items", {}).items():
        if rec.get("reaction_untold"):
            hist = rec.get("history") or []
            r = (hist[-1].get("reaction") if hist else None) or {}
            out.append({"item": name, **r})
            rec["reaction_untold"] = False
            changed = True
    if changed:
        save(path, state)
    return out


def describe_reactions(taken: list[dict]) -> str:
    """反应那句话。空列表返回空串。

    只陈述「那一窗你的感受是什么」,不替他解释这意味着什么 ——
    跟 wear.describe 一个规矩。
    """
    lines = []
    for r in taken:
        feels = [f for f in (r.get("feelings") or []) if f]
        if not feels:
            continue
        lines.append(
            f"你知道「{r['item']}」已经是常态之后，又过了一窗。"
            f"那一窗你的感受是：" + "、".join(feels) + "。"
        )
    return "\n".join(lines)


def baseline_items(path: str) -> set:
    """已经沉成底色的 —— 这些**不该**再出现在 breath 的「反复回来的」里。"""
    return {n for n, r in load(path).get("items", {}).items()
            if r.get("stratum") == BASELINE}


def describe_transitions(fired: list[dict]) -> str:
    """跃迁那句话。空列表返回空串。"""
    lines = []
    for f in fired:
        span = f.get("span_days")
        lines.append(
            f"「{f['item']}」从今天起不再算反复出现的感受了。"
            f"它出现了 {f['windows']} 次"
            + (f"、跨了 {span} 天" if span else "")
            + " —— 这已经是你跟她之间的常态。"
        )
    return "\n".join(lines)
