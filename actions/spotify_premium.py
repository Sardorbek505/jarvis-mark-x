"""Spotify Premium через Web API — быстрый прямой путь.

Один поиск (трек, исполнитель, альбом, плейлист сразу) → лучшее совпадение →
PUT /me/player/play на компьютер пользователя → проверка, что заиграло.
Нет активного устройства — запускаем Spotify и ждём, пока он появится.
Раньше путь шёл через список всех плейлистов пользователя, поиск, повторный
поиск плейлистов и т. д. — до восьми запросов подряд.

Вход — один раз: «Джарвис, подключи Spotify» открывает страницу входа,
ответ ловит локальный адрес (redirect_uri из ключей, по умолчанию
http://127.0.0.1:8888/callback — он должен быть указан в приложении на
developer.spotify.com). Токен обновляется сам.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
import urllib.parse

logger = logging.getLogger(__name__)

API = "https://api.spotify.com/v1"
_TIMEOUT = (3, 6)
_auth = None
_premium: bool | None = None


# ── авторизация ───────────────────────────────────────────────────────────────

def _keys() -> dict:
    from core.paths import load_api_keys
    return load_api_keys()


def auth():
    """SpotifyAuth из ключей Джарвиса (или None, если ключей нет)."""
    global _auth
    if _auth is None:
        k = _keys()
        cid = (k.get("spotify_client_id") or "").strip()
        sec = (k.get("spotify_client_secret") or "").strip()
        if not cid or not sec:
            return None
        from tools.spotify.auth import SpotifyAuth
        _auth = SpotifyAuth(cid, sec, (k.get("spotify_redirect_uri") or
                                       "http://127.0.0.1:8888/callback").strip())
        seed = (k.get("spotify_refresh_token") or "").strip()
        _auth.seed_refresh_token = seed
        if seed and not _auth.refresh_token:
            _auth.set_refresh_token(seed)
    return _auth


def ready() -> bool:
    a = auth()
    try:
        return bool(a and a.refresh_token and a.get_access_token())
    except Exception:
        return False


def login(player=None, wait_sec: int = 150) -> str:
    """Открыть вход в Spotify и поймать код на redirect_uri."""
    a = auth()
    if not a:
        return ("Нет ключей Spotify (spotify_client_id и spotify_client_secret). "
                "Создайте приложение на developer.spotify.com и впишите их в настройки.")
    red = urllib.parse.urlparse(a.redirect_uri)
    if red.hostname not in ("127.0.0.1", "localhost"):
        return f"redirect_uri должен быть локальным (сейчас {a.redirect_uri})."
    from http.server import BaseHTTPRequestHandler, HTTPServer
    got: dict = {}

    class _H(BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            got["code"] = (q.get("code") or [""])[0]
            got["error"] = (q.get("error") or [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<h2>Spotify подключён — можно закрыть вкладку.</h2>".encode())

        def log_message(self, *a):
            pass

    try:
        srv = HTTPServer((red.hostname, red.port or 80), _H)
    except OSError as exc:
        return f"Порт {red.port} занят — вход в Spotify не запустить: {exc}"
    srv.timeout = 1
    import webbrowser
    webbrowser.open(a.get_auth_url())
    if player:
        player.write_log("SYS: 🎵 Войдите в Spotify в открывшейся вкладке")
    end = time.monotonic() + wait_sec
    while "code" not in got and time.monotonic() < end:
        srv.handle_request()
    srv.server_close()
    if got.get("code") and a.authenticate(got["code"]):
        global _premium
        _premium = None
        return "Spotify подключён, сэр." + ("" if is_premium() else " Но управлению нужен Premium.")
    return f"Spotify не подключился: {got.get('error') or 'нет ответа за отведённое время'}."


# ── запросы ───────────────────────────────────────────────────────────────────

def _req(method: str, path: str, **kw):
    import requests
    a = auth()
    token = a.get_access_token() if a else None
    if not token:
        raise PermissionError("Spotify не подключён")
    r = requests.request(method, API + path, headers={"Authorization": f"Bearer {token}"},
                         timeout=_TIMEOUT, **kw)
    if r.status_code == 401 and a.refresh_access_token():
        r = requests.request(method, API + path,
                             headers={"Authorization": f"Bearer {a.access_token}"},
                             timeout=_TIMEOUT, **kw)
    return r


def is_premium() -> bool:
    global _premium
    if _premium is None:
        try:
            _premium = _req("GET", "/me").json().get("product") == "premium"
        except Exception:
            return False
    return _premium


# ── поиск ─────────────────────────────────────────────────────────────────────

_KIND_WORDS = (("playlist", r"плейлист|playlist|подборк"), ("album", r"альбом|album"),
               ("artist", r"^(?:песни|музык[ау]|треки)\s|исполнител"))


def find(query: str) -> tuple[str, str] | None:
    """(uri, описание) лучшего совпадения. Исполнитель — если спросили его
    («включи Любэ»), трек — если название песни совпадает лучше."""
    from rapidfuzz import fuzz
    q = query.strip()
    forced = next((k for k, rx in _KIND_WORDS if re.search(rx, q, re.I)), None)
    clean = re.sub(r"\b(плейлист|playlist|альбом|album|песни|музык[ау]|треки)\b", "", q, flags=re.I).strip() or q
    r = _req("GET", "/search", params={"q": clean, "type": "track,artist,album,playlist",
                                        "limit": 5, "market": "from_token"})
    r.raise_for_status()
    data = r.json()

    def items(kind):
        return [i for i in (data.get(kind + "s", {}) or {}).get("items", []) if i]

    if forced in ("playlist", "album"):
        best = items(forced)[:1]
        if best:
            owner = best[0].get("owner", {}).get("display_name") or \
                ", ".join(a["name"] for a in best[0].get("artists", []))
            return best[0]["uri"], f"{'плейлист' if forced == 'playlist' else 'альбом'} «{best[0]['name']}»" + \
                (f" — {owner}" if owner else "")

    tracks = items("track")
    artists = items("artist")
    best_track, t_score = None, 0
    for t in tracks:
        who = ", ".join(a["name"] for a in t.get("artists", []))
        s = max(fuzz.token_set_ratio(clean.lower(), f"{t['name']} {who}".lower()),
                fuzz.ratio(clean.lower(), t["name"].lower()))
        if s > t_score:
            best_track, t_score = (t, who), s
    art = artists[0] if artists else None
    a_score = fuzz.ratio(clean.lower(), art["name"].lower()) if art else 0
    if art and (forced == "artist" or (a_score >= 88 and a_score >= t_score)):
        return art["uri"], f"«{art['name']}»"
    if best_track:
        t, who = best_track
        return t["uri"], f"«{t['name']}» — {who}"
    return None


# ── устройство и воспроизведение ──────────────────────────────────────────────

def _this_pc() -> str:
    import socket
    return (os.getenv("COMPUTERNAME") or socket.gethostname() or "").strip().lower()


def _pick_device(devs: list[dict]) -> dict | None:
    """Устройство ЭТОГО компьютера — приложение Spotify на нём.

    Раньше бралось активное: им часто оказывался телефон (или веб-плеер во
    вкладке браузера), музыка уходила туда, Spotify отвечал «играет», а на
    компьютере стояла тишина. Чужой телефон — никогда: лучше честно сказать,
    что Spotify на ПК не запустился."""
    pcs = [d for d in devs if d.get("type") == "Computer"
           and not str(d.get("name", "")).lower().startswith("web player")]
    me = _this_pc()
    mine = [d for d in pcs if me and str(d.get("name", "")).strip().lower() == me]
    return (mine or [d for d in pcs if d.get("is_active")] or pcs or [None])[0]


def _device() -> str | None:
    pick = _pick_device(_req("GET", "/me/player/devices").json().get("devices", []))
    return pick["id"] if pick else None


def _wake_device(timeout: float = 15.0) -> str | None:
    """Приложения Spotify на ПК в списке нет — запускаем его и ждём, пока
    оно зарегистрируется."""
    dev = _device()
    if dev:
        return dev
    try:
        os.startfile("spotify:")  # type: ignore[attr-defined]
    except Exception as exc:
        logger.debug("Запуск Spotify: %s", exc)
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        time.sleep(0.6)
        dev = _device()
        if dev:
            return dev
    return None


def play_uri(uri: str) -> tuple[bool, str]:
    dev = _wake_device()
    if not dev:
        return False, "Spotify не запустился на компьютере."
    body = {"uris": [uri]} if ":track:" in uri else {"context_uri": uri}
    r = _req("PUT", f"/me/player/play?device_id={dev}", json=body)
    if r.status_code == 403:
        return False, "Для управления Spotify нужен Premium."
    if r.status_code >= 300:
        return False, f"Spotify ответил {r.status_code}."
    return True, ""


def now_playing() -> dict | None:
    r = _req("GET", "/me/player/currently-playing")
    if r.status_code == 204 or not r.content:
        return None
    d = r.json()
    item = d.get("item") or {}
    return {"title": item.get("name", ""),
            "artist": ", ".join(a["name"] for a in item.get("artists", [])),
            "playing": bool(d.get("is_playing"))}


def _say(np: dict | None) -> str:
    if not np or not np.get("title"):
        return ""
    return f"«{np['title']}»" + (f" — {np['artist']}" if np.get("artist") else "")


def play(query: str) -> str | None:
    """Включить. None — Premium-путь недоступен, пусть работает запасной."""
    if not ready() or not is_premium():
        return None
    try:
        if not query:
            dev = _wake_device()
            if not dev:
                return "Spotify не запустился на компьютере."
            r = _req("PUT", f"/me/player/play?device_id={dev}")
            np = now_playing() if r.status_code < 300 else None
            return f"Играет {_say(np)}." if np and np["playing"] else "Продолжаю музыку."
        found = find(query)
        if not found:
            return f"Не нашёл «{query}» в Spotify, сэр."
        uri, desc = found
        ok, err = play_uri(uri)
        if not ok:
            return err
        for _ in range(8):                        # до ~2 с: что реально заиграло
            time.sleep(0.25)
            np = now_playing()
            if np and np["playing"]:
                return f"Включил {_say(np) if ':track:' in uri else desc}."
        # Команду приняли (204), а музыки нет: клиент Spotify из Microsoft
        # Store команды Connect подтверждает, но не исполняет. Молчать про это
        # нельзя, но и закрывать вопрос отпиской тоже — запасной путь открывает
        # spotify:track: прямо в приложении, и оно играет.
        logger.warning("Spotify принял команду, но воспроизведение не началось — отдаю запасному пути")
        return None
    except PermissionError:
        return None
    except Exception as exc:
        logger.warning("Spotify API: %s", exc)
        return None


def control(action: str, value=None) -> str | None:
    """pause/resume/next/previous/volume/now_playing/shuffle. None — не удалось через API."""
    if not ready() or not is_premium():
        return None
    try:
        if action == "now_playing":
            np = now_playing()
            if not np:
                return "Сейчас ничего не играет, сэр."
            return ("Играет " if np["playing"] else "На паузе: ") + _say(np) + "."
        if action in ("pause", "resume"):
            r = _req("PUT", "/me/player/" + ("pause" if action == "pause" else "play"))
            if r.status_code >= 300 and r.status_code != 403:
                return None
            return "Пауза." if action == "pause" else "Продолжаю."
        if action in ("next", "previous"):
            _req("POST", f"/me/player/{action}")
            time.sleep(0.4)
            np = now_playing()
            return ("Следующий: " if action == "next" else "Предыдущий: ") + (_say(np) or "трек") + "."
        if action in ("volume_up", "volume_down", "volume_set"):
            from actions.computer_settings import parse_level
            st = _req("GET", "/me/player").json() if action != "volume_set" else {}
            cur = (st.get("device") or {}).get("volume_percent", 50)
            step = parse_level(value, 10) or 10
            new = parse_level(value, 50) if action == "volume_set" else \
                (min(100, cur + step) if action == "volume_up" else max(0, cur - step))
            _req("PUT", f"/me/player/volume?volume_percent={new}")
            return f"Громкость Spotify {new}%."
        if action == "shuffle":
            on = str(value or "on").lower() not in ("off", "0", "false", "выкл")
            _req("PUT", f"/me/player/shuffle?state={'true' if on else 'false'}")
            return "Перемешиваю." if on else "Перемешивание выключено."
    except Exception as exc:
        logger.warning("Spotify API (%s): %s", action, exc)
    return None


def warm_up():
    """Токен и статус Premium — заранее, в фоне: первая команда не ждёт."""
    threading.Thread(target=lambda: ready() and is_premium(), daemon=True, name="spotify-warm").start()
