"""JARVIS Mark X — Resource Trend & Handle/Thread Owner Audit Script."""

import gc
import os
import psutil
import sys
import threading
import time
import tracemalloc

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.media.models import MediaSession, MediaType
from core.media.parser import MediaIntentParser
from core.media.state import get_media_tracker
from core.media.bridge.server import BrowserBridgeServer
from core.media.bridge.bridge import get_browser_bridge

def get_snapshot(stage_name: str, proc: psutil.Process):
    gc.collect()
    mem_info = proc.memory_info()
    rss = mem_info.rss / (1024 * 1024)
    tracemalloc_curr, tracemalloc_peak = tracemalloc.get_traced_memory()
    tracemalloc_curr_mb = tracemalloc_curr / (1024 * 1024)
    
    threads = proc.threads()
    thread_count = len(threads)
    active_thread_names = [t.name for t in threading.enumerate()]
    
    try:
        handles = proc.num_handles()
    except Exception:
        handles = 0
        
    try:
        connections = proc.net_connections()
        sockets = len(connections)
    except Exception:
        sockets = 0
        
    children = len(proc.children(recursive=True))
    
    return {
        "stage": stage_name,
        "rss_mb": round(rss, 2),
        "heap_mb": round(tracemalloc_curr_mb, 2),
        "thread_count": thread_count,
        "thread_names": active_thread_names,
        "handles": handles,
        "sockets": sockets,
        "processes": children + 1
    }

def run_audit():
    tracemalloc.start()
    proc = psutil.Process(os.getpid())
    snapshots = []
    
    # 1. BEFORE (Cold Start)
    snapshots.append(get_snapshot("BEFORE (Cold Start)", proc))
    
    # 2. AFTER WARMUP
    get_browser_bridge()
    server = BrowserBridgeServer.get_instance()
    _ = MediaIntentParser.parse("Поставь фильм Интерстеллар")
    tracker = get_media_tracker()
    tracker.set_active_session(MediaSession(title="Warmup", media_type=MediaType.MOVIE))
    snapshots.append(get_snapshot("AFTER WARMUP", proc))
    
    # 3. AFTER 500 OPS
    for i in range(500):
        _ = MediaIntentParser.parse("Пауза")
        _ = MediaIntentParser.parse("Продолжи")
    snapshots.append(get_snapshot("AFTER 500 OPS", proc))
    
    # 4. AFTER 1000 OPS
    for i in range(500):
        _ = MediaIntentParser.parse("Громче на 10 процентов")
        _ = MediaIntentParser.parse("Включи рок в спотифай")
    snapshots.append(get_snapshot("AFTER 1000 OPS", proc))
    
    # 5. AFTER 2000 OPS
    for i in range(1000):
        _ = MediaIntentParser.parse(f"Во все тяжкие {i % 5 + 1} сезон {i % 10 + 1} серия")
    snapshots.append(get_snapshot("AFTER 2000 OPS", proc))
    
    # 6. AFTER 5000 OPS
    for i in range(3000):
        _ = MediaIntentParser.parse_intent("стоп")
    snapshots.append(get_snapshot("AFTER 5000 OPS", proc))
    
    # 7. AFTER GC
    gc.collect()
    snapshots.append(get_snapshot("AFTER GC", proc))
    
    # 8. AFTER FULL SHUTDOWN
    server._running = False
    if server._server_socket:
        try:
            server._server_socket.close()
        except Exception:
            pass
    time.sleep(0.2)
    gc.collect()
    snapshots.append(get_snapshot("AFTER FULL SHUTDOWN", proc))
    
    print("=== RESOURCE TREND SNAPSHOTS ===")
    print(f"{'STAGE':<25} {'RSS (MB)':<10} {'HEAP (MB)':<10} {'THREADS':<10} {'HANDLES':<10} {'SOCKETS':<10}")
    for s in snapshots:
        print(f"{s['stage']:<25} {s['rss_mb']:<10} {s['heap_mb']:<10} {s['thread_count']:<10} {s['handles']:<10} {s['sockets']:<10}")
        
    print("\n=== THREAD OWNER ANALYSIS (AT FULL SHUTDOWN) ===")
    for name in snapshots[-1]["thread_names"]:
        print(f" - Thread: {name}")

if __name__ == "__main__":
    run_audit()
