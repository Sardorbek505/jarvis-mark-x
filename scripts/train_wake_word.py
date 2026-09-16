"""Обучение wake-word «Джарвис» поверх эмбеддера openWakeWord.

Признаки — тот же embedding_model.onnx, что уже крутится в тракте (96 чисел
на кадр 80 мс). Классификатор — маленькая сеть (как у штатных моделей oww),
вход (1, 16, 96) = последние 1.28 с. Экспорт в ONNX с тем же интерфейсом, что
у hey_jarvis_v0.1.onnx, поэтому подключается в WakeWordDetector2Stage без
изменений в тракте.

Данные: data/wake/{pos,neg} — синтетика (wake_data_gen.py),
        data/wake/{user_pos,user_neg} — записи владельца (record_wake_samples.py).
Записи владельца весят больше и аугментируются сильнее: именно его голос модель
обязана ловить.

Запуск: python scripts/train_wake_word.py [--epochs 40] [--out config/wake_models/jarvis_ru.onnx]
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # не подменять объект: импорт из другого скрипта закрывал его

SR = 16000
CLIP = int(SR * 2.0)   # 16 кадров эмбеддера = ~2 с; клипы 1.5 с дополняются тишиной спереди
FRAMES = 16          # окно классификатора, как у штатных моделей oww
EMB = 96


def _load_dir(d: Path) -> list[np.ndarray]:
    return [np.load(f) for f in sorted(d.glob("*.npy"))]


def _fit_clip(x: np.ndarray, rng: np.random.Generator, jitter: bool) -> np.ndarray:
    """Клип к 1.5 с: слово — в конце окна (как в жизни: сработать, когда договорено)."""
    x = x.astype(np.int16)
    if jitter:
        # лёгкие вариации громкости и сдвига — для записей владельца
        gain = rng.uniform(0.5, 1.4)
        x = np.clip(x.astype(np.float32) * gain, -32768, 32767).astype(np.int16)
    if len(x) >= CLIP:
        # ищем самый громкий участок и ставим его конец к концу окна
        env = np.convolve(np.abs(x.astype(np.float32)), np.ones(1600) / 1600, mode="same")
        peak = int(np.argmax(env))
        end = min(len(x), peak + int(0.45 * SR) + (rng.integers(-800, 800) if jitter else 0))
        start = max(0, end - CLIP)
        seg = x[start:end]
        out = np.zeros(CLIP, dtype=np.int16)
        out[-len(seg):] = seg
        return out
    out = np.zeros(CLIP, dtype=np.int16)
    out[-len(x):] = x
    return out


def _speed(x: np.ndarray, factor: float) -> np.ndarray:
    """Темп и высота вместе (как у другого человека): factor>1 — быстрее и выше."""
    n = max(1, int(len(x) / factor))
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x.astype(np.float32)).astype(np.int16)


def _reverb(x: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    out = x.astype(np.float32)
    for _ in range(rng.integers(1, 3)):
        delay = int(rng.uniform(0.015, 0.07) * SR)
        echo = np.zeros_like(out)
        echo[delay:] = out[:-delay] * rng.uniform(0.15, 0.45)
        out = out + echo
    return np.clip(out, -32768, 32767).astype(np.int16)


def _owner_variant(c: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Одна аугментированная копия записи владельца: темп/высота, громкость,
    сдвиг, шум, иногда реверберация — микрофон и комната бывают разными."""
    v = _speed(c, rng.uniform(0.88, 1.14))
    v = _fit_clip(v, rng, True)
    if rng.random() < 0.35:
        v = _reverb(v, rng)
    return _add_noise(v, rng, rng.uniform(6, 30))


