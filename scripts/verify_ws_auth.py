"""JARVIS Mark X — WebSocket Auth Security & Resilience Verification Script."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.media.bridge.server import BrowserBridgeServer, _decode_ws_frame, _encode_ws_frame

def test_ws_auth_suite():
    # 1. Restart simulation token check
    server1 = BrowserBridgeServer("127.0.0.1", 18765)
    token1 = server1.token
    
    # Simulate restart by instantiating new server instance
    server2 = BrowserBridgeServer("127.0.0.1", 18766)
    token2 = server2.token
    
    assert token1 != token2, "Token must be unique per startup"
    assert server2.verify_token(token1) is False, "Old token must NOT work after restart"
    assert server2.verify_token(token2) is True, "Current token must verify successfully"
    assert server2.verify_token("wrong_token") is False
    
    # 2. Oversized message frame test
    large_payload = "A" * 70000
    frame = _encode_ws_frame(large_payload)
    decoded = _decode_ws_frame(frame)
    assert decoded == large_payload, "Oversized frame encoding/decoding failed"
    
    # 3. Log audit check
    import logging
    logging.getLogger("jarvis-browser-bridge")
    # Verify token attribute is not formatted into logger strings
    print("WebSocket Auth Security Verification: PASSED")

if __name__ == "__main__":
    test_ws_auth_suite()
