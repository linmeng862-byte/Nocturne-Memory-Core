# ============================================================
# Regression: a wrong answer at the door has to cost something
#
# The door had no rate limit, no lockout, no delay. A five-character
# password — 676,000 combinations — falls in under four hours at fifty
# tries a second. Password strength and attempt cost multiply, and only
# one of them requires someone to change their password.
# 门上没有限流、没有锁定、没有延迟。五位密码 676,000 种，
# 按每秒五十次试不到四小时就穿了。
# 密码强度和尝试代价是相乘的，而只有其中一个需要谁去改密码。
#
# Two properties are locked down here, and they are the whole design:
#   1. it is never a lockout — she cannot be shut out by someone else
#   2. the correct password is never delayed
# ============================================================

import door_cost


def _ledger():
    return door_cost.FailureLedger()


def test_typos_are_free():
    """Three wrong tries cost nothing. Fat fingers are not an attack."""
    led = _ledger()
    for i in range(door_cost.FREE_TRIES):
        assert led.note_failure("1.2.3.4", 1000.0 + i) == 0.0


def test_cost_climbs_after_the_free_tries():
    led = _ledger()
    delays = [led.note_failure("1.2.3.4", 1000.0 + i) for i in range(12)]
    assert delays[: door_cost.FREE_TRIES] == [0.0] * door_cost.FREE_TRIES
    tail = delays[door_cost.FREE_TRIES:]
    assert tail == sorted(tail), f"代价必须单调不降：{tail}"
    assert max(delays) == door_cost.MAX_DELAY


def test_cost_is_capped_so_it_is_never_a_lockout():
    """Bounded wait, forever. An unbounded delay is a lockout wearing a hat."""
    led = _ledger()
    for i in range(500):
        d = led.note_failure("1.2.3.4", 1000.0 + i)
    assert d == door_cost.MAX_DELAY
    assert door_cost.MAX_DELAY <= 5.0


def test_a_correct_password_clears_her_own_typos():
    led = _ledger()
    for i in range(6):
        led.note_failure("1.2.3.4", 1000.0 + i)
    led.note_success("1.2.3.4", 1006.0)
    assert led.count("1.2.3.4", 1006.0) == 0


def test_success_does_not_refund_the_global_budget():
    """One person logging in must not hand a running attack a fresh budget."""
    led = _ledger()
    for i in range(20):
        led.note_failure("9.9.9.9", 1000.0 + i)
    before = led.count(door_cost.GLOBAL_KEY, 1020.0)
    led.note_success("1.2.3.4", 1020.0)
    assert led.count(door_cost.GLOBAL_KEY, 1020.0) == before


def test_failures_expire_out_of_the_window():
    """Yesterday's wrong guesses must not tax today's login."""
    led = _ledger()
    for i in range(10):
        led.note_failure("1.2.3.4", 1000.0 + i)
    later = 1000.0 + door_cost.WINDOW_SECONDS + 60
    assert led.note_failure("1.2.3.4", later) == 0.0


def test_rotating_the_forwarded_header_does_not_dodge_the_cost():
    """Per-IP is best effort — the header is written by the caller. The
    global lane is what an attacker cannot rotate away from."""
    led = _ledger()
    last = 0.0
    for i in range(40):
        last = led.note_failure(f"10.0.0.{i}", 1000.0 + i)
    assert last == door_cost.MAX_DELAY, "换 IP 就绕过了代价"


def test_rotating_ips_cannot_grow_memory_without_bound():
    led = door_cost.FailureLedger(max_tracked=64)
    for i in range(5000):
        led.note_failure(f"10.{i // 256}.{i % 256}.1", 1000.0 + i * 0.001)
    assert len(led._hits) <= 64 + 1  # +1 for the global lane


def test_global_lane_survives_the_pruning():
    """Evicting cold keys must never evict the one lane that cannot be dodged."""
    led = door_cost.FailureLedger(max_tracked=8)
    led.note_failure("first", 1000.0)
    for i in range(200):
        led.note_failure(f"10.0.0.{i}", 1001.0 + i * 0.001)
    assert door_cost.GLOBAL_KEY in led._hits


def test_client_key_prefers_the_forwarded_header():
    assert door_cost.client_key({"x-forwarded-for": "1.2.3.4, 5.6.7.8"}) == "1.2.3.4"
    assert door_cost.client_key({}, fallback="9.9.9.9") == "9.9.9.9"
    assert door_cost.client_key({}) == ""


def test_client_key_is_length_bounded():
    assert len(door_cost.client_key({"x-forwarded-for": "a" * 9999})) <= 64


def test_brute_force_cost_in_the_real_shape():
    """676,000 combinations, sequential, at the capped delay."""
    combos = 26 * 26 * 10 * 10 * 10
    days = combos * door_cost.MAX_DELAY / 86400
    assert days > 7, f"五位密码还是能在 {days:.1f} 天内跑穿"
