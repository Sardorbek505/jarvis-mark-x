"""Proxies browser WebSocket ↔ Gemini Live API."""
import asyncio
import base64
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from google import genai
from google.genai import types
from fastapi import WebSocket

from telegram_bot.server.memory import Memory
from telegram_bot.config import Config

logger = logging.getLogger(__name__)

LIVE_MODEL = "models/gemini-2.5-flash-native-audio-latest"

_SYSTEM_BASE = """Ты — JARVIS, персональный ИИ-ассистент пользователя.
Ты его второй мозг: помнишь всё, что он говорил, его цели и привычки.
Отвечаешь кратко, умно и по делу. Говоришь на том же языке что и пользователь.
Когда пользователь говорит о своих планах, целях, предпочтениях — запоминай это."""


class VoiceSession:
    def __init__(self, user_id: int, ws: WebSocket, memory: Memory, config: Config):
        self._uid = user_id
        self._ws = ws
        self._mem = memory
        self._cfg = config
        self._session = None
        self._voice_active = False

    def _live_config(self, context: str) -> types.LiveConnectConfig:
        system = f"{_SYSTEM_BASE}\n\n{context}" if context else _SYSTEM_BASE
        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            system_instruction=types.Content(
                parts=[types.Part(text=system)], role="user"
            ),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Charon")
                )
            ),
        )

    async def run(self):
        context = await self._mem.get_context()
        client = genai.Client(
            api_key=self._cfg.gemini_api_key,
            http_options={"api_version": "v1beta"},
        )

        try:
            async with client.aio.live.connect(
                model=LIVE_MODEL, config=self._live_config(context)
            ) as session:
                self._session = session
                await self._push("status", state="idle")

                async with asyncio.TaskGroup() as tg:
                    tg.create_task(self._from_client())
                    tg.create_task(self._from_gemini())

        except* Exception as eg:
            for exc in eg.exceptions:
                logger.error(f"Session error: {exc}")
            await self._push("status", state="idle")

    async def _from_client(self):
        """Browser WebSocket → Gemini Live."""
        async for raw in self._ws.iter_text():
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            t = msg.get("type")

            if t == "audio" and self._voice_active:
                pcm = base64.b64decode(msg["data"])
                try:
                    await self._session.send_realtime_input(
                        media={"data": pcm, "mime_type": "audio/pcm"}
                    )
                except Exception as e:
                    logger.debug(f"send_realtime_input: {e}")

            elif t == "start_voice":
                self._voice_active = True
                await self._push("status", state="listening")

            elif t == "stop_voice":
                self._voice_active = False
                await self._push("status", state="processing")
                try:
                    await self._session.send(end_of_turn=True)
                except Exception:
                    pass

            elif t == "text":
                text = msg.get("text", "").strip()
                if text:
                    await self._mem.add_message("user", text)
                    await self._push("status", state="processing")
                    await self._push("thinking")
                    try:
                        await self._session.send(input=text, end_of_turn=True)
                    except Exception as e:
                        logger.error(f"send text: {e}")
                        await self._push("status", state="idle")

    async def _from_gemini(self):
        """Gemini Live → browser WebSocket."""
        out_text: list[str] = []

        try:
            async for response in self._session.receive():
                # Raw audio chunk → forward immediately
                if response.data:
                    b64 = base64.b64encode(response.data).decode()
                    await self._push("audio", data=b64)
                    await self._push("status", state="speaking")

                sc = response.server_content
                if sc:
                    # User speech transcription
                    if sc.input_transcription and sc.input_transcription.text:
                        t = sc.input_transcription.text.strip()
                        if t:
                            await self._push("transcript_user", text=t)
                            await self._mem.add_message("user", t)

                    # Model response transcription — accumulate until turn_complete
                    if sc.output_transcription and sc.output_transcription.text:
                        out_text.append(sc.output_transcription.text)

                    if sc.turn_complete:
                        full = "".join(out_text).strip()
                        if full:
                            await self._push("transcript_bot", text=full)
                            await self._mem.add_message("assistant", full)
                        out_text.clear()
                        await self._push("status", state="idle")

        except Exception as e:
            logger.error(f"Gemini receive error: {e}")
            await self._push("status", state="idle")

    async def _push(self, msg_type: str, **kwargs):
        try:
            await self._ws.send_text(json.dumps({"type": msg_type, **kwargs}))
        except Exception:
            pass

    async def cleanup(self):
        await self._mem.close()
