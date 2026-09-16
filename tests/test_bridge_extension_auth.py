"""Расширение Chrome подключается к мосту без ручного токена — по Origin.

До этого мост не мог работать никогда: расширение стучалось на 8765 при
сервере на 18765, а токен генерировался при старте и расширению не отдавался.
"""

import json
import re
import socket
import time
from pathlib import Path

import pytest

from core.media.bridge.server import BrowserBridgeServer, _decode_ws_frame


@pytest.fixture
def server():
    srv = BrowserBridgeServer(port=0)  # свободный порт
    srv.start()
    for _ in range(50):
        if srv._server_socket is not None:
            break
        time.sleep(0.02)
    yield srv
    srv.stop()


def _handshake(port: int, origin: str | None) -> bytes:
    s = socket.create_connection(("127.0.0.1", port), timeout=3)
    headers = [
        "GET / HTTP/1.1", f"Host: 127.0.0.1:{port}", "Upgrade: websocket", "Connection: Upgrade",
        "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==", "Sec-WebSocket-Version: 13",
    ]
    if origin:
        headers.append(f"Origin: {origin}")
    s.sendall(("\r\n".join(headers) + "\r\n\r\n").encode())
    data = b""
    deadline = time.time() + 2
    while time.time() < deadline:
        try:
            chunk = s.recv(4096)
        except socket.timeout:
            break
        if not chunk:
            break
        data += chunk
        if b"AUTH_" in data or b"\r\n\r\n" in data and len(data) > 200:
            break
    s.close()
    return data


def test_extension_origin_is_authenticated_without_token(server):
    data = _handshake(server.port, "chrome-extension://abcdefghijklmnopabcdefghijklmnop")
    assert b"101" in data.split(b"\r\n")[0]
    frame = data.split(b"\r\n\r\n", 1)[1]
    assert json.loads(_decode_ws_frame(frame))["type"] == "AUTH_SUCCESS"


def test_web_page_origin_is_not_authenticated(server):
    data = _handshake(server.port, "http://evil.example")
    frame = data.split(b"\r\n\r\n", 1)[1] if b"\r\n\r\n" in data else b""
    assert b"AUTH_SUCCESS" not in frame


def test_extension_points_to_the_server_port():
    js = (Path(__file__).resolve().parents[1] / "extension" / "background.js").read_text(encoding="utf-8")
    port = int(re.search(r"ws://127\.0\.0\.1:(\d+)", js).group(1))
    assert port == BrowserBridgeServer().port
