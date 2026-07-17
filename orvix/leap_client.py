"""
leap_client.py

owns the connection to the leapd tracking service. this is the only module
that knows anything about the raw websocket wire protocol, everything
downstream of this just works with plain python dicts.

protocol notes (see docs/SETUP.md for the full writeup):
- connect to ws://localhost:6437/v6.json
- the first message leapd sends back reports the negotiated protocol
  version, we don't strictly need to parse it for v1 (we're not using any
  version-gated features) but we log it for debugging
- send {"background": true} right after connecting so we keep getting frames
  even when we don't have leapd's notion of "focus" (unrelated to macOS
  window focus, see docs/SETUP.md)
- we don't request {"enableGestures": true} since v1 only needs
  pinchStrength/grabStrength off the hand object, not leapd's built-in
  circle/swipe/keyTap gesture detection
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

import websockets
from websockets.asyncio.client import ClientConnection

logger = logging.getLogger("orvix.leap_client")

LEAP_WS_URL = "ws://localhost:6437/v6.json"

# leapd doesn't strictly require a heartbeat on modern protocol versions,
# but older docs mention clients needing to send something periodically or
# risk being treated as dead. sending a harmless no-op message on an
# interval costs nothing and protects against that on whatever ancient
# leapd build ends up running here.
HEARTBEAT_INTERVAL_SECONDS = 5.0


class LeapConnectionError(RuntimeError):
    """raised when we can't connect to leapd at all, e.g. it's not running."""


async def _send_heartbeat(ws: ClientConnection) -> None:
    """background task, pings leapd on an interval so it doesn't think we died."""
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
        try:
            await ws.send(json.dumps({"heartbeat": True}))
        except websockets.ConnectionClosed:
            return


async def stream_frames(url: str = LEAP_WS_URL) -> AsyncIterator[dict]:
    """
    connect to leapd and yield parsed frame dicts forever, one per message.

    raises LeapConnectionError if the initial connection fails (most likely
    cause: leapd isn't running, see docs/SETUP.md step 3). if the connection
    drops mid-stream after connecting successfully, this just returns and
    lets the caller decide whether to reconnect, we don't retry internally
    so callers stay in control of reconnect/backoff behavior.
    """
    try:
        ws = await websockets.connect(url)
    except (OSError, websockets.InvalidHandshake) as exc:
        raise LeapConnectionError(
            f"couldn't connect to leapd at {url}. is leapd running? "
            f"see docs/SETUP.md step 3. original error: {exc}"
        ) from exc

    heartbeat_task = asyncio.create_task(_send_heartbeat(ws))

    try:
        # ask for background data so we still get frames when some other
        # app has leapd's "focus", see the protocol notes up top
        await ws.send(json.dumps({"background": True}))

        async for raw_message in ws:
            try:
                frame = json.loads(raw_message)
            except json.JSONDecodeError:
                logger.warning("got a non-json message from leapd, skipping: %r", raw_message)
                continue

            # the very first message is leapd reporting the negotiated
            # protocol version, not a real tracking frame. it doesn't have
            # a "hands" key, so this check quietly skips it as a side effect
            # while also protecting us from any other unexpected non-frame
            # message shape.
            if "hands" not in frame:
                logger.debug("non-frame message from leapd: %r", frame)
                continue

            yield frame
    finally:
        heartbeat_task.cancel()
        await ws.close()


async def stream_latest_frames(url: str = LEAP_WS_URL) -> AsyncIterator[dict]:
    """
    like stream_frames, but only ever yields the *newest* frame, dropping
    any that arrived while you were busy with the last one.

    this exists because of a real measured failure. macOS intermittently
    stalls CGEventPost for a few hundred ms (347ms observed). frames keep
    arriving at ~75/sec throughout, so a naive in-order loop comes back
    from the stall ~26 frames behind and then faithfully replays every one
    of them. it never catches up, it just keeps rendering stale hand
    positions, so the cursor trails you by seconds. lag was measured
    ballooning to 2.7s and staying there.

    for cursor control an old frame is worthless, the only one that matters
    is where your hand is *now*. so we let the reader run ahead and keep a
    single slot: if the consumer is slow, older frames get overwritten and
    quietly dropped. that bounds lag to roughly one stall instead of
    accumulating it forever.
    """
    queue: asyncio.Queue = asyncio.Queue(maxsize=1)
    sentinel = object()
    error: BaseException | None = None

    async def reader() -> None:
        nonlocal error
        try:
            async for frame in stream_frames(url):
                if queue.full():
                    # drop the frame nobody got to, it's already stale
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                queue.put_nowait(frame)
        except (asyncio.CancelledError, GeneratorExit):
            raise
        except BaseException as exc:  # noqa: BLE001 - hand it to the consumer to raise
            error = exc
        finally:
            # tell the consumer we're done. this has to *await* rather than
            # put_nowait: if the last real frame is still sitting unconsumed
            # the queue is full, and a conditional put_nowait would silently
            # skip the sentinel and leave the consumer blocked on get()
            # forever. awaiting parks until the consumer takes that frame.
            # if the consumer bailed first it cancels us, which lands here
            # as CancelledError and there's nobody left to notify anyway.
            try:
                await queue.put(sentinel)
            except asyncio.CancelledError:
                pass

    task = asyncio.create_task(reader())
    try:
        while True:
            item = await queue.get()
            if item is sentinel:
                if error is not None:
                    raise error
                return
            yield item
    finally:
        task.cancel()


def fingertips_for_hand(frame: dict, hand: dict) -> dict[int, tuple[float, float, float]]:
    """
    map finger type -> tip position, for one hand.

    leapd reports fingers in a top-level "pointables" list rather than
    nested inside the hand, so they're matched back up by handId. finger
    types are the SDK's: 0 thumb, 1 index, 2 middle, 3 ring, 4 pinky.

    returns {} when the frame carries no pointables, which callers must
    cope with: whether pointables are present depends on the protocol
    version leapd negotiated, and we don't want a missing key to take out
    cursor movement, which needs none of this.
    """
    hand_id = hand.get("id")
    tips: dict[int, tuple[float, float, float]] = {}
    for p in frame.get("pointables", []):
        if p.get("handId") != hand_id:
            continue
        tip = p.get("tipPosition")
        finger_type = p.get("type")
        if tip is None or finger_type is None:
            continue
        tips[finger_type] = tuple(tip)
    return tips


def pick_hand(frame: dict, preferred_hand: str) -> dict | None:
    """
    pull the hand we care about out of a frame dict, or None if it's not
    visible right now.

    preferred_hand is "left", "right", or "first" (whichever leapd listed
    first, cheapest option if you don't care which hand). if the preferred
    hand isn't in view but some hand is, we still return None rather than
    silently falling back to whatever hand is available, since switching
    hands mid-gesture would be a confusing surprise for the cursor mapping.
    """
    hands = frame.get("hands", [])
    if not hands:
        return None

    if preferred_hand == "first":
        return hands[0]

    for hand in hands:
        if hand.get("type") == preferred_hand:
            return hand

    return None
