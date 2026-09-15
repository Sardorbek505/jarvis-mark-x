"""JARVIS Mark X — Сервер моста управления браузером (BrowserBridgeServer).

Запускает локальный WebSocket сервер (127.0.0.1:18765) на чистом Python stdlib
с надежной аутентификацией на основе криптографически безопасного токена.
"""

import base64
import hashlib
import hmac
import json
import logging
import secrets
import socket
import struct
import threading
import time
from typing import Any, Dict, List, Optional, Set

from core.media.models import MediaState
from core.media.state import get_media_tracker

logger = logging.getLogger("jarvis-browser-bridge")

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _make_ws_handshake_response(sec_key: str) -> str:
    accept_val = base64.b64encode(hashlib.sha1((sec_key + GUID).encode()).digest()).decode()
    resp = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept_val}\r\n\r\n"
    )
    return resp


def _encode_ws_frame(text: str) -> bytes:
    payload = text.encode("utf-8")
    length = len(payload)
    if length <= 125:
        header = bytes([0x81, length])
    elif length <= 65535:
        header = bytes([0x81, 126]) + struct.pack(">H", length)
    else:
        header = bytes([0x81, 127]) + struct.pack(">Q", length)
    return header + payload


def _decode_ws_frame(data: bytes) -> Optional[str]:
    if len(data) < 2:
        return None
    second_byte = data[1]
    is_masked = (second_byte & 0x80) != 0
    length = second_byte & 0x7F

    idx = 2
    if length == 126:
        if len(data) < 4:
            return None
        length = struct.unpack(">H", data[2:4])[0]
        idx = 4
    elif length == 127:
        if len(data) < 10:
            return None
        length = struct.unpack(">Q", data[2:10])[0]
        idx = 10

    mask = None
    if is_masked:
        if len(data) < idx + 4:
            return None
        mask = data[idx:idx + 4]
        idx += 4

    if len(data) < idx + length:
        return None

    raw = data[idx:idx + length]
    if is_masked and mask:
        unmasked = bytes(b ^ mask[i % 4] for i, b in enumerate(raw))
    else:
        unmasked = raw

    try:
        return unmasked.decode("utf-8")
    except Exception:
        return None


