"""
tests for stream_latest_frames' drop-stale-frames behaviour, and for the
redundant-move skipping in mouse_control. both exist because macOS stalls
CGEventPost for a few hundred ms under load, and an in-order loop never
recovers from that. see the docstrings in leap_client/mouse_control.
"""

import asyncio

import pytest

from orvix import leap_client
from orvix.leap_client import stream_latest_frames


def fake_stream(frames, delay=0.0):
    """stand in for stream_frames, emitting the given frames."""

    async def _gen(url=None):
        for f in frames:
            if delay:
                await asyncio.sleep(delay)
            yield f

    return _gen


@pytest.mark.asyncio
async def test_a_fast_consumer_sees_everything(monkeypatch):
    frames = [{"hands": [], "n": i} for i in range(5)]
    monkeypatch.setattr(leap_client, "stream_frames", fake_stream(frames, delay=0.01))

    seen = [f["n"] async for f in stream_latest_frames()]
    assert seen == [0, 1, 2, 3, 4]


@pytest.mark.asyncio
async def test_a_slow_consumer_skips_stale_frames_and_gets_the_newest(monkeypatch):
    """
    the actual bug: consumer stalls (like CGEventPost does), frames pile up.
    it must NOT then replay the backlog, it must jump to the newest.
    """
    frames = [{"hands": [], "n": i} for i in range(60)]
    monkeypatch.setattr(leap_client, "stream_frames", fake_stream(frames, delay=0.001))

    seen = []
    async for f in stream_latest_frames():
        seen.append(f["n"])
        await asyncio.sleep(0.02)  # slow consumer, ~20x the frame interval

    assert len(seen) < 60, "should have dropped stale frames, not replayed them"
    assert seen[-1] == 59, "must end on the newest frame"
    assert seen == sorted(seen), "frames must stay in order"


@pytest.mark.asyncio
async def test_errors_from_the_reader_reach_the_consumer(monkeypatch):
    async def _boom(url=None):
        raise leap_client.LeapConnectionError("leapd is not running")
        yield  # noqa: unreachable, makes this an async generator

    monkeypatch.setattr(leap_client, "stream_frames", _boom)

    with pytest.raises(leap_client.LeapConnectionError, match="not running"):
        async for _ in stream_latest_frames():
            pass


def test_move_skips_a_redundant_repost(monkeypatch):
    posted = []

    class FakeQuartz:
        kCGEventLeftMouseDragged = "drag"
        kCGEventMouseMoved = "move"
        kCGEventLeftMouseDown = "down"
        kCGEventLeftMouseUp = "up"
        kCGMouseButtonLeft = 0
        kCGHIDEventTap = 0

        @staticmethod
        def CGEventCreateMouseEvent(src, kind, pos, btn):
            return (kind, pos)

        @staticmethod
        def CGEventPost(tap, ev):
            posted.append(ev)

        @staticmethod
        def CGEventCreate(src):
            return None

        @staticmethod
        def CGEventGetLocation(ev):
            class P:
                x = y = 0.0

            return P()

    from orvix import mouse_control

    monkeypatch.setattr(mouse_control, "Quartz", FakeQuartz)

    m = mouse_control.QuartzMouseController()
    m.move(10, 10)
    m.move(10, 10)  # identical, should be skipped
    m.move(11, 10)
    assert len(posted) == 2

    # pressing changes move -> drag semantics, so the next post must go out
    # even at the same pixel
    posted.clear()
    m.mouse_down()
    m.move(11, 10)
    assert any(kind == "drag" for kind, _ in posted)