def _sapi_clips(texts: list[str], rng: np.random.Generator) -> list[np.ndarray]:
    """Третий движок голоса — Windows SAPI (Irina): ещё один тембр в обучение."""
    try:
        import wave
        import subprocess

        def _synthesize(text: str, path: Path) -> Path:
            # Как в latency_probe, но без импорта модуля: тот при импорте
            # подменяет sys.stdout и закрывает наш.
            script = (
                "Add-Type -AssemblyName System.Speech; "
                "$f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo("
                "16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, "
                "[System.Speech.AudioFormat.AudioChannel]::Mono); "
                "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                "$s.SelectVoice('Microsoft Irina Desktop'); "
                f"$s.Rate = {int(rng.integers(-3, 4))}; "
                f"$s.SetOutputToWaveFile('{path}', $f); "
                f"$s.Speak('{text}'); $s.Dispose()"
            )
            subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True, capture_output=True)
            return path

        cache = Path("data/wake/sapi")
        cache.mkdir(parents=True, exist_ok=True)
        out = []
        for i, t in enumerate(texts):
            import hashlib
            wav = cache / f"{hashlib.sha1(t.encode()).hexdigest()[:10]}.wav"
            if not wav.exists():
                _synthesize(t, wav)
            with wave.open(str(wav)) as w:
                out.append(np.frombuffer(w.readframes(w.getnframes()), np.int16))
        return out
    except Exception as e:
        print("SAPI пропущен:", e)
        return []


def _add_noise(x: np.ndarray, rng: np.random.Generator, snr_db: float) -> np.ndarray:
    n = rng.standard_normal(len(x)).astype(np.float32)
    sig_p = np.mean(x.astype(np.float32) ** 2) + 1e-6
    n *= np.sqrt(sig_p / (np.mean(n ** 2) + 1e-9) / (10 ** (snr_db / 10)))
    return np.clip(x.astype(np.float32) + n, -32768, 32767).astype(np.int16)


# ── Потоковая нарезка ────────────────────────────────────────────────────────
#
# Модель в тракте видит бегущее окно из 16 кадров эмбеддингов (80 мс шаг,
# ~1.3 с звука). Кадр k покрывает звук примерно до (k*80 + 760) мс. Позитив —
# только окно, конец которого попадает в зону «слово только что закончилось»;
# окно с половиной слова и окно с началом любой другой речи — негативы.
# Без этого модель училась ловить «тишина, потом голос» и срабатывала на
# любую фразу (стенд 16.09.2026).

LEAD_SEC = 1.6      # тишина/фон перед клипом в ленте
TAIL_SEC = 0.8
POS_ZONE = (-0.12, 0.40)   # конец окна относительно конца слова, с
FRAME_MS = 80
FRAME_OFFSET_MS = 760


def _speech_end_sec(clip: np.ndarray) -> float:
    env = np.convolve(np.abs(clip.astype(np.float32)), np.ones(320) / 320, mode="same")
    thr = env.max() * 0.15
    idx = np.where(env > thr)[0]
    return (idx[-1] / SR) if len(idx) else len(clip) / SR


def _timeline(clip: np.ndarray, rng: np.random.Generator, room: list) -> tuple[np.ndarray, float]:
    lead = np.zeros(int(LEAD_SEC * SR), dtype=np.float32)
    if room and rng.random() < 0.7:
        bed = room[rng.integers(len(room))]
        lead += np.resize(bed, len(lead)) * rng.uniform(0.5, 2.0)
    tail = np.zeros(int(TAIL_SEC * SR), dtype=np.float32)
    tl = np.concatenate([lead, clip.astype(np.float32), tail])
    return np.clip(tl, -32768, 32767).astype(np.int16), LEAD_SEC + _speech_end_sec(clip)


