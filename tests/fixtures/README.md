# fixtures

`sample_frames.json` goes here once we've got the Leap driver running and can
capture a real batch of frames off the websocket stream. using real captured
data instead of hand-written fake json means our tests actually reflect what
this specific LM-010 + leapd build sends, not what old docs say it should
send (there's known drift across protocol versions, see docs/SETUP.md).
