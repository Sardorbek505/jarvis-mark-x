"""Забота о перерывах: «Сэр, вы смотрите уже два часа — может, отдохнёте?»

Раз в 30 секунд Джарвис смотрит, чем занят человек:
  • видео — фильм или YouTube играет в окне Джарвиса (actions.video_player);
  • игра — впереди окно игры (известные .exe и папки Steam, Epic, Riot…).

Пока занятие идёт, копится непрерывная сессия. Перерыв дольше GAP_SEC
(встал, поел) начинает новую. На FIRST_MIN минутах сессии Джарвис говорит
одну фразу, дальше — каждые REPEAT_MIN. Для видео предлагает паузу (ответ
«да» ставит её через video_control), в игре только говорит и ничего не жмёт.

«Не напоминай сегодня» выключает до полуночи, «напоминай через час» меняет
порог. Настройки — в break_reminder.json в папке данных.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from dataclasses import dataclass
from datetime import date

logger = logging.getLogger(__name__)

FIRST_MIN = 120
REPEAT_MIN = 60
GAP_SEC = 10 * 60
TICK_SEC = 30

# Игры, которые узнаём по имени процесса, где бы они ни стояли.
GAME_EXES = {
    "cs2.exe": "Counter-Strike 2", "csgo.exe": "CS:GO", "dota2.exe": "Dota 2",
    "valorant-win64-shipping.exe": "Valorant", "r5apex.exe": "Apex Legends",
    "fortniteclient-win64-shipping.exe": "Fortnite", "gta5.exe": "GTA V",
    "gta5_enhanced.exe": "GTA V", "tslgame.exe": "PUBG", "rustclient.exe": "Rust",
    "leagueclient.exe": "League of Legends", "league of legends.exe": "League of Legends",
    "overwatch.exe": "Overwatch", "cod.exe": "Call of Duty", "rainbowsix.exe": "Rainbow Six",
    "eldenring.exe": "Elden Ring", "cyberpunk2077.exe": "Cyberpunk 2077",
    "witcher3.exe": "Ведьмак 3", "rdr2.exe": "Red Dead Redemption 2",
    "minecraft.exe": "Minecraft", "robloxplayerbeta.exe": "Roblox",
    "fc25.exe": "EA FC", "fc26.exe": "EA FC", "warthunder.exe": "War Thunder",
    "worldoftanks.exe": "Мир танков", "wotblitz.exe": "Tanks Blitz", "deadlock.exe": "Deadlock",
}
# Всё, что запущено из этих папок, — игра.
GAME_DIRS = ("\\steamapps\\common\\", "\\epic games\\", "\\riot games\\", "\\xboxgames\\",
             "\\ubisoft game launcher\\games\\", "\\ea games\\", "\\gog galaxy\\games\\",
             "\\battle.net\\", "\\games\\")
# Лаунчеры и их помощники — не игра, даже если лежат в тех же папках.
NOT_GAMES = {"steam.exe", "steamwebhelper.exe", "epicgameslauncher.exe", "riotclientservices.exe",
             "riotclientux.exe", "battle.net.exe", "eadesktop.exe", "galaxyclient.exe",
             "upc.exe", "ubisoftconnect.exe", "crashreporter.exe", "unitycrashhandler64.exe"}


@dataclass
class Activity:
    kind: str          # "video" | "game"
    name: str          # «Counter-Strike 2», заголовок видео


def game_name(exe_path: str) -> str | None:
    """Название игры по пути к .exe или None, если это не игра."""
    if not exe_path:
        return None
    p = exe_path.replace("/", "\\").lower()
    exe = p.rsplit("\\", 1)[-1]
    if exe in NOT_GAMES:
        return None
    if exe in GAME_EXES:
        return GAME_EXES[exe]
    for d in GAME_DIRS:
        if d in p:
            folder = p.split(d, 1)[1].split("\\", 1)[0]
            orig = exe_path.replace("/", "\\")
            start = orig.lower().find(folder)
            return orig[start:start + len(folder)] if start >= 0 and folder else exe[:-4]
    return None


def _foreground_exe() -> str:
    if sys.platform != "win32":
        return ""
    try:
        from core import win_apps
        w = win_apps.foreground()
        if not w:
            return ""
        import psutil
        return psutil.Process(w.pid).exe()
    except Exception:
        return ""


def _clean_title(title: str) -> str:
    """«Железный человек 2 — VK Видео» → «Железный человек 2»."""
    for tail in (" - YouTube", " — VK Видео", " | VK Видео", " - VK Видео", " — смотреть видео онлайн"):
        title = title.split(tail)[0]
    return title.strip()


def current_activity() -> Activity | None:
    """Чем человек занят прямо сейчас (видео важнее: игра может стоять фоном)."""
    try:
        from core import browser_cdp as cdp
        if cdp.running():
            st = cdp.video_state()
            if st and not st.get("paused", True):
                title = ""
                try:
                    title = cdp.tab(create=False).eval("document.title") or ""
                except Exception:
                    pass
                return Activity("video", _clean_title(title))
    except Exception as exc:
        logger.debug("Видео: %s", exc)
    game = game_name(_foreground_exe())
    return Activity("game", game) if game else None


def say_duration(minutes: int) -> str:
    h, m = divmod(int(minutes), 60)
    def plural(n, one, few, many):
        n10, n100 = n % 10, n % 100
        return one if n10 == 1 and n100 != 11 else few if 2 <= n10 <= 4 and not 12 <= n100 <= 14 else many
    parts = []
    if h:
        parts.append(f"{h} {plural(h, 'час', 'часа', 'часов')}")
    if m >= 5 or not h:
        parts.append(f"{m} {plural(m, 'минуту', 'минуты', 'минут')}")
    return " ".join(parts)


def prompt_for(act: Activity, minutes: int) -> str:
    dur = say_duration(minutes)
    if act.kind == "video":
        return ("[СИСТЕМА: забота о перерыве. Пользователь смотрит видео"
                + (f" «{act.name}»" if act.name else "") + f" без перерыва уже {dur}. "
                f"Скажи одной-двумя короткими фразами, тепло и в своём стиле, например: "
                f"«Сэр, вы смотрите уже {dur}. Может, сделаете перерыв? Поставить на паузу?» "
                "Сам на вопрос не отвечай. Ответит «да» — вызови video_control с action=\"pause\"; "
                "«нет» — коротко согласись и больше не спрашивай. "
                "«Не напоминай сегодня» — break_reminder с action=\"off_today\".]")
    return (f"[СИСТЕМА: забота о перерыве. Пользователь играет в {act.name} без перерыва уже {dur}. "
            f"Скажи ОДНОЙ короткой фразой, не отвлекая от игры, например: "
            f"«Сэр, вы играете уже {dur} — может, перерыв после этого раунда?» "
            "Ничего не нажимай и игру не трогай. «Не напоминай сегодня» — "
            "break_reminder с action=\"off_today\".]")


class BreakReminder:
    """Считает непрерывные сессии и зовёт say(prompt) на порогах."""

    def __init__(self, say, sense=current_activity, clock=time.time, settings_path=None):
        self._say = say
        self._sense = sense
        self._clock = clock
        self._path = settings_path
        self.first_min = FIRST_MIN
        self.repeat_min = REPEAT_MIN
        self.off_until: str = ""          # дата ISO: выключено по этот день включительно
        self.enabled = True
        self._start: float | None = None   # начало текущей сессии
        self._last_seen: float | None = None
        self._next_at: float | None = None
        self._stop = threading.Event()
        self._load()

    # ── настройки ──────────────────────────────────────────────────────────
    def _load(self):
        if not self._path or not os.path.isfile(self._path):
            return
        try:
            import json
            with open(self._path, encoding="utf-8") as f:
                d = json.load(f)
            self.first_min = int(d.get("first_min", FIRST_MIN))
            self.repeat_min = int(d.get("repeat_min", REPEAT_MIN))
            self.off_until = str(d.get("off_until", ""))
            self.enabled = bool(d.get("enabled", True))
        except Exception as exc:
            logger.warning("Настройки перерывов: %s", exc)

    def _save(self):
        if not self._path:
            return
        try:
            from pathlib import Path

            from core.storage import atomic_write_json
            atomic_write_json(Path(self._path), {"first_min": self.first_min, "repeat_min": self.repeat_min,
                                                 "off_until": self.off_until, "enabled": self.enabled})
        except Exception as exc:
            logger.warning("Настройки перерывов не сохранились: %s", exc)

    def _today(self) -> str:
        return date.fromtimestamp(self._clock()).isoformat()

    def active_now(self) -> bool:
        return self.enabled and not (self.off_until and self._today() <= self.off_until)

    # ── такт ───────────────────────────────────────────────────────────────
    def tick(self) -> str | None:
        """Один замер. Возвращает сказанное указание (для тестов и лога)."""
        now = self._clock()
        act = self._sense()
        if not act:
            if self._last_seen is not None and now - self._last_seen > GAP_SEC:
                self._start = self._last_seen = self._next_at = None
            return None
        if self._last_seen is None or now - self._last_seen > GAP_SEC:
            self._start = now
            self._next_at = now + self.first_min * 60
        self._last_seen = now
        if self._next_at is not None and now >= self._next_at:
            self._next_at = now + self.repeat_min * 60
            if self.active_now():
                prompt = prompt_for(act, round((now - self._start) / 60))
                logger.info("Перерыв: %s %s, %.0f мин", act.kind, act.name, (now - self._start) / 60)
                try:
                    self._say(prompt)
                except Exception as exc:
                    logger.warning("Не смог напомнить о перерыве: %s", exc)
                return prompt
        return None

    def session_minutes(self) -> int:
        return 0 if self._start is None else round((self._clock() - self._start) / 60)

    # ── команды голосом ────────────────────────────────────────────────────
    def command(self, action: str, first_min=None, repeat_min=None) -> str:
        action = (action or "status").lower()
        if action == "off_today":
            self.off_until = self._today()
            self._save()
            return "Хорошо, сэр, сегодня о перерывах не напоминаю."
        if action == "off":
            self.enabled = False
            self._save()
            return "Напоминания о перерывах выключены."
        if action == "on":
            self.enabled, self.off_until = True, ""
            self._save()
            return (f"Напоминания включены: через {say_duration(self.first_min)}, "
                    f"потом каждые {say_duration(self.repeat_min)}.")
        if action == "set":
            if first_min:
                self.first_min = max(10, int(float(first_min)))
            if repeat_min:
                self.repeat_min = max(10, int(float(repeat_min)))
            self.enabled, self.off_until = True, ""
            if self._start is not None:
                self._next_at = self._start + self.first_min * 60
                if self._next_at <= self._clock():
                    self._next_at = self._clock() + 60
            self._save()
            return (f"Буду напоминать через {say_duration(self.first_min)}, "
                    f"потом каждые {say_duration(self.repeat_min)}.")
        state = "выключены" if not self.enabled else \
            "выключены до завтра" if not self.active_now() else \
            f"включены: через {say_duration(self.first_min)}, потом каждые {say_duration(self.repeat_min)}"
        mins = self.session_minutes()
        return f"Напоминания о перерывах {state}." + (f" Текущая сессия — {say_duration(mins)}." if mins else "")

    # ── фон ────────────────────────────────────────────────────────────────
    def start(self):
        def run():
            while not self._stop.wait(TICK_SEC):
                try:
                    self.tick()
                except Exception as exc:
                    logger.debug("Перерыв, такт: %s", exc)
        threading.Thread(target=run, daemon=True, name="break-reminder").start()
        logger.info("Напоминания о перерывах: %s мин, потом каждые %s", self.first_min, self.repeat_min)

    def stop(self):
        self._stop.set()


_instance: BreakReminder | None = None


def get(say=None) -> BreakReminder:
    """Общий экземпляр (Джарвис создаёт его со своей speak)."""
    global _instance
    if _instance is None:
        try:
            from core.paths import get_data_root
            path = os.path.join(str(get_data_root()), "break_reminder.json")
        except Exception:
            path = None
        _instance = BreakReminder(say or (lambda p: None), settings_path=path)
    elif say is not None:
        _instance._say = say
    return _instance


def break_reminder(parameters: dict, player=None) -> str:
    p = parameters or {}
    return get().command(p.get("action", "status"), p.get("first_minutes"), p.get("repeat_minutes"))