def _stream_windows(clips: list, is_pos: bool, rng: np.random.Generator, room: list,
                    negatives_only: bool = False) -> np.ndarray:
    """(N, FRAMES, 96) окон, нарезанных из лент по правилу выше."""
    from openwakeword.utils import AudioFeatures
    if not clips:
        return np.zeros((0, FRAMES, EMB), dtype=np.float32)
    af = AudioFeatures(inference_framework="onnx")
    lines, ends = zip(*[_timeline(c, rng, room) for c in clips])
    lines = list(lines)
    L = max(len(x) for x in lines)
    arr = np.zeros((len(lines), L), dtype=np.int16)
    for i, x in enumerate(lines):
        arr[i, : len(x)] = x
    emb = af.embed_clips(arr, batch_size=64)   # (N, T, 96)
    out = []
    for i in range(len(lines)):
        T = emb.shape[1]
        n_audio_frames = int((len(lines[i]) * 1000 / SR - FRAME_OFFSET_MS) / FRAME_MS)
        T = min(T, max(FRAMES, n_audio_frames))
        word_end = ends[i]
        pos_idx, neg_idx = [], []
        for e in range(FRAMES, T + 1):
            t_end = (e * FRAME_MS + FRAME_OFFSET_MS) / 1000.0
            rel = t_end - word_end
            if is_pos and POS_ZONE[0] <= rel <= POS_ZONE[1]:
                pos_idx.append(e)
            elif is_pos and rel < -0.30:
                neg_idx.append(e)          # слово ещё не договорено
            elif is_pos and rel > 0.75:
                neg_idx.append(e)          # слово давно прошло
            elif not is_pos:
                neg_idx.append(e)
        if negatives_only or not is_pos:
            pick = neg_idx
            if not is_pos and len(pick) > 4:
                pick = list(rng.choice(pick, size=4, replace=False))
            if negatives_only and len(pick) > 3:
                pick = list(rng.choice(pick, size=3, replace=False))
        else:
            pick = pos_idx
        for e in pick:
            out.append(emb[i, e - FRAMES:e, :])
    if not out:
        return np.zeros((0, FRAMES, EMB), dtype=np.float32)
    return np.stack(out).astype(np.float32)


