"""Эталон голоса владельца из записей data/wake/user_pos + user_neg.

Запуск: python scripts/enroll_owner_voice.py → config/owner_voice.npy
Печатает самопроверку: сходство каждой записи с эталоном (ожидается ≥ 0.6).
"""

from __future__ import annotations

import glob
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from core.speaker_verifier import ENROLLMENT_PATH, SpeakerVerifier  # noqa: E402


def main():
    files = sorted(glob.glob("data/wake/user_pos/*.npy")) + sorted(glob.glob("data/wake/user_neg/*.npy"))
    if len(files) < 10:
        print("Мало записей владельца (нужно ≥10): сначала scripts/record_wake_samples.py")
        return 1
    v = SpeakerVerifier(enrollment=np.zeros(256))
    embs = []
    for f in files:
        e = v.embed(np.load(f))
        if e is not None:
            embs.append(e / (np.linalg.norm(e) + 1e-9))
    bank = np.stack(embs).astype(np.float32)
    ENROLLMENT_PATH.parent.mkdir(parents=True, exist_ok=True)
    np.save(ENROLLMENT_PATH, bank)
    # самопроверка leave-one-out: среднее по трём ближайшим ДРУГИМ записям
    scores = []
    for i in range(len(bank)):
        sims = np.delete(bank, i, 0) @ bank[i]
        scores.append(float(np.sort(sims)[-3:].mean()))
    print(f"Банк из {len(embs)} записей → {ENROLLMENT_PATH}")
    print(f"Самопроверка (leave-one-out): min {min(scores):.2f}, медиана {np.median(scores):.2f}, "
          f"ниже 0.62: {sum(s < 0.62 for s in scores)} из {len(scores)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
