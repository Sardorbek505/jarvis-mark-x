"""Свои команды: фраза → цепочка действий.

«Режим стрима»: открыть OBS, подождать 2 секунды, нажать Ctrl+Shift+S,
громкость 35. Команду можно собрать голосом («Джарвис, создай команду…»),
описанием в окне «Свои команды» (ИИ разложит на шаги) или вручную, и
поставить готовые паки для программ (core/macro_packs.py).

- Фразы узнаются целиком, мгновенно и без облака (через core/quick.py);
  {слот} во фразе ловит слова: «вкладка {номер}», «найди на странице {текст}».
- Команда с app работает, только когда эта программа впереди: «новая
  вкладка» — в браузере, а «закрой чат» — в Telegram.
- confirm — сначала переспросить (для всего, что жалко сделать случайно).
- Шаги идут в фоне; шаг не вышел — Джарвис говорит, какой и почему.

Хранится в macros.json в папке данных.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

# Что умеет шаг. value — главное значение шага (строка).
STEP_TYPES = {
    "open_app": "Запустить программу",      # value: имя («OBS Studio», «телеграм»)
    "open_url": "Открыть сайт",             # value: адрес
    "keys": "Нажать клавиши",               # value: «ctrl+shift+s», «f5», «ctrl+k s» (по очереди)
    "type": "Набрать текст",                # value: текст (русский тоже)
    "click": "Клик мышью",                  # x, y (value: left/right/double)
    "wait": "Пауза",                        # value: секунды
    "volume": "Громкость системы",          # value: 0..100
    "media": "Медиаклавиша",                # value: playpause/next/previous/stop/mute
    "say": "Сказать",                       # value: текст
    "tool": "Инструмент Джарвиса",          # tool + args (music_player, clock, …)
}
MAX_WAIT_SEC = 60.0
_SLOT = re.compile(r"\{([^{}]+)\}")


@dataclass
class Command:
    name: str
    phrases: list[str]
    steps: list[dict]
    app: str = ""                    # «chrome.exe|msedge.exe» — только когда она впереди
    confirm: bool = False
    enabled: bool = True
    pack: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])

    @classmethod
    def from_dict(cls, d: dict) -> "Command":
        return cls(name=str(d.get("name") or "Без названия").strip(),
                   phrases=[str(p).strip() for p in d.get("phrases") or [] if str(p).strip()],
                   steps=clean_steps(d.get("steps") or []),
                   app=str(d.get("app") or "").strip(), confirm=bool(d.get("confirm")),
                   enabled=d.get("enabled", True) is not False, pack=str(d.get("pack") or ""),
                   id=str(d.get("id") or uuid.uuid4().hex[:10]))


def clean_steps(steps) -> list[dict]:
    """Шаги из любого источника (ИИ, окно, JSON) — в один вид; мусор — прочь."""
    out = []
    for s in steps or []:
        if not isinstance(s, dict):
            continue
        do = str(s.get("do") or s.get("type") or "").strip().lower()
        if do not in STEP_TYPES:
            continue
        step = {"do": do, "value": str(s.get("value") if s.get("value") is not None else "").strip()}
        if do == "click":
            step["x"], step["y"] = int(float(s.get("x") or 0)), int(float(s.get("y") or 0))
        if do == "tool":
            step["tool"] = str(s.get("tool") or "").strip()
            args = s.get("args")
            if isinstance(args, str):
                try:
                    args = json.loads(args or "{}")
                except ValueError:
                    args = {}
            step["args"] = args if isinstance(args, dict) else {}
            if not step["tool"]:
                continue
        out.append(step)
    return out


def describe_step(s: dict) -> str:
    v = s.get("value", "")
    return {"open_app": f"запустить {v}", "open_url": f"открыть {v}", "keys": f"нажать {v}",
            "type": f"набрать «{v}»", "click": f"клик {s.get('x')},{s.get('y')}", "wait": f"пауза {v} с",
            "volume": f"громкость {v}", "media": f"медиаклавиша {v}", "say": f"сказать «{v}»",
            "tool": f"{s.get('tool')} {s.get('args')}"}.get(s["do"], s["do"])


# ── фразы ─────────────────────────────────────────────────────────────────────

def _norm(text: str) -> str:
    from core.quick import normalize
    return normalize(text)


def phrase_regex(phrase: str) -> tuple[re.Pattern, list[str]]:
    """«вкладка {номер}» → ^вкладка (?P<s0>.+)$ (буквы — как после normalize)."""
    parts, names, pos = [], [], 0
    for m in _SLOT.finditer(phrase):
        lit = _norm(phrase[pos:m.start()])
        if lit:
            parts.append(re.escape(lit))
        parts.append(f"(?P<s{len(names)}>.+?)")
        names.append(m.group(1).strip())
        pos = m.end()
    tail = _norm(phrase[pos:])
    if tail:
        parts.append(re.escape(tail))
    return re.compile(r"\s*".join(parts) if parts else r"(?!)"), names


def _num(text: str) -> str:
    """«три» → «3» (для клавиш вида ctrl+{номер})."""
    t = text.strip()
    if t.isdigit():
        return t
    try:
        from actions.computer_settings import _words_number
        n = _words_number(t)
        return str(n) if n is not None else t
    except Exception:
        return t


def fill(value: str, slots: dict) -> str:
    return _SLOT.sub(lambda m: slots.get(m.group(1).strip(), m.group(0)), value or "")


# ── исполнители шагов (Windows) ───────────────────────────────────────────────

_VK = {"ctrl": 0x11, "control": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B, "enter": 0x0D, "return": 0x0D,
       "tab": 0x09, "escape": 0x1B, "esc": 0x1B, "space": 0x20, "backspace": 0x08, "delete": 0x2E, "del": 0x2E,
       "insert": 0x2D, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22, "left": 0x25, "up": 0x26,
       "right": 0x27, "down": 0x28, "plus": 0xBB, "=": 0xBB, "minus": 0xBD, "-": 0xBD, ".": 0xBE, ",": 0xBC,
       "/": 0xBF, "`": 0xC0, ";": 0xBA, "'": 0xDE, "[": 0xDB, "]": 0xDD, "\\": 0xDC, "printscreen": 0x2C,
       "playpause": 0xB3, "next": 0xB0, "previous": 0xB1, "prev": 0xB1, "stop": 0xB2, "mute": 0xAD,
       "volumeup": 0xAF, "volumedown": 0xAE, "capslock": 0x14}
_EXTENDED = {0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x24, 0x23, 0x21, 0x22, 0x5B, 0xB3, 0xB0, 0xB1, 0xB2,
             0xAD, 0xAF, 0xAE}


def vk_code(key: str) -> int | None:
    k = key.strip().lower()
    if k in _VK:
        return _VK[k]
    if re.fullmatch(r"f([1-9]|1[0-9]|2[0-4])", k):
        return 0x70 + int(k[1:]) - 1
    if len(k) == 1 and ("a" <= k <= "z" or "0" <= k <= "9"):
        return ord(k.upper())
    return None


def parse_keys(combo: str) -> list[list[int]]:
    """«ctrl+k s» → [[ctrl, k], [s]]. Неизвестная клавиша — ValueError."""
    chords = []
    for chord in (combo or "").strip().split():
        keys = []
        for part in re.split(r"(?<!^)\+(?!$)", chord):
            code = vk_code(part)
            if code is None:
                raise ValueError(f"не знаю клавишу «{part}»")
            keys.append(code)
        if keys:
            chords.append(keys)
    if not chords:
        raise ValueError("клавиши не указаны")
    return chords


def press_keys(combo: str) -> None:
    import ctypes
    user32 = ctypes.windll.user32
    for chord in parse_keys(combo):
        for vk in chord:
            user32.keybd_event(vk, 0, 1 if vk in _EXTENDED else 0, 0)
        for vk in reversed(chord):
            user32.keybd_event(vk, 0, (1 if vk in _EXTENDED else 0) | 2, 0)
        time.sleep(0.05)


def type_text(text: str) -> None:
    """Печать любого текста (и кириллицы) — юникодными нажатиями, без буфера обмена."""
    import ctypes
    from ctypes import wintypes

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

    class INPUT(ctypes.Structure):
        class _U(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _U)]

    send = ctypes.windll.user32.SendInput
    data = text.encode("utf-16-le")
    for i in range(0, len(data), 2):
        code = int.from_bytes(data[i:i + 2], "little")
        for flags in (0x0004, 0x0004 | 0x0002):              # KEYEVENTF_UNICODE, + KEYUP
            inp = INPUT(type=1, ki=KEYBDINPUT(0, code, flags, 0, None))
            send(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
        time.sleep(0.005)


def click(x: int, y: int, how: str = "left") -> None:
    import ctypes
    u = ctypes.windll.user32
    u.SetCursorPos(int(x), int(y))
    down, up = (0x0008, 0x0010) if how == "right" else (0x0002, 0x0004)
    for _ in range(2 if how == "double" else 1):
        u.mouse_event(down, 0, 0, 0, 0)
        u.mouse_event(up, 0, 0, 0, 0)
        time.sleep(0.05)


def _foreground_exe() -> str:
    try:
        from core import win_apps
        w = win_apps.foreground()
        return (w.exe if w else "").lower()
    except Exception:
        return ""


# ── хранилище и запуск ────────────────────────────────────────────────────────

class Macros:
    def __init__(self, path: Path, foreground: Callable[[], str] | None = None):
        self.path = Path(path)
        self.foreground = foreground or _foreground_exe
        self.say: Callable[[str], None] = lambda text: None      # system-реплика Джарвису
        self.log: Callable[[str], None] = lambda text: None
        self.run_tool: Callable[[str, dict], str] = lambda name, args: "инструменты недоступны"
        self.do: dict[str, Callable[[dict], str | None]] = {
            "open_app": self._open_app, "open_url": self._open_url,
            "keys": lambda s: press_keys(s["value"]), "type": lambda s: type_text(s["value"]),
            "click": lambda s: click(s.get("x", 0), s.get("y", 0), s.get("value") or "left"),
            "wait": lambda s: time.sleep(min(MAX_WAIT_SEC, max(0.0, float(s["value"] or 1)))),
            "volume": self._volume, "media": lambda s: press_keys(s["value"] or "playpause"),
            "say": lambda s: self.say(f"[СИСТЕМА: произнеси пользователю, своими словами не дополняй: "
                                      f"«{s['value']}»]"),
            "tool": lambda s: self.run_tool(s["tool"], s.get("args") or {}),
        }
        self._lock = threading.RLock()
        self.commands: list[Command] = []
        self._rx: list[tuple[Command, list[tuple[re.Pattern, list[str]]]]] = []
        self.load()

    # ── шаги, которым нужен остальной Джарвис ──
    @staticmethod
    def _open_app(s: dict) -> str:
        from actions.open_app import open_app
        res = open_app(parameters={"app_name": s["value"]}) or ""
        if _failed(res):
            raise RuntimeError(res)
        return res

    @staticmethod
    def _open_url(s: dict) -> str:
        from actions.browser_control import browser_control
        return browser_control(parameters={"action": "go_to", "url": s["value"]}) or ""

    @staticmethod
    def _volume(s: dict) -> str:
        from actions.computer_settings import computer_settings
        return computer_settings(parameters={"action": "volume_set", "value": s["value"]}) or ""

    # ── файл ──
    def load(self):
        with self._lock:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self.commands = [Command.from_dict(d) for d in data.get("commands", [])]
                self.installed = set(data.get("packs") or [])
            except FileNotFoundError:
                self.commands, self.installed = [], set()
                self._install_defaults()
            except Exception as exc:
                logger.warning("Свои команды не прочитались (%s) — начинаю с пустых", exc)
                self.commands, self.installed = [], set()
            self._compile()

    def save(self):
        with self._lock:
            self._compile()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"version": 1, "packs": sorted(self.installed),
                                       "commands": [asdict(c) for c in self.commands]},
                                      ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)

    def _compile(self):
        self._rx = [(c, [phrase_regex(p) for p in c.phrases]) for c in self.commands if c.enabled]

    def _install_defaults(self):
        from core.macro_packs import PACKS
        for key, pack in PACKS.items():
            if pack.get("default"):
                self._add_pack(key)
        if self.commands:
            try:
                self.save()
            except OSError as exc:
                logger.debug("macros.json не записан: %s", exc)

    # ── команды ──
    def find(self, name: str) -> Command | None:
        n = _norm(name)
        if not n:
            return None
        exact = [c for c in self.commands if _norm(c.name) == n or c.id == name]
        if exact:
            return exact[0]
        loose = [c for c in self.commands if n in _norm(c.name) or any(n == _norm(p) for p in c.phrases)]
        return loose[0] if len(loose) == 1 else None

    def upsert(self, cmd: Command) -> Command:
        with self._lock:
            self.commands = [c for c in self.commands if c.id != cmd.id and _norm(c.name) != _norm(cmd.name)]
            self.commands.append(cmd)
            self.save()
        return cmd

    def delete(self, name: str) -> bool:
        c = self.find(name)
        if not c:
            return False
        with self._lock:
            self.commands = [x for x in self.commands if x.id != c.id]
            self.save()
        return True

    def needs_confirm(self, name: str) -> bool:
        c = self.find(name)
        return bool(c and c.confirm)

    # ── паки ──
    def _add_pack(self, key: str) -> int:
        from core.macro_packs import PACKS
        pack = PACKS[key]
        self.commands = [c for c in self.commands if c.pack != key]
        for d in pack["commands"]:
            c = Command.from_dict({**d, "pack": key, "app": d.get("app") or pack.get("app", "")})
            self.commands.append(c)
        self.installed.add(key)
        return len(pack["commands"])

    def install_pack(self, key: str) -> str:
        from core.macro_packs import PACKS
        key = self._pack_key(key)
        if not key:
            return "Нет такого пака. Есть: " + ", ".join(p["title"] for p in PACKS.values()) + "."
        with self._lock:
            n = self._add_pack(key)
            self.save()
        return f"Пак «{PACKS[key]['title']}» установлен: {n} команд."

    def remove_pack(self, key: str) -> str:
        from core.macro_packs import PACKS
        key = self._pack_key(key)
        if not key or key not in self.installed:
            return "Такой пак не установлен."
        with self._lock:
            self.commands = [c for c in self.commands if c.pack != key]
            self.installed.discard(key)
            self.save()
        return f"Пак «{PACKS[key]['title']}» удалён."

    @staticmethod
    def _pack_key(name: str) -> str | None:
        from core.macro_packs import PACKS
        n = _norm(name)
        for key, p in PACKS.items():
            if n in (key, _norm(p["title"])) or (n and (n in _norm(p["title"]) or _norm(p["title"]) in n)):
                return key
        return None

    def packs_text(self) -> str:
        from core.macro_packs import PACKS
        rows = [f"{'✓' if k in self.installed else '·'} {p['title']} ({len(p['commands'])}) — {p['about']}"
                for k, p in PACKS.items()]
        return "Паки команд:\n" + "\n".join(rows)

    def list_text(self) -> str:
        own = [c for c in self.commands if not c.pack]
        packs = sorted({c.pack for c in self.commands if c.pack})
        lines = [f"«{c.name}» — скажите «{c.phrases[0] if c.phrases else '?'}»: "
                 + ", ".join(describe_step(s) for s in c.steps[:4]) for c in own]
        head = f"Своих команд: {len(own)}." + (f" Паки: {', '.join(packs)}." if packs else "")
        return head + ("\n" + "\n".join(lines) if lines else "")

    # ── узнать фразу ──
    def match(self, text: str) -> tuple[Command, dict] | None:
        """Фраза целиком → (команда, слоты). Команды своей программы — первыми,
        потом свои общие, потом паки."""
        t = _norm(text)
        if not t:
            return None
        fg = None
        found = []
        for cmd, rxs in self._rx:
            for rx, names in rxs:
                m = rx.fullmatch(t)
                if not m:
                    continue
                if cmd.app:
                    fg = self.foreground() if fg is None else fg
                    if not any(a.strip().lower() in fg for a in re.split(r"[|,]", cmd.app) if a.strip()):
                        continue
                slots = {name: (m.group(f"s{i}") or "").strip() for i, name in enumerate(names)}
                rank = (0 if cmd.app else 1, 1 if cmd.pack else 0, -len(rx.pattern))
                found.append((rank, cmd, slots))
                break
        if not found:
            return None
        found.sort(key=lambda f: f[0])
        return found[0][1], found[0][2]

    # ── запуск ──
    def run(self, cmd: Command, slots: dict | None = None, wait: bool = False) -> str:
        """Выполнить шаги в фоне. Ответ сразу; неудача — вслух через say."""
        slots = slots or {}
        steps = []
        for s in cmd.steps:
            # В клавишах слот — число («вкладка три» → ctrl+3).
            vals = {k: _num(v) for k, v in slots.items()} if s["do"] == "keys" else slots
            st = dict(s, value=fill(s.get("value", ""), vals))
            if st["do"] == "tool":
                st["args"] = {k: fill(v, slots) if isinstance(v, str) else v for k, v in (s.get("args") or {}).items()}
            steps.append(st)

        def go():
            started = time.monotonic()
            for i, st in enumerate(steps, 1):
                try:
                    res = self.do[st["do"]](st)
                    if st["do"] == "tool" and isinstance(res, str) and _failed(res):
                        raise RuntimeError(res)
                except Exception as exc:
                    logger.warning("Команда «%s», шаг %d (%s): %s", cmd.name, i, describe_step(st), exc)
                    self.log(f"SYS: ⚠ «{cmd.name}»: шаг {i} ({describe_step(st)}) — {exc}")
                    self.say(f"[СИСТЕМА: своя команда «{cmd.name}» остановилась на шаге {i} "
                             f"({describe_step(st)}): {exc}. Скажи пользователю одной фразой.]")
                    return False
            logger.info("Команда «%s»: %d шагов за %.1f с", cmd.name, len(steps), time.monotonic() - started)
            self.log(f"SYS: ✓ «{cmd.name}» — выполнено")
            return True

        if wait:
            return "Готово." if go() else f"«{cmd.name}» не выполнилась до конца."
        threading.Thread(target=go, daemon=True, name="macro").start()
        return f"Выполняю «{cmd.name}»."


def _failed(text: str) -> bool:
    from core.quick import failed
    return failed(text)


# ── голосом: создать словами ─────────────────────────────────────────────────

AI_MODEL = "gemini-2.5-flash"
AI_PROMPT = """Ты собираешь команду для голосового ассистента Windows из описания пользователя.
Верни ТОЛЬКО JSON: {"name": "короткое название", "phrases": ["фраза запуска", "ещё вариант"],
"app": "" , "steps": [шаги]}.
Шаг — {"do": тип, "value": строка}. Типы:
open_app (value: программа как её зовут люди: «OBS Studio», «Telegram»), open_url (value: адрес),
keys (value: «ctrl+shift+s», «f5», «alt+tab»; несколько нажатий подряд через пробел),
type (value: текст), click (x, y числа; value: left/right/double), wait (value: секунды),
volume (value: 0-100, громкость системы), media (value: playpause/next/previous/mute),
say (value: что сказать), tool (tool: имя, args: объект) — инструменты: music_player
(action=play, query=…; action=pause/next), clock (action=timer_set, minutes=…), weather,
window_control (action=minimize_all/show_desktop), computer_control (action=volume_set, value=…).
Музыку включай через tool music_player, а не медиаклавишами. Фразы — как человек скажет вслух,
по-русски, без имени ассистента. {слово} во фразе — переменная часть, её можно подставить в value.
app — только если команда имеет смысл лишь в одной программе («chrome.exe»).
Описание: """


def parse_ai(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.S)
    d = json.loads(m.group(0) if m else text)
    cmd = Command.from_dict(d)
    if not cmd.steps:
        raise ValueError("ИИ не собрал ни одного шага")
    if not cmd.phrases:
        cmd.phrases = [cmd.name.lower()]
    return asdict(cmd)


def build_with_ai(description: str) -> dict:
    """Описание словами → команда (dict). Отдельный запрос к Gemini."""
    from google.genai import types

    from actions import vision
    key = vision._get_api_key()
    if not key:
        raise RuntimeError("нет ключа Gemini")
    resp = vision._client(key).models.generate_content(
        model=AI_MODEL, contents=[AI_PROMPT + description.strip()],
        config=types.GenerateContentConfig(response_mime_type="application/json"))
    return parse_ai(resp.text or "")


# ── один на процесс ──────────────────────────────────────────────────────────

_macros: Macros | None = None


def macros() -> Macros:
    global _macros
    if _macros is None:
        env = os.getenv("JARVIS_MACROS", "").strip()
        if env:
            path = Path(env)
        else:
            try:
                from core.paths import get_data_root
                path = Path(get_data_root()) / "macros.json"
            except Exception:
                path = Path(__file__).resolve().parent.parent / "macros.json"
        _macros = Macros(path)
    return _macros


def macro_tool(p: dict) -> str:
    """Инструмент macro для Gemini."""
    p = p or {}
    a = str(p.get("action") or "list").strip().lower()
    m = macros()
    if a == "run":
        if p.get("phrase"):                       # мгновенный путь: как сказали, со слотами
            hit = m.match(str(p["phrase"]))
            if hit:
                return m.run(*hit)
        c = m.find(str(p.get("name") or ""))
        if not c:
            hit = m.match(str(p.get("name") or ""))
            if not hit:
                return f"Нет своей команды «{p.get('name')}». " + m.list_text()
            c, slots = hit
            return m.run(c, slots)
        return m.run(c, {})
    if a == "create":
        cmd = Command.from_dict({"name": p.get("name"), "phrases": p.get("phrases") or [],
                                 "steps": p.get("steps") or [], "app": p.get("app") or "",
                                 "confirm": bool(p.get("confirm"))})
        if not cmd.steps:
            return "Не понял шаги команды — перечисли, что именно делать."
        if not cmd.phrases:
            cmd.phrases = [cmd.name.lower()]
        m.upsert(cmd)
        return (f"Команда «{cmd.name}» сохранена: скажите «{cmd.phrases[0]}». Шаги: "
                + "; ".join(describe_step(s) for s in cmd.steps) + ".")
    if a == "delete":
        return "Удалил." if m.delete(str(p.get("name") or "")) else "Такой команды нет."
    if a == "show":
        c = m.find(str(p.get("name") or ""))
        if not c:
            return "Такой команды нет."
        return (f"«{c.name}»: фразы — " + ", ".join(f"«{x}»" for x in c.phrases) + ". Шаги: "
                + "; ".join(describe_step(s) for s in c.steps) + (f". Только в {c.app}." if c.app else "."))
    if a == "packs":
        return m.packs_text()
    if a == "install_pack":
        return m.install_pack(str(p.get("name") or ""))
    if a == "remove_pack":
        return m.remove_pack(str(p.get("name") or ""))
    if a == "list":
        return m.list_text()
    return f"Не понял действие «{a}»."
