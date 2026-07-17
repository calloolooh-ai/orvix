"""
leap_client.py

owns the connection to the leapd tracking service. this is the only module
that knows anything about the raw websocket wire protocol, everything
downstream of this just works with plain python dicts.

not implemented yet, this is a scaffold stub. see the plan doc / next commit
for the real implementation.
"""

# TODO: async websocket connection to ws://localhost:6437/v6.json
# TODO: version negotiation off the first message the server sends
# TODO: heartbeat loop so leapd doesn't think we died
# TODO: request background:true so we still get data when not "focused"
# TODO: async generator that yields parsed frame dicts to the caller
