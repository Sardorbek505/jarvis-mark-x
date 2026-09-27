"""Узнавание голоса на настоящем звуке (CI, job wake-word).

«Владелец» — голос Edge ru-RU-DmitryNeural: записывает 5 фраз, как в окне
«Обо мне». Потом его же другие фразы (и чуть быстрее — как живой человек
говорит по-разному) должны узнаваться, а чужие голоса — нет. Модели — те же,
что в Джарвисе: vosk-small-ru (речь) + vosk-model-spk (отпечатки).

Синтез — нижняя граница трудности: живые голоса похожи сильнее. Порог
подбирается по записям владельца (VoiceID.enroll), здесь проверяем, что
при нём свои проходят, а чужие — нет, и печатаем сходство для подстройки.

Запуск: python scripts/voice_id_check.py models/vosk-small-ru models/vosk-spk
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
# Чужие — прежде всего мужские: женский голос отсекается легко (0.13–0.30).
STRANGERS = ["uk-UA-OstapNeural", "kk-KZ-DauletNeural", "en-GB-RyanNeural", "de-DE-ConradNeural",
             "pl-PL-MarekNeural", "ru-RU-SvetlanaNeural"]
# Как в жизни: просьба, потом «да» — Джарвис сверяет речь за последние 30 с.
TESTS = [("Джарвис, напиши маме, что я задержусь", "Да, отправляй"),
         ("Позвони брату и скажи, что ужин готов", "Да"),
         ("Выключи компьютер", "Да, выключай"),
         ("Удали папку загрузки", "Да, точно")]


def main(asr_dir: str, spk_dir: str) -> int:
    embed = V.VoskEmbedder(Path(asr_dir), Path(spk_dir))
    with tempfile.TemporaryDirectory() as tmp:
        vid = V.VoiceID(Path(tmp) / "voice_id.json", embed=embed)
        res = vid.enroll([W._say(p, OWNER, tmp) for p in V.ENROLL_PHRASES])
        print(f"Запись: {res['text']} порог {vid.threshold:.3f}, сходство своих {vid.profile.get('self_sim')}")
        if not res["ok"]:
            return 1
        misses, false, own_total, strangers_heard = [], [], 0, 0
        for voice, rate, own in [(OWNER, "+0%", True), (OWNER, "+15%", True)] + [(v, "+0%", False) for v in STRANGERS]:
            for ask, yes in TESTS:
                try:
                    pcm = W._say(ask, voice, tmp, rate) + W._say(yes, voice, tmp, rate)
                except Exception as exc:          # Edge не отдал звук этим голосом — не наша проверка
                    print(f"--- [{voice}] синтез не удался ({type(exc).__name__}) — пропускаю голос")
                    break
                ok, score = vid.verify(pcm)
                sec = len(pcm) / 32000
                mark = "OK " if ok == own else "ERR"
                print(f"{mark} [{voice.split('-')[2]} {rate}] «{ask}» + «{yes}» ({sec:.1f} с) → сходство "
                      f"{score:.3f} (порог {vid.threshold_for(len(pcm)):.3f}) → свой: {ok}")
                own_total += own
                strangers_heard += not own
                if own and not ok:
                    misses.append((voice, ask))
                if ok and not own:
                    false.append((voice, ask))
    print(f"\nСвоих не узнал: {len(misses)} из {own_total}; чужих принял за своего: {len(false)} из {strangers_heard}")
    if strangers_heard < 8:
        print("Слишком мало чужих голосов синтезировалось — проверка не состоялась")
        return 1
    return 1 if false or len(misses) > 1 else 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:3]))
