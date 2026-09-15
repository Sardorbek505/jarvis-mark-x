"""JARVIS Mark X — Интеграционный Smoke-тест медиасистемы без моков.

Запускает реальные вызовы MediaOrchestrator к YouTube, VK Video и Spotify:
  1. Проверяет открытие реальных страниц.
  2. Проверяет команды управления (pause, resume, seek, mute, unmute, volume, fullscreen, close).
  3. Регистрирует результаты и ограничения в итоговом отчёте.
"""

import sys
import os
import time


# Принудительная настройка UTF-8 вывода для Windows консоли
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

# Добавляем корень проекта в sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.media.models import parse_media_request, MediaType
from core.media.orchestrator import get_media_orchestrator


def run_smoke_tests():
    orchestrator = get_media_orchestrator()
    print("=" * 60)
    print("СТАРТ REAL INTEGRATION SMOKE TEST (БЕЗ МОКОВ)")
    print("=" * 60)

    results = []

    # ── 1. ТЕСТ YOUTUBE ────────────────────────────────────────────────────────
    print("\n[1/3] Тестирование YouTube...")
    try:
        req_yt = parse_media_request("Включи видео про SpaceX на ютубе")
        open_res = orchestrator.play_media(req_yt)
        print(f"  * Open: {open_res}")
        time.sleep(3.0)

        p_res = orchestrator.pause()
        print(f"  * Pause: {p_res}")
        time.sleep(1.0)

        r_res = orchestrator.resume()
        print(f"  * Resume: {r_res}")
        time.sleep(1.0)

        s_fw = orchestrator.seek_relative(30)
        print(f"  * Seek +30s: {s_fw}")
        time.sleep(1.0)

        s_bk = orchestrator.seek_relative(-30)
        print(f"  * Seek -30s: {s_bk}")
        time.sleep(1.0)

        s_pct = orchestrator.seek_percent(50)
        print(f"  * Seek 50%: {s_pct}")
        time.sleep(1.0)

        m_on = orchestrator.mute()
        print(f"  * Mute: {m_on}")
        time.sleep(1.0)

        m_off = orchestrator.unmute()
        print(f"  * Unmute: {m_off}")
        time.sleep(1.0)

        fs_on = orchestrator.enter_fullscreen()
        print(f"  * Fullscreen ON: {fs_on}")
        time.sleep(1.5)

        fs_off = orchestrator.exit_fullscreen()
        print(f"  * Fullscreen OFF: {fs_off}")
        time.sleep(1.0)

        vol = orchestrator.set_volume(40)
        print(f"  * Volume 40%: {vol}")
        time.sleep(1.0)

        cls = orchestrator.close()
        print(f"  * Close: {cls}")

        results.append(("YouTube", "PASSED"))
    except Exception as e:
        print(f"  [!] Ошибка YouTube: {e}")
        results.append(("YouTube", f"FAILED: {e}"))

    # ── 2. ТЕСТ VK VIDEO ───────────────────────────────────────────────────────
    print("\n[2/3] Тестирование VK Видео...")
    try:
        req_vk = parse_media_request("Включи 1 сезон 1 серию сериала Матрица на вк")
        open_res = orchestrator.play_media(req_vk)
        print(f"  * Open: {open_res}")
        time.sleep(3.0)

        p_res = orchestrator.pause()
        print(f"  * Pause: {p_res}")
        time.sleep(1.0)

        r_res = orchestrator.resume()
        print(f"  * Resume: {r_res}")
        time.sleep(1.0)

        s_fw = orchestrator.seek_relative(30)
        print(f"  * Seek +30s: {s_fw}")
        time.sleep(1.0)

        s_pct = orchestrator.seek_percent(50)
        print(f"  * Seek 50%: {s_pct}")
        time.sleep(1.0)

        fs_on = orchestrator.enter_fullscreen()
        print(f"  * Fullscreen ON: {fs_on}")
        time.sleep(1.5)

        fs_off = orchestrator.exit_fullscreen()
        print(f"  * Fullscreen OFF: {fs_off}")
        time.sleep(1.0)

        cls = orchestrator.close()
        print(f"  * Close: {cls}")

        results.append(("VK Video", "PASSED"))
    except Exception as e:
        print(f"  [!] Ошибка VK Video: {e}")
        results.append(("VK Video", f"FAILED: {e}"))

    # ── 3. ТЕСТ SPOTIFY ────────────────────────────────────────────────────────
    print("\n[3/3] Тестирование Spotify...")
    try:
        req_sp = parse_media_request("Включи Queen Bohemian Rhapsody на спотифай")
        req_sp.media_type = MediaType.MUSIC
        open_res = orchestrator.play_media(req_sp)
        print(f"  * Open: {open_res}")
        time.sleep(2.0)

        p_res = orchestrator.pause()
        print(f"  * Pause: {p_res}")
        time.sleep(1.0)

        r_res = orchestrator.resume()
        print(f"  * Resume: {r_res}")
        time.sleep(1.0)

        nxt = orchestrator.next_track()
        print(f"  * Next: {nxt}")
        time.sleep(1.0)

        prv = orchestrator.previous_track()
        print(f"  * Previous: {prv}")
        time.sleep(1.0)

        cur = orchestrator.get_current_media()
        print(f"  * Current Track: {cur}")

        cls = orchestrator.close()
        print(f"  * Close: {cls}")

        results.append(("Spotify", "PASSED"))
    except Exception as e:
        print(f"  [!] Ошибка Spotify: {e}")
        results.append(("Spotify", f"FAILED: {e}"))

    print("\n" + "=" * 60)
    print("ИТОГИ REAL SMOKE TEST:")
    for name, status in results:
        print(f"  * {name}: {status}")
    print("=" * 60)


if __name__ == "__main__":
    run_smoke_tests()