class BrowserBridgeServer:
    """Локальный WebSocket сервер управления браузером с проверкой криптографического токена."""

    _instance: Optional["BrowserBridgeServer"] = None
    _lock = threading.Lock()

    def __init__(self, host: str = "127.0.0.1", port: int = 18765):
        self.host = host
        self.port = port
        # Токен генерируется через secrets при старте JARVIS, не hardcoded, не пишется в логи
        self.token = secrets.token_hex(32)
        self._server_socket: Optional[socket.socket] = None
        self._clients: List[socket.socket] = []
        self._authenticated_clients: Set[socket.socket] = set()
        self._clients_lock = threading.Lock()
        self._tabs: Dict[str, Dict[str, Any]] = {}
        self._tabs_lock = threading.Lock()
        self._responses: Dict[str, Dict[str, Any]] = {}
        self._running = False
        self._thread: Optional[threading.Thread] = None

    @classmethod
    def get_instance(cls) -> "BrowserBridgeServer":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
                    cls._instance.start()
        return cls._instance

    def verify_token(self, provided_token: str) -> bool:
        if not provided_token or not isinstance(provided_token, str):
            return False
        return hmac.compare_digest(provided_token, self.token)

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._server_loop, daemon=True, name="JarvisBrowserBridgeServer")
        self._thread.start()
        logger.info("BrowserBridgeServer: Запущен на ws://%s:%d [аутентификация по токену включена]", self.host, self.port)

    def _server_loop(self):
        try:
            self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._server_socket.bind((self.host, self.port))
            self._server_socket.listen(5)
            self._server_socket.settimeout(1.0)
        except Exception as e:
            logger.warning("BrowserBridgeServer: Не удалось занять порт %d (%s)", self.port, e)
            self._running = False
            return

        while self._running:
            try:
                conn, addr = self._server_socket.accept()
                client_thread = threading.Thread(target=self._handle_client, args=(conn, addr), daemon=True)
                client_thread.start()
            except socket.timeout:
                continue
            except Exception as e:
                if self._running:
                    logger.debug("Bridge accept note: %s", e)

    def _handle_client(self, conn: socket.socket, addr: Any):
        conn.settimeout(5.0)
        try:
            data = conn.recv(4096)
            if not data:
                conn.close()
                return

            req_str = data.decode("utf-8", errors="ignore")
            sec_key = None
            for line in req_str.split("\r\n"):
                if line.lower().startswith("sec-websocket-key:"):
                    sec_key = line.split(":", 1)[1].strip()
                    break

            if sec_key:
                handshake = _make_ws_handshake_response(sec_key)
                conn.sendall(handshake.encode("utf-8"))
            else:
                conn.close()
                return

            # Требуем аутентификацию handshake первым сообщением
            buffer = b""
            authenticated = False
            start_auth_time = time.time()

            while self._running and not authenticated:
                if time.time() - start_auth_time > 5.0:
                    logger.warning("BrowserBridgeServer: Таймаут аутентификации подключения — закрываем сокет")
                    conn.close()
                    return

                try:
                    chunk = conn.recv(4096)
                    if not chunk:
                        conn.close()
                        return
                    buffer += chunk
                    text = _decode_ws_frame(buffer)
                    if text:
                        buffer = b""
                        try:
                            msg = json.loads(text)
                            if msg.get("type") == "AUTH":
                                prov_token = str(msg.get("token", ""))
                                if self.verify_token(prov_token):
                                    authenticated = True
                                    with self._clients_lock:
                                        self._clients.append(conn)
                                        self._authenticated_clients.add(conn)
                                    resp = _encode_ws_frame(json.dumps({"type": "AUTH_SUCCESS", "message": "Authenticated"}))
                                    conn.sendall(resp)
                                else:
                                    logger.warning("BrowserBridgeServer: Отклонено подключение — недействительный token")
                                    resp = _encode_ws_frame(json.dumps({"type": "AUTH_FAILED", "message": "Invalid token"}))
                                    conn.sendall(resp)
                                    conn.close()
                                    return
                            else:
                                logger.warning("BrowserBridgeServer: Отклонено подключение — сообщение отправлено до аутентификации")
                                resp = _encode_ws_frame(json.dumps({"type": "AUTH_REQUIRED", "message": "Authentication required"}))
                                conn.sendall(resp)
                                conn.close()
                                return
                        except Exception:
                            conn.close()
                            return
                except socket.timeout:
                    continue

            conn.settimeout(None)
            buffer = b""
            while self._running:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buffer += chunk
                text = _decode_ws_frame(buffer)
                if text:
                    buffer = b""
                    self._process_ws_message(text)

        except Exception as e:
            logger.debug("Client connection closed: %s", e)
        finally:
            with self._clients_lock:
                if conn in self._clients:
                    self._clients.remove(conn)
                self._authenticated_clients.discard(conn)
            try:
                conn.close()
            except Exception:
                pass

    def _process_ws_message(self, text: str):
        try:
            msg = json.loads(text)
            msg_type = msg.get("type")

            if msg_type == "MEDIA_STATE_UPDATE":
                tab_id = str(msg.get("tab_id", "default"))
                state_data = msg.get("state", {})
                url = msg.get("url", state_data.get("url", ""))
                title = msg.get("title", state_data.get("title", ""))

                tab_info = {
                    "tab_id": tab_id,
                    "window_id": str(msg.get("window_id", "")),
                    "url": url,
                    "title": title,
                    "state": state_data,
                    "last_updated": time.time()
                }

                with self._tabs_lock:
                    self._tabs[tab_id] = tab_info

                self._sync_tracker(tab_info)

            elif msg_type == "COMMAND_RESPONSE":
                tab_id = str(msg.get("tab_id", "default"))
                with self._tabs_lock:
                    self._responses[tab_id] = msg

        except Exception as e:
            logger.debug("Process message error: %s", e)

    def _sync_tracker(self, tab_info: Dict[str, Any]):
        state_data = tab_info.get("state", {})
        paused = state_data.get("paused", True)
        status = MediaState.PAUSED if paused else MediaState.PLAYING

        tracker = get_media_tracker()
        active_session = tracker.get_active_session()

        if active_session:
            if not active_session.tab_id or active_session.tab_id == tab_info["tab_id"]:
                active_session.tab_id = tab_info["tab_id"]
                active_session.window_id = tab_info["window_id"]
                if tab_info.get("url"):
                    active_session.source_url = tab_info["url"]

                tracker.update_session_state(
                    status=status,
                    position_seconds=state_data.get("currentTime"),
                    volume=state_data.get("volume"),
                    muted=state_data.get("muted"),
                )

                if state_data.get("duration") and state_data["duration"] > 0:
                    active_session.duration_seconds = float(state_data["duration"])

    def send_command(self, command: str, tab_id: Optional[str] = None, timeout: float = 2.0, **kwargs) -> Dict[str, Any]:
        """Отправляет команду в расширение для выполнения в браузере только аутентифицированным клиентам."""
        msg = {"command": command, **kwargs}
        if tab_id:
            msg["tab_id"] = str(tab_id)

        payload = json.dumps(msg)
        frame = _encode_ws_frame(payload)

        with self._clients_lock:
            clients_copy = list(self._authenticated_clients) if self._authenticated_clients else list(self._clients)

        if not clients_copy:
            return {"success": False, "error": "BRIDGE_NOT_CONNECTED"}

        for conn in clients_copy:
            try:
                conn.sendall(frame)
            except Exception as e:
                logger.debug("Send command frame error: %s", e)

        start_t = time.time()
        target_key = str(tab_id) if tab_id else None
        while time.time() - start_t < timeout:
            with self._tabs_lock:
                if target_key and target_key in self._responses:
                    return self._responses.pop(target_key)
                elif not target_key and self._responses:
                    k, v = self._responses.popitem()
                    return v
            time.sleep(0.02)

        return {"success": True, "message": "Command dispatched via bridge"}

    def close_tab(self, tab_id: str, timeout: float = 2.0) -> Dict[str, Any]:
        return self.send_command("close_tab", tab_id=tab_id, timeout=timeout)

    def get_tab_info(self, tab_id: str) -> Optional[Dict[str, Any]]:
        with self._tabs_lock:
            return self._tabs.get(str(tab_id))

    def get_all_tabs(self) -> List[Dict[str, Any]]:
        with self._tabs_lock:
            return list(self._tabs.values())

    def is_bridge_connected(self) -> bool:
        with self._clients_lock:
            return len(self._authenticated_clients) > 0 or len(self._clients) > 0


def get_bridge_server() -> BrowserBridgeServer:
    return BrowserBridgeServer.get_instance()