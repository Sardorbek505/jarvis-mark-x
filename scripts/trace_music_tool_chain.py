"""JARVIS Mark X — Music Tool Call Chain Trace & Reproduction Script.

Traces step-by-step execution of music commands and measures blocking times,
tool response timing, and WebSocket session impact.
"""

import asyncio
import logging
import time
import uuid
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from actions.music_player import music_player
from core.media.parser import MediaIntentParser

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("music-trace")

async def trace_music_command(phrase: str, correlation_id: str):
    t_start = time.perf_counter()
    print(f"\n[{time.strftime('%H:%M:%S')}] [{correlation_id}] 1. USER SPEECH RECEIVED: '{phrase}'")
    
    # 2. GEMINI FUNCTION CALL RECEIVED
    t_fc = time.perf_counter()
    print(f"[{time.strftime('%H:%M:%S')}] [{correlation_id}] 2. GEMINI FUNCTION CALL RECEIVED (+{(t_fc - t_start)*1000:.1f}ms): name='music_player'")
    
    # 3. music_player START
    t_tool_start = time.perf_counter()
    print(f"[{time.strftime('%H:%M:%S')}] [{correlation_id}] 3. music_player START (+{(t_tool_start - t_start)*1000:.1f}ms)")
    
    # 4. MediaIntentParser
    t_parser_start = time.perf_counter()
    req = MediaIntentParser.parse(phrase)
    t_parser_end = time.perf_counter()
    print(f"[{time.strftime('%H:%M:%S')}] [{correlation_id}] 4. MediaIntentParser (+{(t_parser_end - t_parser_start)*1000:.2f}ms): title='{req.title}', type='{req.media_type}'")
    
    # 5. MediaOrchestrator & ProviderRouter
    from core.media.router import get_provider_router
    t_route_start = time.perf_counter()
    get_provider_router()
    # Route estimation
    t_route_end = time.perf_counter()
    print(f"[{time.strftime('%H:%M:%S')}] [{correlation_id}] 5. ProviderRouter (+{(t_route_end - t_route_start)*1000:.2f}ms)")
    
    # 6. Controller Execution & Provider Search
    t_exec_start = time.perf_counter()
    # Execute tool in thread to measure blocking time
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, lambda: music_player({"action": "play", "query": phrase}))
    t_exec_end = time.perf_counter()
    blocking_duration_ms = (t_exec_end - t_exec_start) * 1000.0
    print(f"[{time.strftime('%H:%M:%S')}] [{correlation_id}] 6. Provider/Controller execution (+{blocking_duration_ms:.1f}ms): result='{result[:60]}...'")
    
    # 7. FUNCTION RESPONSE SENT TO GEMINI
    t_resp = time.perf_counter()
    total_elapsed_ms = (t_resp - t_start) * 1000.0
    print(f"[{time.strftime('%H:%M:%S')}] [{correlation_id}] 7. FUNCTION RESPONSE SENT TO GEMINI (+{total_elapsed_ms:.1f}ms total)")
    
    # Evaluate connection risk
    connection_risk = "HIGH - WEBSOCKET TIMEOUT / DISCONNECT" if blocking_duration_ms > 3000.0 else "LOW"
    print(f"[{time.strftime('%H:%M:%S')}] [{correlation_id}] EVALUATION: Blocking Duration = {blocking_duration_ms:.1f}ms | Risk = {connection_risk}")
    
    return {
        "correlation_id": correlation_id,
        "phrase": phrase,
        "blocking_duration_ms": blocking_duration_ms,
        "total_elapsed_ms": total_elapsed_ms,
        "connection_risk": connection_risk
    }

async def run_reproduction_suite():
    print("=== MUSIC TOOL CHAIN REPRODUCTION & TRACE SUITE ===")
    
    test_phrases = [
        "Джарвис, поставь музыку",
        "Включи Queen",
        "Поставь старые популярные хиты",
        "Врубай рокерские хиты 80-х",
        "Поставь расслабляющую музыку"
    ]
    
    results = []
    for i, phrase in enumerate(test_phrases, 1):
        cid = f"corr-{uuid.uuid4().hex[:8]}"
        res = await trace_music_command(phrase, cid)
        results.append(res)
        await asyncio.sleep(0.5)
        
    print("\n=== SUMMARY OF TRACED COMMANDS ===")
    print(f"{'PHRASE':<35} {'BLOCKING TIME':<18} {'RISK':<15}")
    for r in results:
        print(f"{r['phrase']:<35} {r['blocking_duration_ms']:.1f} ms           {r['connection_risk']:<15}")

if __name__ == "__main__":
    asyncio.run(run_reproduction_suite())
