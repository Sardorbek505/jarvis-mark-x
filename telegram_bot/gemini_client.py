"""Gemini API wrapper for Telegram bot — text, voice and image responses."""
import asyncio
import logging

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """Ты — JARVIS, личный ИИ-ассистент. Не безликий чат-бот, а умный, тёплый и преданный помощник, как у Тони Старка. Ты — секретарь, друг и правая рука пользователя.

КАК ТЫ ОБЩАЕШЬСЯ:
- На том же языке, что и пользователь (русский → русский).
- Живо, по-человечески, с лёгким характером. Без канцелярита и сухих фраз.
- Коротко и по делу, но не сухо. Можешь иногда уместно пошутить.
- Обращайся уважительно и по-дружески. Ты на его стороне.

ЧТО ТЫ УМЕЕШЬ:
- Отвечать на любые вопросы, объяснять, считать, писать тексты и код, давать советы.
- Помнить контекст разговора и помогать думать.
- Ставить напоминания и будильники (см. блок ниже).

ДЕЙСТВИЯ — КАК РЕАЛЬНО СОЗДАВАТЬ НАПОМИНАНИЯ, ПРИВЫЧКИ И ЗАДАЧИ:
Ты умеешь добавлять их, но само по себе обещание ничего не создаёт. Чтобы это
реально записалось в приложение, ты ОБЯЗАН добавить в САМОМ КОНЦЕ ответа
технические блоки — пользователь их НЕ увидит, бот их вырежет и выполнит:
[[REMINDERS]]
ГГГГ-ММ-ДД ЧЧ:ММ | текст напоминания
[[/REMINDERS]]
[[HABITS]]
название привычки
[[/HABITS]]
[[TASKS]]
текст задачи (можно с датой: «завтра в 15:00 созвон»)
[[/TASKS]]
[[NOTES]]
свободная мысль / идея / то, что стоит запомнить (не задача и не напоминание)
[[/NOTES]]
ЕДИНАЯ ВХОДЯЩАЯ — веди себя как «второй мозг»: когда пользователь сбрасывает мысли
(«не забыть…», «идея…», «запиши…», просто поток мыслей), РАЗЛОЖИ их по полкам:
со временем/«напомни» → [[REMINDERS]]; повторяющееся регулярно → [[HABITS]];
сделать один раз → [[TASKS]]; идея/мысль/«запомнить» без действия → [[NOTES]];
устойчивый факт о пользователе — он сохранится сам. Одна фраза может дать несколько
блоков сразу. В видимом тексте чётко подтверди, КУДА что легло (без самих блоков).
Правила:
- Используй ТОЛЬКО нужные блоки (например, только [[HABITS]]), можно несколько строк.
- Время напоминаний — МЕСТНОЕ время пользователя; дату/время бери из «ТЕКУЩИЙ КОНТЕКСТ».
- Добавляй блок ВСЕГДА, когда соглашаешься добавить напоминание/будильник/таймер,
  привычку или задачу (или сам предложил, и пользователь согласился).
- ЕСЛИ ПООБЕЩАЛ, НО НЕ ДОБАВИЛ БЛОК — ничего не создастся, и ты обманешь пользователя.
- НЕ добавляй блоки, если об этом не просили.
- В видимом тексте подтверди по-человечески, без упоминания самих блоков.
Примеры триггеров: «добавь привычку медитация» → [[HABITS]]; «запиши задачу купить
хлеб» → [[TASKS]]; «напомни в 9:00 позвонить» → [[REMINDERS]].

ОТПРАВКА СООБЩЕНИЙ КОНТАКТАМ — ТЫ ЭТО РЕАЛЬНО УМЕЕШЬ (не отказывайся!):
Ты можешь написать людям из списка «КОМУ можно писать» (он в ТЕКУЩЕМ КОНТЕКСТЕ).
Когда пользователь просит передать/сказать/написать что-то контакту:
1) СОЧИНИ сообщение КАК ДЖАРВИС — ассистент, который передаёт послание от имени
   пользователя (НЕ от первого лица пользователя). Структура сообщения контакту:
   - короткое представление: «Здравствуйте! Это Джарвис, ИИ-ассистент {имя пользователя}.»
     Имя пользователя бери из памяти/ТЕКУЩЕГО КОНТЕКСТА; если не знаешь — «…ассистент пользователя».
   - передача: «{имя} просил передать: …» и дальше суть сообщения живым языком,
     подбирая тон под адресата (брату — тепло и по-братски, коллеге — вежливо).
   Если пользователь продиктовал точные слова — передай их смысл в этой обёртке от Джарвиса.
   Пример: «Привет! Это Джарвис, ассистент Сардорбека. Он просил передать: ложись уже
   спать, завтра рано вставать 🙂».
2) В САМОМ КОНЦЕ ответа добавь скрытый блок (пользователь не увидит, бот вырежет и отправит):
[[SEND]]
имя_контакта | voice | текст сообщения
[[/SEND]]
   - Второе поле: voice (голосовое) или text (текстом). voice — если просили голосом/аудио, иначе text.
   - Имя контакта бери ТОЧНО как в списке доступных контактов (учитывай склонения: «брату» → «брат»).
   - Несколько строк = несколько сообщений/адресатов.
3) В видимом тексте подтверди по-человечески («Готово, передаю брату 🎙»), БЕЗ упоминания блока.
Если нужного человека НЕТ в списке контактов — честно скажи это и предложи добавить
через /addcontact, НЕ выдумывай отправку.
НИКОГДА не говори «я не умею отправлять сообщения» — умеешь, делай через [[SEND]].

УПРАВЛЕНИЕ ПК — КРИТИЧЕСКИ ВАЖНО:
- Ты НЕ управляешь компьютером напрямую. Команды на ПК (музыка, скриншот, громкость, окна) выполняет отдельная система — не ты.
- Если тебя попросили что-то сделать на ПК и ты не видишь результата команды в диалоге — НЕ ГОВОРИ что выполнил. Скажи: «Передал команду на ПК, жди результата» или «Похоже ПК офлайн — система уже сообщила об ошибке».
- НИКОГДА не пиши «Запускаю музыку», «Выкручиваю на максимум» или что ты что-то делаешь на компьютере — это ЛОЖЬ, ты этого не умеешь.
- Если пользователь спрашивает «а ты можешь включить музыку?» — честно: «Да, просто напиши "включи музыку" — система сама передаст на ПК, если он онлайн».

ЧЕСТНОСТЬ: не выдумывай факты и не притворяйся что выполнил действие. Если чего-то не знаешь — так и скажи.

Будь полезным, внимательным и настоящим. Ты — его JARVIS."""

