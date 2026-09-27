"""Узнавание голоса на настоящих людях (CI, job wake-word).

Синтез для этого не годится: голоса Edge сделаны на общей основе, и модель
отпечатков считает «Остапа» и «Дмитрия» одним человеком (замерено: 0.94).
Поэтому — Mini LibriSpeech (dev-clean-2): живые дикторы, мужчины и женщины.

Каждый диктор «записывает голос» 5 фразами, как в окне «Обо мне»
(VoiceID.enroll — тот же порог, что в Джарвисе). Потом проверяем:
  • его же другие фразы — должны проходить;
  • фразы других людей ТОГО ЖЕ пола — не должны (самый трудный случай).
Печатаем долю промахов и ложных «свой» для каждой модели и распределения
сходства — по ним подбираются пределы порога (LO/HI/MARGIN в core/voice_id.py).

Запуск: python scripts/voice_id_check.py LibriSpeech models/vosk-small-ru models/vosk-spk \\
        models/voice-id/wespeaker_en_voxceleb_resnet34.onnx
"""
import hashlib
import importlib.util
import random
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

PER_GENDER = 10
ENROLL_N = 5
TEST_N = 4
MIN_SEC = 3.0
MAX_FA = 0.02            # чужих «свой» — не больше 2 %
MAX_MISS = 0.10          # своих не узнал — не больше 10 %


def _pcm(path: Path) -> bytes:
    import numpy as np
    import soundfile as sf
    x, sr = sf.read(str(path), dtype="int16")
    assert sr == V.SAMPLE_RATE, sr
    return np.asarray(x, dtype="int16").tobytes()


def pitch(pcm: bytes) -> float:
    """Медиана основного тона (Гц) по звонким кадрам — автокорреляция."""
    import numpy as np
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    n, hop = 640, 320                                  # 40 мс кадр, 20 мс шаг
    lo, hi = V.SAMPLE_RATE // 300, V.SAMPLE_RATE // 70
    energy = [float(np.sqrt(np.mean(x[i:i + n] ** 2))) for i in range(0, len(x) - n, hop)]
    loud = np.percentile(energy, 60) if energy else 0
    f0 = []
    for k, i in enumerate(range(0, len(x) - n, hop)):
        if energy[k] < loud:
            continue
        fr = x[i:i + n] - x[i:i + n].mean()
        ac = np.correlate(fr, fr, "full")[n - 1:]
        if ac[0] <= 0:
            continue
        lag = lo + int(np.argmax(ac[lo:hi]))
        if ac[lag] / ac[0] > 0.45:
            f0.append(V.SAMPLE_RATE / lag)
    return float(np.median(f0)) if f0 else 0.0


def _genders(root: Path) -> dict[str, str]:
    """Пол дикторов из SPEAKERS.TXT, если он есть (в полном LibriSpeech)."""
    f = next(iter(root.rglob("SPEAKERS.TXT")), None)
    out = {}
    if f:
        for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
            parts = [p.strip() for p in line.split("|")]
            if not line.startswith(";") and len(parts) > 1:
                out[parts[0]] = parts[1]
    return out


def speakers(root: Path) -> list[tuple[str, str, list[Path]]]:
    """[(id, пол, файлы)] — по PER_GENDER мужчин и женщин с достаточной речью.
    В Mini LibriSpeech списка дикторов нет — пол по высоте голоса (< 165 Гц — M)."""
    import soundfile as sf
    known = _genders(root)
    data = next(p for p in root.rglob("*") if p.is_dir() and p.name.startswith("dev-clean"))
    out = {"M": [], "F": []}
    for spk in sorted(p for p in data.iterdir() if p.is_dir()):
        long = [f for f in sorted(spk.rglob("*.flac")) if sf.info(str(f)).duration >= MIN_SEC]
        if len(long) < ENROLL_N + TEST_N:
            continue
        g = known.get(spk.name)
        if not g:
            f0 = pitch(b"".join(_pcm(f) for f in long[:3]))
            g = "M" if 0 < f0 < 165 else "F" if f0 else ""
            print(f"  диктор {spk.name}: основной тон {f0:.0f} Гц → {g or '?'}")
        if g in out and len(out[g]) < PER_GENDER:
            out[g].append((spk.name, g, long))
        if all(len(v) >= PER_GENDER for v in out.values()):
            break
    return out["M"] + out["F"]