def _features(clips: list[np.ndarray]) -> np.ndarray:
    """(N, FRAMES, 96) — последние 16 кадров эмбеддингов каждого клипа."""
    from openwakeword.utils import AudioFeatures
    af = AudioFeatures(inference_framework="onnx")
    arr = np.stack(clips).astype(np.int16)
    emb = af.embed_clips(arr, batch_size=64)      # (N, T, 96)
    return emb[:, -FRAMES:, :].astype(np.float32)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/wake")
    ap.add_argument("--out", default="config/wake_models/jarvis_ru.onnx")
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()
    rng = np.random.default_rng(args.seed)
    data = Path(args.data)

    pos = _load_dir(data / "pos")
    neg = _load_dir(data / "neg")
    upos = _load_dir(data / "user_pos")
    uneg = _load_dir(data / "user_neg")
    print(f"синтетика: {len(pos)} pos / {len(neg)} neg; владелец: {len(upos)} pos / {len(uneg)} neg")

    # Записи владельца: ×12 аугментаций (шум, громкость, сдвиг) — главное, что
    # модель обязана ловить. Часть оригиналов откладываем для честной оценки.
    hold_idx = set(rng.choice(len(upos), size=max(3, len(upos) // 5), replace=False).tolist()) if upos else set()

    # Шумовой профиль микрофона владельца: первые 0.4 с каждой записи — до
    # речи. Подмешивается в синтетику ОБЕИХ меток: иначе модель учит
    # «тембр этого микрофона = Джарвис» и ловит любую фразу владельца.
    room = [c[: int(0.4 * SR)].astype(np.float32) for c in (upos + uneg) if len(c) > SR]
    room = [r for r in room if np.sqrt(np.mean(r ** 2)) < 400]

    def _with_room(c: np.ndarray) -> np.ndarray:
        if not room or rng.random() < 0.3:
            return c
        bed = room[rng.integers(len(room))]
        bed = np.resize(bed, len(c)) * rng.uniform(0.6, 2.5)
        return np.clip(c.astype(np.float32) + bed, -32768, 32767).astype(np.int16)

    train_pos = [ _with_room(_fit_clip(c, rng, False)) for c in pos ]
    train_neg = [ _with_room(_fit_clip(c, rng, False)) for c in neg ]
    hold_pos, hold_neg = [], []
    OWNER_AUG = 40
    for i, c in enumerate(upos):
        base = _fit_clip(c, rng, False)
        if i in hold_idx:
            hold_pos.append(base)
            continue
        train_pos.append(base)
        for _ in range(OWNER_AUG):
            train_pos.append(_owner_variant(c, rng))
    # Негативы владельца — самые ценные: похожие слова ЕГО голосом. Половина —
    # в обучение (×40), половина — на честную оценку.
    n_pos_owner = max(1, sum(1 for i in range(len(upos)) if i not in hold_idx))
    n_neg_owner = max(1, len(uneg) - len(uneg) // 3)
    # столько же аугментаций негативов владельца, сколько позитивов — баланс по голосу
    neg_aug = max(OWNER_AUG, int(OWNER_AUG * n_pos_owner / n_neg_owner))
    for i, c in enumerate(uneg):
        base = _fit_clip(c, rng, False)
        if i % 3 == 2:
            hold_neg.append(base)
            continue
        train_neg.append(base)
        for _ in range(neg_aug):
            train_neg.append(_owner_variant(c, rng))
    print(f"владелец в обучении: pos ×{OWNER_AUG}, neg ×{neg_aug}")

    # Реальная комната: минутные записи data/wake/room/*.npy (без слова) и
    # дампы ложных срабатываний logs/debug_wake_*_raw.wav. Живой прогон
    # 17.09.2026: сеть давала 1.00 на шуме уровня 80–200 — синтетика такого
    # не содержала. Режем на клипы по 1.5 с с перекрытием.
    room_clips = []
    for f in sorted(glob.glob(str(data / "room" / "*.npy"))):
        r = np.load(f)
        step = int(0.75 * SR)
        for i in range(0, len(r) - int(1.5 * SR), step):
            room_clips.append(r[i:i + int(1.5 * SR)])
    import wave as _wave
    for f in sorted(glob.glob("logs/debug_wake_*_raw.wav")):
        try:
            with _wave.open(f) as w:
                room_clips.append(np.frombuffer(w.readframes(w.getnframes()), np.int16))
        except Exception:
            pass
    for c in room_clips:
        base = _fit_clip(c, rng, False)
        train_neg.append(base)
        for _ in range(3):
            v = _fit_clip(c, rng, True)
            train_neg.append(np.clip(v.astype(np.float32) * rng.uniform(0.3, 3.0), -32768, 32767).astype(np.int16))
    # часть комнаты — в отложенную оценку
    for c in room_clips[::7]:
        hold_neg.append(_fit_clip(c, rng, False))
    print(f"комната: {len(room_clips)} клипов-основ")

    # SAPI Irina — третий тембр
    from wake_data_gen import NEGATIVE_WORDS, POSITIVE_PHRASES  # noqa: E402
    sapi_pos = _sapi_clips([f"{p} " for p in POSITIVE_PHRASES] + ["Джарвис", "Эй Джарвис", "Джарвис включи", "Джарвис скажи"], rng)
    sapi_neg = _sapi_clips(NEGATIVE_WORDS[:60], rng)
    for c in sapi_pos:
        # у SAPI-фраз слово в начале: берём первые 1.2 с
        head = c[: int(1.2 * SR)]
        for _ in range(15):
            train_pos.append(_owner_variant(head, rng))
    for c in sapi_neg:
        for _ in range(6):
            train_neg.append(_owner_variant(c, rng))
    print(f"SAPI: {len(sapi_pos)} pos / {len(sapi_neg)} neg клипов-основ")

    # отложенная синтетика
    n_hold = 150
    hold_pos += train_pos[:n_hold]
    train_pos = train_pos[n_hold:]
    hold_neg += train_neg[:n_hold * 2]
    train_neg = train_neg[n_hold * 2:]

    print("признаки (скользящие окна по ленте, как в потоке)…")
    t0 = time.time()
    Xp, Xn = _stream_windows(train_pos, True, rng, room), _stream_windows(train_neg, False, rng, room)
    Hp, Hn = _stream_windows(hold_pos, True, rng, room), _stream_windows(hold_neg, False, rng, room)
    # окна-негативы, вырезанные из позитивных лент (недоговорённое слово, опоздание)
    Xn = np.concatenate([Xn, _stream_windows(train_pos, True, rng, room, negatives_only=True)])
    Hn = np.concatenate([Hn, _stream_windows(hold_pos, True, rng, room, negatives_only=True)])
    print(f"  {len(Xp)} pos, {len(Xn)} neg, hold {len(Hp)}/{len(Hn)} — {time.time() - t0:.0f} с")

    import torch
    import torch.nn as nn
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    X = torch.tensor(np.concatenate([Xp, Xn])).to(dev)
    y = torch.tensor(np.concatenate([np.ones(len(Xp)), np.zeros(len(Xn))]), dtype=torch.float32).to(dev)
    # веса классов: позитивов меньше
    pos_w = torch.tensor([len(Xn) / max(1, len(Xp))], device=dev)

    class Net(nn.Module):
        def __init__(self):
            super().__init__()
            self.net = nn.Sequential(
                nn.Flatten(), nn.Linear(FRAMES * EMB, 128), nn.LayerNorm(128), nn.ReLU(), nn.Dropout(0.25),
                nn.Linear(128, 64), nn.ReLU(), nn.Linear(64, 1),
            )

        def forward(self, x):
            return torch.sigmoid(self.net(x))

    torch.manual_seed(args.seed)
    net = Net().to(dev)
    opt = torch.optim.AdamW(net.parameters(), lr=2e-3, weight_decay=1e-3)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=args.epochs)
    bce = nn.BCELoss(reduction="none")
    n = len(X)
    for ep in range(args.epochs):
        net.train()
        perm = torch.randperm(n, device=dev)
        total = 0.0
        for i in range(0, n, 256):
            idx = perm[i:i + 256]
            xb, yb = X[idx], y[idx]
            # немного шума в признаках — устойчивость
            xb = xb + 0.02 * torch.randn_like(xb)
            p = net(xb).squeeze(1)
            w = torch.where(yb > 0.5, pos_w, torch.ones_like(yb))
            loss = (bce(p, yb) * w).mean()
            opt.zero_grad()
            loss.backward()
            opt.step()
            total += loss.item() * len(idx)
        sched.step()
        if ep % 5 == 0 or ep == args.epochs - 1:
            net.eval()
            with torch.no_grad():
                ph = net(torch.tensor(Hp).to(dev)).squeeze(1).cpu().numpy()
                nh = net(torch.tensor(Hn).to(dev)).squeeze(1).cpu().numpy()
            print(f"  эпоха {ep:2d} loss {total / n:.4f} | hold pos@0.5 {np.mean(ph > 0.5):.3f} neg@0.5 {np.mean(nh > 0.5):.3f}")

    # Порог: минимальный ложный пропуск при нуле ложных срабатываний на отложенных негативах
    net.eval()
    with torch.no_grad():
        ph = net(torch.tensor(Hp).to(dev)).squeeze(1).cpu().numpy()
        nh = net(torch.tensor(Hn).to(dev)).squeeze(1).cpu().numpy()
        owner = net(torch.tensor(_stream_windows([_fit_clip(c, rng, False) for c in upos], True, rng, room)).to(dev)).squeeze(1).cpu().numpy() if upos else np.array([])
    best = None
    for thr in np.arange(0.3, 0.96, 0.02):
        fa = float(np.mean(nh >= thr))
        tp = float(np.mean(ph >= thr))
        if best is None or (fa, -tp) < (best[1], -best[2]):
            best = (thr, fa, tp)
    thr, fa, tp = best
    print(f"порог {thr:.2f}: отложенные — ловит {tp:.1%}, ложных {fa:.2%}; записи владельца ≥порога: {np.mean(owner >= thr):.1%} (медиана {np.median(owner):.2f})")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    net_cpu = net.to("cpu").eval()
    dummy = torch.zeros(1, FRAMES, EMB)
    torch.onnx.export(net_cpu, dummy, str(out), input_names=["x"], output_names=["y"],
                      opset_version=17, dynamic_axes=None, dynamo=False)
    meta = {"threshold": round(float(thr), 2), "frames": FRAMES, "hold_tp": tp, "hold_fa": fa,
            "owner_recall": float(np.mean(owner >= thr)) if len(owner) else None,
            "train_pos": len(Xp), "train_neg": len(Xn)}
    out.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print("сохранено:", out, meta)


if __name__ == "__main__":
    main()