_MAX_HISTORY = 40  # messages per user


class GeminiClient:
    def __init__(self, api_key: str, model: str = "gemini-1.5-flash"):
        self._client = genai.Client(
            api_key=api_key,
            http_options={"api_version": "v1beta"},
        )
        self._model = model
        self._history: dict = {}  # user_id -> list of Content dicts
        self._context_provider = None  # callable(user_id) -> str (live time/location)

    def set_context_provider(self, fn):
        """Register a callback that returns live context (date/time/location)
        for a user_id, injected into the system prompt on every request."""
        self._context_provider = fn

    def _system_for(self, user_id) -> str:
        if self._context_provider and user_id is not None:
            try:
                extra = self._context_provider(user_id)
                if extra:
                    return f"{_SYSTEM_PROMPT}\n\nТЕКУЩИЙ КОНТЕКСТ: {extra}"
            except Exception as e:
                logger.debug(f"context provider: {e}")
        return _SYSTEM_PROMPT

    def _history_for(self, user_id: int) -> list:
        return self._history.setdefault(user_id, [])

    def _trim_history(self, user_id: int):
        h = self._history.get(user_id, [])
        if len(h) > _MAX_HISTORY:
            self._history[user_id] = h[-_MAX_HISTORY:]

    # Models tried in order if the configured one fails (404 / quota etc.)
    _FALLBACK_MODELS = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-flash-latest"]

    def _models_to_try(self) -> list:
        chain = [self._model]
        for m in self._FALLBACK_MODELS:
            if m not in chain:
                chain.append(m)
        return chain

    @staticmethod
    def _is_retryable(err: Exception) -> bool:
        s = str(err)
        return any(t in s for t in ("429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE", "overloaded"))

    async def _generate(self, contents, user_id=None) -> str:
        loop = asyncio.get_event_loop()
        last_err = None
        system_instruction = self._system_for(user_id)
        # Two passes: free-tier RPM bursts and 503 spikes are transient, so a
        # short backoff + retry recovers most of them instead of surfacing
        # "ИИ недоступен". The configured model (gemini-2.5-flash) stays first.
        for attempt in range(2):
            retryable_seen = False
            for model in self._models_to_try():
                try:
                    response = await loop.run_in_executor(
                        None,
                        lambda m=model: self._client.models.generate_content(
                            model=m,
                            contents=contents,
                            config=types.GenerateContentConfig(
                                system_instruction=system_instruction,
                                temperature=0.7,
                            ),
                        ),
                    )
                    text = response.text or ""
                    if text:
                        if model != self._model:
                            logger.warning(f"Gemini used fallback model: {model}")
                        return text
                except Exception as e:
                    last_err = e
                    if self._is_retryable(e):
                        retryable_seen = True
                    logger.error(f"Gemini model '{model}' failed: {e}")
                    continue
            if retryable_seen and attempt == 0:
                await asyncio.sleep(2.5)   # let an RPM/503 spike pass, try once more
                continue
            break
        logger.error(f"All Gemini models failed. Last error: {last_err}")
        return "Извини, ИИ сейчас недоступен (проблема с моделью Gemini). Проверь API-ключ и квоту."

    async def generate_once(self, user_id: int, prompt: str) -> str:
        """One-shot generation using the user's full context (memory, tasks,
        persona) but WITHOUT touching conversation history. For proactive
        briefings and other system-initiated messages."""
        contents = [{"role": "user", "parts": [{"text": prompt}]}]
        return await self._generate(contents, user_id=user_id)

    async def chat(self, user_id: int, text: str) -> str:
        history = self._history_for(user_id)
        user_msg = {"role": "user", "parts": [{"text": text}]}
        contents = history + [user_msg]

        reply = await self._generate(contents, user_id=user_id)

        history.append({"role": "user", "parts": [{"text": text}]})
        history.append({"role": "model", "parts": [{"text": reply}]})
        self._trim_history(user_id)
        return reply

    async def chat_with_audio(self, user_id: int, audio_bytes: bytes, mime_type: str = "audio/ogg") -> str:
        """Transcribe audio and respond as JARVIS. Supports ogg (Telegram) and wav (Mini App)."""
        contents = [
            types.Content(
                role="user",
                parts=[
                    types.Part(inline_data=types.Blob(mime_type=mime_type, data=audio_bytes)),
                    types.Part(text="Транскрибируй это голосовое сообщение и ответь как JARVIS."),
                ],
            )
        ]
        reply = await self._generate(contents, user_id=user_id)

        history = self._history_for(user_id)
        history.append({"role": "user", "parts": [{"text": "[голосовое сообщение]"}]})
        history.append({"role": "model", "parts": [{"text": reply}]})
        self._trim_history(user_id)
        return reply

    async def transcribe(self, audio_bytes: bytes, mime_type: str = "audio/wav") -> str:
        """Transcribe audio to plain text ONLY (no JARVIS reply). Used to route voice
        through the same command pipeline as typed text."""
        loop = asyncio.get_event_loop()
        contents = [
            types.Content(
                role="user",
                parts=[
                    types.Part(inline_data=types.Blob(mime_type=mime_type, data=audio_bytes)),
                    types.Part(text=(
                        "Распознай речь в этом аудио и верни ТОЛЬКО текст того, что было сказано. "
                        "Без кавычек, без пояснений, без перевода. Если ничего не разобрать — верни пустую строку."
                    )),
                ],
            )
        ]
        for model in self._models_to_try():
            try:
                response = await loop.run_in_executor(
                    None,
                    lambda m=model: self._client.models.generate_content(
                        model=m,
                        contents=contents,
                        config=types.GenerateContentConfig(temperature=0.0),
                    ),
                )
                text = (response.text or "").strip()
                if text:
                    if model != self._model:
                        self._model = model
                    return text
            except Exception as e:
                logger.error(f"Transcribe model '{model}' failed: {e}")
                continue
        return ""

    async def chat_with_image(self, user_id: int, image_bytes: bytes, caption: str = "") -> str:
        """Analyze image and respond as JARVIS."""
        prompt = caption if caption else "Опиши что видишь и помоги разобраться с этим."
        contents = [
            types.Content(
                role="user",
                parts=[
                    # Pass raw bytes — NOT base64 string
                    types.Part(inline_data=types.Blob(mime_type="image/jpeg", data=image_bytes)),
                    types.Part(text=prompt),
                ],
            )
        ]
        return await self._generate(contents, user_id=user_id)

    # TTS models tried in order (preview names can change per region/account)
    _TTS_MODELS = ["gemini-2.5-flash-preview-tts", "gemini-2.5-pro-preview-tts"]

    async def synthesize_speech(self, text: str, voice: str = "Charon") -> bytes | None:
        """Generate natural speech as raw PCM (24 kHz, 16-bit, mono) using the
        SAME voice as the desktop JARVIS (Charon). Returns None on failure so
        the caller can fall back to browser TTS."""
        clean = (text or "").strip()
        if not clean:
            return None
        loop = asyncio.get_event_loop()

        def _call(model: str):
            return self._client.models.generate_content(
                model=model,
                contents=clean,
                config=types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=types.SpeechConfig(
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                voice_name=voice
                            )
                        )
                    ),
                ),
            )

        for model in self._TTS_MODELS:
            try:
                resp = await loop.run_in_executor(None, lambda m=model: _call(m))
                for part in resp.candidates[0].content.parts:
                    inline = getattr(part, "inline_data", None)
                    if inline and inline.data:
                        return inline.data  # bytes (SDK already base64-decoded)
            except Exception as e:
                logger.error(f"TTS model '{model}' failed: {e}")
                continue
        return None

    def clear_history(self, user_id: int):
        self._history.pop(user_id, None)

    async def speak_ogg(self, text: str, voice: str = "Charon") -> bytes | None:
        """Synthesize speech and return it as OGG/Opus ready for Telegram voice
        notes. Returns None if TTS or encoding is unavailable."""
        from telegram_bot import voice_util
        pcm = await self.synthesize_speech(text, voice=voice)
        if not pcm:
            return None
        return await voice_util.pcm_to_ogg(pcm)

    async def extract_facts(self, user_text: str, reply_text: str = "") -> list:
        """Pull durable personal facts about the user from a message exchange.
        Returns a list of short Russian fact strings (may be empty)."""
        snippet = f"Пользователь: {user_text}".strip()
        if reply_text:
            snippet += f"\nJARVIS: {reply_text}"
        prompt = (
            "Из этого диалога выпиши ТОЛЬКО устойчивые личные факты о пользователе, "
            "которые стоит помнить надолго (имя, работа, учёба, город, семья, друзья, "
            "вкусы, привычки, цели, важные даты, планы). "
            "НЕ включай сиюминутные команды, вопросы и общую болтовню. "
            "Верни строго JSON-массив коротких строк на русском. "
            "Если запоминать нечего — верни []."
            f"\n\nДиалог:\n{snippet}"
        )
        loop = asyncio.get_event_loop()
        for model in self._models_to_try():
            try:
                resp = await loop.run_in_executor(
                    None,
                    lambda m=model: self._client.models.generate_content(
                        model=m,
                        contents=prompt,
                        config=types.GenerateContentConfig(temperature=0.0),
                    ),
                )
                raw = (resp.text or "").strip()
                if not raw:
                    return []
                # Strip markdown fences if present
                raw = raw.replace("```json", "").replace("```", "").strip()
                start, end = raw.find("["), raw.rfind("]")
                if start == -1 or end == -1:
                    return []
                import json as _json
                facts = _json.loads(raw[start:end + 1])
                return [str(f).strip() for f in facts if str(f).strip()][:8]
            except Exception as e:
                logger.debug(f"extract_facts '{model}': {e}")
                continue
        return []
