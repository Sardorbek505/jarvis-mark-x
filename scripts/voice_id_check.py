"""Узнавание голоса на настоящем звуке (CI, job wake-word).

«Владелец» — голос Edge ru-RU-DmitryNeural: записывает 5 фраз, как в окне
«Обо мне». Потом его же другие фразы (и чуть быстрее — как живой человек
говорит по-разному) должны узнаваться, а чужие голоса — нет. Модели — те же,
что в Джарвисе: WeSpeaker (sherpa-onnx) — основная, и для сравнения Vosk
(vosk-small-ru + vosk-model-spk).

Синтез — нижняя граница трудности: живые голоса похожи сильнее. Порог
подбирается по записям владельца (VoiceID.enroll), здесь проверяем, что
при нём свои проходят, а чужие — нет, и печатаем сходство для подстройки.

Запуск: python scripts/voice_id_check.py models/vosk-small-ru models/vosk-spk \
        models/voice-id/wespeaker_en_voxceleb_resnet34.onnx
"""
import importlib.util
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


V = _load("voice_id", "core/voice_id.py")
W = _load("wake_check_vosk", "scripts/wake_check_vosk.py")          # _say: Edge → PCM 16 кГц

OWNER = "ru-RU-DmitryNeural"
# Чужие — прежде всего мужские: женский голос отсекается легко. Многоязычные
# голоса Edge говорят по-русски; обычные иностранные на русском тексте молчат.
STRANGERS = ["uk-UA-OstapNeural", "kk-KZ-DauletNeural", "en-US-AndrewMultilingualNeural",
             "en-US-BrianMultilingualNeural", "de-DE-FlorianMultilingualNeural",
             "fr-FR-RemyMultilingualNeural", "ru-RU-SvetlanaNeural"]
# Как в жизни: просьба, потом «да» — Джарвис сверяет речь за последние 30 с.
TESTS = [("Джарвис, напиши маме, что я задержусь", "Да, отправляй"),
         ("Позвони брату и скажи, что ужин готов", "Да"),
         ("Выключи компьютер", "Да, выключай"),
         ("Удали папку загрузки", "Да, точно")]


def synth(tmp: str):
    """Звук один раз на все модели: запись владельца и пары «просьба + да»."""
    enroll = [W._say(p, OWNER, tmp) for p in V.ENROLL_PHRASES]
    cases = []
    for voice, rate, own in [(OWNER, "+0%", True), (OWNER, "+15%", True)] + [(v, "+0%", False) for v in STRANGERS]:
        try:
            pcms = [W._say(ask, voice, tmp, rate) + W._say(yes, voice, tmp, rate) for ask, yes in TESTS]
        except Exception as exc:          # Edge не отдал звук этим голосом — не наша проверка
            print(f"--- [{voice}] синтез не удался ({type(exc).__name__}) — пропускаю голос")
            continue
        cases += [(voice, rate, own, t, pcm) for t, pcm in zip(TESTS, pcms)]
    return enroll, cases


def check(embed, enroll, cases, tmp: str) -> tuple[int, int, int, int]:
    vid = V.VoiceID(Path(tmp) / f"voice_id_{embed.name}.json", embed=embed)
    res = vid.enroll(enroll)
    print(f"\n=== {embed.name}: {res['text']} порог {vid.threshold:.3f}, сходство своих "
          f"{vid.profile.get('self_sim')} (мин. {vid.profile.get('self_min')})")
    if not res["ok"]:
        return 99, 0, 99, 0
    misses = false = own_total = strangers = 0
    worst_stranger, worst_own = 0.0, 1.0
    for voice, rate, own, (ask, yes), pcm in cases:
        ok, score = vid.verify(pcm)
        mark = "OK " if ok == own else "ERR"
        print(f"{mark} [{voice.split('-')[2]} {rate}] «{ask}» + «{yes}» ({len(pcm) / 32000:.1f} с) → "
              f"сходство {score:.3f} (порог {vid.threshold_for(len(pcm)):.3f}) → свой: {ok}")
        own_total += own
        strangers += not own
        misses += own and not ok
        false += bool(ok) and not own
        if own:
            worst_own = min(worst_own, score)
        else:
            worst_stranger = max(worst_stranger, score)
    print(f"{embed.name}: своих не узнал {misses} из {own_total}; чужих принял за своего {false} из {strangers}; "
          f"худший свой {worst_own:.3f}, самый похожий чужой {worst_stranger:.3f}")
    return misses, own_total, false, strangers


def main(asr_dir: str, spk_dir: str, wespeaker: str = "") -> int:
    engines = [V.VoskEmbedder(Path(asr_dir), Path(spk_dir))]
    if wespeaker:
        engines.append(V.SherpaEmbedder(Path(wespeaker)))
    with tempfile.TemporaryDirectory() as tmp:
        enroll, cases = synth(tmp)
        results = {e.name: check(e, enroll, cases, tmp) for e in engines}
    # Судим по основной модели (WeSpeaker, если дана); Vosk — для сравнения.
    name = engines[-1].name
    misses, _own, false, strangers = results[name]
    if strangers < 12:
        print("Слишком мало чужих голосов синтезировалось — проверка не состоялась")
        return 1
    print(f"\nИтог по {name}: {'ГОДИТСЯ' if not false and misses <= 1 else 'НЕ ГОДИТСЯ'}")
    return 1 if false or misses > 1 else 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:4]))
