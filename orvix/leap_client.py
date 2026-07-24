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

# leapd not running at all fails the TCP connect almost instantly (OSError),
# but a wedged leapd process can leave its listening socket open while never
# completing the websocket handshake. without a bound on the handshake
# itself, that hangs stream_frames forever with zero feedback: the gui just
# sits on "starting..." and the cli/calibrate/viz tools all hang the same
# way, since none of them wrap this call with their own timeout.
CONNECT_TIMEOUT_SECONDS = 5.0


def _reject_non_finite(constant: str) -> float:
    # json.loads accepts the non-standard NaN/Infinity/-Infinity literals by
    # default. a corrupted or glitching leapd frame that ever emits one of
    # these for a coordinate would otherwise parse fine and flow straight
    # into the One Euro Filter, which has no recovery from a NaN sample: once
    # x_prev is NaN, every future filtered output is NaN forever, freezing
    # the cursor until the app is restarted. raising here makes such a frame
    # get skipped the same way a malformed non-json message already is.
    raise ValueError(f"non-finite json constant: {constant}")

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
        ws = await asyncio.wait_for(
            websockets.connect(url), timeout=CONNECT_TIMEOUT_SECONDS
        )
    except (OSError, websockets.InvalidHandshake) as exc:
        raise LeapConnectionError(
            f"couldn't connect to leapd at {url}. is leapd running? "
            f"see docs/SETUP.md step 3. original error: {exc}"
        ) from exc
    except asyncio.TimeoutError as exc:
        raise LeapConnectionError(
            f"leapd at {url} didn't finish the connection handshake within "
            f"{CONNECT_TIMEOUT_SECONDS:.0f}s. it may be running but wedged, "
            f"try restarting it: sudo launchctl kickstart -k "
            f"system/com.leapmotion.leapd"
        ) from exc

    heartbeat_task = asyncio.create_task(_send_heartbeat(ws))

    try:
        # ask for background data so we still get frames when some other
        # app has leapd's "focus", see the protocol notes up top
        await ws.send(json.dumps({"background": True}))

        async for raw_message in ws:
            try:
                frame = json.loads(raw_message, parse_constant=_reject_non_finite)
            except (json.JSONDecodeError, ValueError):
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


def extended_fingers_for_hand(frame: dict, hand: dict) -> set[int] | None:
    """
    which fingers of this hand are currently extended (straight), as a set of
    SDK finger types (0 thumb .. 4 pinky). used to tell a real closed fist
    from a partial curl for the grab gesture.

    returns None, not an empty set, when the frame carries no usable
    pointables for this hand (older protocol versions omit them, or none
    reported the "extended" flag). callers must treat None as "can't tell"
    and not as "no fingers extended", otherwise every frame would look like a
    fist. an empty set means pointables were present and all fingers curled.
    """
    hand_id = hand.get("id")
    extended: set[int] = set()
    saw_flag = False
    for p in frame.get("pointables", []):
        if p.get("handId") != hand_id:
            continue
        finger_type = p.get("type")
        is_extended = p.get("extended")
        if finger_type is None or is_extended is None:
            continue
        saw_flag = True
        if is_extended:
            extended.add(finger_type)
    return extended if saw_flag else None


def pick_hand(
    frame: dict, preferred_hand: str, last_hand_id: object | None = None
) -> dict | None:
    """
    pull the hand we care about out of a frame dict, or None if it's not
    visible right now.

    preferred_hand is "left", "right", or "first" (whichever leapd listed
    first, cheapest option if you don't care which hand). if the preferred
    hand isn't in view but some hand is, we still return None rather than
    silently falling back to whatever hand is available, since switching
    hands mid-gesture would be a confusing surprise for the cursor mapping.

    "first" has no notion of left/right to anchor on, so a second hand
    entering frame (a bystander, or the user's own other hand) could
    otherwise silently steal tracking the instant it sorts ahead of the
    real one in leapd's list. pass the id of the hand you picked last frame
    as last_hand_id and, if that id is still present, we keep tracking it
    instead of blindly taking hands[0]; only fall back to hands[0] once
    that id is genuinely gone.
    """
    hands = frame.get("hands", [])
    if not hands:
        return None

    if preferred_hand == "first":
        if last_hand_id is not None:
            for hand in hands:
                if hand.get("id") == last_hand_id:
                    return hand
        return hands[0]

    for hand in hands:
        if hand.get("type") == preferred_hand:
            return hand

    return None
