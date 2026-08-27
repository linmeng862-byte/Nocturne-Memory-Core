from __future__ import annotations

# ============================================================
# Module: Door cost (door_cost.py)
# 模块：门的代价
#
# Guessing wrong at the front door used to cost nothing: unlimited tries,
# no delay, no lockout. A five-character password is 676,000 combinations,
# which at fifty tries a second is under four hours.
# 猜错一次的成本原本是零：无限次、零延迟、不锁定。
# 五位密码是 676,000 种，按每秒五十次试，不到四个小时。
#
# The fix is not a longer password — that is hers to choose. The fix is
# that a wrong answer costs time. Password strength and attempt cost
# multiply; only one of them is a decision someone else has to make.
# 解法不是更长的密码 —— 那是她的选择。解法是**答错要花时间**。
# 密码强度和尝试代价是相乘的，而只有其中一个需要别人来做决定。
#
# Two rules shape everything here:
#
#   1. Delay, never lock out. A lockout is a way to be locked out of your
#      own memory by someone else's traffic. A delay cannot be — the worst
#      an attacker can do is make her wait a few seconds.
#      只延迟，不锁定。锁定意味着别人的流量能把她关在自己的记忆外面。
#      延迟做不到这件事 —— 攻击者最多让她多等几秒。
#
#   2. The delay is on the failing path only. A correct password is never
#      slowed, no matter what anyone else has been doing. So there is no
#      configuration in which this stops her from getting in.
#      延迟只加在**失败**那条路上。密码对了就从不减速，不管别人在干什么。
#      所以不存在任何一种配置，会让这东西把她挡在外面。
#
# Depended on by: server.py
# ============================================================

import threading

WINDOW_SECONDS = 900.0      # failures older than this stop counting
FREE_TRIES = 3              # fat fingers are not an attack
BASE_DELAY = 0.25
MAX_DELAY = 4.0
MAX_TRACKED = 4096          # bounded: a rotating-IP attack must not eat memory

GLOBAL_KEY = ""             # the lane no header can dodge


def delay_for_count(n: int) -> float:
    """Seconds to wait before answering the n-th failure.

    The exponent is clamped before it is used, not after. Computing
    2 ** 1024 and then taking min() raises OverflowError — and the caller
    swallows exceptions so the door keeps answering, which means the delay
    would switch itself off under exactly the sustained attack it exists
    for. The ceiling has to be reached by arithmetic that cannot fail.
    指数在**用之前**就夹住,不是算完再夹。先算 2 ** 1024 再 min() 会溢出 ——
    而调用方会吞掉异常让门继续回话,于是这个延迟会在它唯一存在意义的那种
    持续攻击下把自己关掉。上限必须由一段不会失败的算术抵达。
    """
    if n <= FREE_TRIES:
        return 0.0
    steps = min(n - FREE_TRIES - 1, 32)
    return min(MAX_DELAY, BASE_DELAY * (2 ** steps))


class FailureLedger:
    """Recent credential failures, per key. In memory, per process."""

    def __init__(self, window: float = WINDOW_SECONDS, max_tracked: int = MAX_TRACKED):
        self._window = window
        self._max = max_tracked
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        cutoff = now - self._window
        for key in list(self._hits):
            kept = [t for t in self._hits[key] if t > cutoff]
            if kept:
                self._hits[key] = kept
            else:
                del self._hits[key]
        # An attacker rotating X-Forwarded-For would otherwise create a new
        # key per request. Drop the coldest; the global lane is unaffected
        # because it is refreshed on every single failure.
        # 攻击者轮换 X-Forwarded-For 会让每个请求都建一个新 key。
        # 丢掉最冷的那些；全局那条不受影响，因为每次失败都会刷新它。
        if len(self._hits) > self._max:
            ranked = sorted(self._hits.items(), key=lambda kv: kv[1][-1])
            for key, _ in ranked[: len(self._hits) - self._max]:
                if key != GLOBAL_KEY:
                    del self._hits[key]

    def count(self, key: str, now: float) -> int:
        with self._lock:
            cutoff = now - self._window
            return sum(1 for t in self._hits.get(key, ()) if t > cutoff)

    def note_failure(self, key: str, now: float) -> float:
        """Record one wrong answer. Returns how long this one should wait.

        The global lane is always recorded too. Per-key is best effort — the
        client address behind a proxy comes from a header the client writes,
        so it can be rotated. The global lane cannot be, and legitimate use
        produces almost no failures, so a budget there is nearly free.
        全局那条永远也记一笔。按 key 只是尽力而为 —— 代理后面的客户端地址
        来自客户端自己写的 header，可以轮换。全局那条换不掉，
        而正常使用几乎不产生失败，所以那儿设预算几乎不花什么。
        """
        with self._lock:
            self._prune(now)
            cutoff = now - self._window
            keys = [GLOBAL_KEY] + ([key] if key else [])
            for k in keys:
                self._hits.setdefault(k, []).append(now)
            worst = max(sum(1 for t in self._hits.get(k, ()) if t > cutoff)
                        for k in keys)
        return delay_for_count(worst)

    def note_success(self, key: str, now: float) -> None:
        """Forget this key's failures. Her own typos must not accumulate.

        The global lane is deliberately NOT cleared: one person logging in
        should not hand a running attack a fresh budget.
        刻意**不**清全局那条：一个人登录成功，不该给正在跑的攻击重置预算。
        """
        with self._lock:
            self._hits.pop(key, None)


def client_key(headers, fallback: str = "") -> str:
    """Best-effort caller identity. Never trusted, only used to bucket."""
    xff = ""
    try:
        xff = str(headers.get("x-forwarded-for") or "").strip()
    except Exception:
        xff = ""
    if xff:
        return xff.split(",")[0].strip()[:64]
    return str(fallback or "")[:64]
