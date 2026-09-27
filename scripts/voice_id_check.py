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
STRANGERS = ["ru-RU-SvetlanaNeural", "ru-RU-DariyaNeural", "uk-UA-OstapNeural", "kk-KZ-DauletNeural"]
TESTS = ["Джарвис, напиши маме, что я задержусь", "Да, отправляй",
         "Позвони брату и скажи, что ужин готов", "Да, выключай компьютер"]


def main(asr_dir: str, spk_dir: str) -> int:
    embed = V.VoskEmbedder(Path(asr_dir), Path(spk_dir))
    with tempfile.TemporaryDirectory() as tmp:
        vid = V.VoiceID(Path(tmp) / "voice_id.json", embed=embed)
        res = vid.enroll([W._say(p, OWNER, tmp) for p in V.ENROLL_PHRASES])
        print(f"Запись: {res['text']} порог {vid.threshold:.3f}, сходство своих {vid.profile.get('self_sim')}")
        if not res["ok"]:
            return 1
        misses, false = [], []
        for voice, rate, own in [(OWNER, "+0%", True), (OWNER, "+15%", True)] + [(v, "+0%", False) for v in STRANGERS]:
            for phrase in TESTS:
                # «да» короткое — как в жизни, проверяется вместе с просьбой перед ним
                pcm = W._say(phrase, voice, tmp, rate) + W._say("Да, подтверждаю", voice, tmp, rate)
                ok, score = vid.verify(pcm)
                mark = "OK " if ok == own else "ERR"
                print(f"{mark} [{voice.split('-')[2]} {rate}] «{phrase}» → сходство {score:.3f} → свой: {ok}")
                if own and not ok:
                    misses.append((voice, phrase))
                if ok and not own:
                    false.append((voice, phrase))
    print(f"\nСвоих не узнал: {len(misses)} из {2 * len(TESTS)}; чужих принял за своего: {len(false)}")
    return 1 if false or len(misses) > 1 else 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:3]))
