"""Search throttle: the whole point is not tripping Bilibili's risk control."""

from __future__ import annotations

import pytest

from core.throttle import MinIntervalThrottle


@pytest.mark.asyncio
async def test_first_call_does_not_wait():
    throttle = MinIntervalThrottle(5.0)
    assert await throttle.wait() == 0.0


@pytest.mark.asyncio
async def test_second_call_waits_the_remaining_interval():
    clock = {"now": 0.0}
    throttle = MinIntervalThrottle(5.0, clock=lambda: clock["now"])

    async def fake_sleep(seconds: float) -> None:
        clock["now"] += seconds

    # Patch the sleep the throttle uses so the test is instant but still
    # verifies the computed delay.
    import core.throttle as module

    original = module.asyncio.sleep
    module.asyncio.sleep = fake_sleep
    try:
        assert await throttle.wait() == 0.0
        clock["now"] += 2.0
        assert await throttle.wait() == pytest.approx(3.0)
        # already inside the interval -> no extra wait beyond the minimum
        assert await throttle.wait() == pytest.approx(5.0)
    finally:
        module.asyncio.sleep = original


@pytest.mark.asyncio
async def test_zero_interval_never_waits():
    throttle = MinIntervalThrottle(0.0)
    assert await throttle.wait() == 0.0
    assert await throttle.wait() == 0.0
