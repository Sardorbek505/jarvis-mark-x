"""Записывает с микрофона музыку из колонок — фон для обучения wake-модели.

Живой прогон 17.09.2026: под музыкой модель слышала «Джарвис» 3 раза из 7.
Программное эхоподавление на встроенных динамиках ноутбука не помогает
(AEC3 глушил и голос), поэтому модель учится на реальном звуке «музыка в
этой комнате через этот микрофон».

Играет то, что открыто в Spotify (явный play через Windows, а не media-клавиша:
та переключает и могла ставить на паузу), каждые SEGMENT_SEC —
следующий трек и другая громкость приложения. Сохраняет
data/wake/music/bed_NN.npy (16 кГц int16). В конце — пауза и громкость 100%.
"""
import argparse
import sys
import threading
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.media_session_manager import control_app  # noqa: E402
from core.audio_capture import AudioCaptureEngine  # noqa: E402

SEGMENT_SEC = 40
VOLUMES = (1.0, 0.6, 0.3)


def set_spotify_volume(level: float) -> None:
    from pycaw.pycaw import AudioUtilities, ISimpleAudioVolume
    for s in AudioUtilities.GetAllSessions():
        if s.Process and s.Process.name().lower() == "spotify.exe":
            s._ctl.QueryInterface(ISimpleAudioVolume).SetMasterVolume(level, None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--segments", type=int, default=6)
    ap.add_argument("--out", default="data/wake/music")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    start_idx = len(list(out.glob("bed_*.npy")))

    buf: list[bytes] = []
    refs: list[bytes] = []
    lock = threading.Lock()

    def on_frame(mic: bytes, ref: bytes, _ts: float):
        with lock:
            buf.append(mic)
            refs.append(ref)

    eng = AudioCaptureEngine(on_frame=on_frame)
    eng.start()
    try:
        for seg in range(args.segments):
            set_spotify_volume(VOLUMES[seg % len(VOLUMES)])
            if seg:
                control_app("spotify", "next")
            control_app("spotify", "play")
            time.sleep(1.5)
            with lock:
                buf.clear()
                refs.clear()
            time.sleep(SEGMENT_SEC)
            with lock:
                pcm = np.frombuffer(b"".join(buf), np.int16)
                ref = np.frombuffer(b"".join(refs), np.int16)
            ref_rms = float(np.sqrt(np.mean(ref.astype(np.float64) ** 2))) if len(ref) else 0.0
            if ref_rms < 50:
                print(f"сегмент {seg}: в колонках тишина (ref rms {ref_rms:.0f}) — пропуск")
                continue
            path = out / f"bed_{start_idx + seg:02d}.npy"
            np.save(path, pcm)
            rms = float(np.sqrt(np.mean(pcm.astype(np.float64) ** 2))) if len(pcm) else 0.0
            print(f"{path.name}: {len(pcm) / 16000:.0f} с, громкость {VOLUMES[seg % len(VOLUMES)]:.0%}, rms {rms:.0f}")
    finally:
        control_app("spotify", "pause")
        set_spotify_volume(1.0)
        eng.stop()


if __name__ == "__main__":
    main()
