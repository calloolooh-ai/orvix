"""
main.py

entry point. wires leap_client -> gesture_interpreter -> coord_mapper ->
mouse_control together into one asyncio loop.

flags (planned):
    --calibrate   run the calibration flow instead of live control
    --dry-run     log intended mouse actions instead of moving the real cursor
    --verbose     print frame/gesture debug info

not implemented yet, this is a scaffold stub.
"""

# TODO: argparse for --calibrate / --dry-run / --verbose
# TODO: asyncio.run() the main control loop
# TODO: graceful reconnect if leapd drops the websocket connection mid-session


def main() -> None:
    raise NotImplementedError("orvix isn't built yet, check back after the leap_client step")


if __name__ == "__main__":
    main()