class Cached:
    """Отпечаток каждого звука считается один раз на модель."""

    def __init__(self, embed):
        self.embed, self.name, self._c = embed, embed.name, {}
        for k in ("LO", "HI", "MARGIN"):
            setattr(self, k, getattr(embed, k))

    def __call__(self, pcm: bytes):
        key = hashlib.md5(pcm).hexdigest()
        if key not in self._c:
            self._c[key] = self.embed(pcm)
        return self._c[key]


def _q(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))]


def check(embed, spk, tmp: str) -> tuple[float, float]:
    rnd = random.Random(1)
    emb = Cached(embed)
    profiles, tests = {}, {}
    for sid, g, files in spk:
        files = files[:]
        rnd.shuffle(files)
        vid = V.VoiceID(Path(tmp) / f"{embed.name}_{sid}.json", embed=emb)
        res = vid.enroll([_pcm(f) for f in files[:ENROLL_N]])
        if not res["ok"]:
            print(f"  {sid}: запись не вышла — {res['text']}")
            continue
        profiles[sid] = (g, vid)
        tests[sid] = (g, [_pcm(f) for f in files[ENROLL_N:ENROLL_N + TEST_N]])
    own, strange, misses, fa = [], [], 0, 0
    raw_own, raw_strange = [], []           # чистое сходство — для таблицы порогов
    for sid, (g, vid) in profiles.items():
        for s2, (g2, pcms) in tests.items():
            if g2 != g:
                continue
            for pcm in pcms:
                ok, score = vid.verify(pcm)
                (raw_own if s2 == sid else raw_strange).append(score)
                if s2 == sid:
                    own.append(score)
                    misses += not ok
                else:
                    strange.append(score)
                    fa += bool(ok)
    thr = sorted(vid.threshold for _g, vid in profiles.values())
    selfs = sorted(vid.profile["self_min"] for _g, vid in profiles.values())
    miss_r, fa_r = misses / max(1, len(own)), fa / max(1, len(strange))
    print(f"\n=== {embed.name}: дикторов {len(profiles)}, порог {thr[0]:.3f}…{thr[-1]:.3f}, "
          f"мин. сходство записей с отпечатком {selfs[0]:.3f}…{selfs[-1]:.3f}")
    print(f"  свои   ({len(own)}): мин {min(own):.3f}, 5% {_q(own, .05):.3f}, медиана {_q(own, .5):.3f}")
    print(f"  чужие  ({len(strange)}, тот же пол): медиана {_q(strange, .5):.3f}, 95% {_q(strange, .95):.3f}, "
          f"99% {_q(strange, .99):.3f}, макс {max(strange):.3f}")
    print(f"  своих не узнал {misses} ({miss_r:.1%}); чужих принял за своего {fa} ({fa_r:.1%})")
    for t in (0.5, 0.6, 0.7, 0.75, 0.78, 0.8, 0.82, 0.84, 0.86, 0.88):
        print(f"    порог {t:.2f}: промахов {sum(s < t for s in raw_own) / len(raw_own):5.1%}, "
              f"чужих {sum(s >= t for s in raw_strange) / len(raw_strange):5.1%}")
    return miss_r, fa_r


def main(libri: str, asr_dir: str = "", spk_dir: str = "", wespeaker: str = "") -> int:
    spk = speakers(Path(libri))
    print(f"Дикторы: {sum(g == 'M' for _s, g, _f in spk)} муж., {sum(g == 'F' for _s, g, _f in spk)} жен.")
    if min(sum(g == x for _s, g, _f in spk) for x in "MF") < 3:
        print("Мало дикторов одного пола — проверка не состоялась")
        return 1
    engines = []
    if asr_dir and spk_dir:
        engines.append(V.VoskEmbedder(Path(asr_dir), Path(spk_dir)))
    if wespeaker:
        engines.append(V.SherpaEmbedder(Path(wespeaker)))
    with tempfile.TemporaryDirectory() as tmp:
        results = {e.name: check(e, spk, tmp) for e in engines}
    # Судим по основной модели (WeSpeaker, если дана); Vosk — для сравнения.
    name = engines[-1].name
    miss_r, fa_r = results[name]
    good = fa_r <= MAX_FA and miss_r <= MAX_MISS
    print(f"\nИтог по {name}: {'ГОДИТСЯ' if good else 'НЕ ГОДИТСЯ'} "
          f"(нужно: чужих ≤ {MAX_FA:.0%}, промахов ≤ {MAX_MISS:.0%})")
    return 0 if good else 1


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:5]))
