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
