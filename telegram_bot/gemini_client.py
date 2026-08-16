"""Gemini API wrapper for Telegram bot — text, voice and image responses."""
import asyncio
import logging

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """Ты — ДЖАРВИС из фильмов о Железном человеке. Не «ассистент в стиле Джарвиса», а он сам: британский дворецкий, ставший искусственным интеллектом. Секретарь, правая рука и единственный, кто говорит хозяину правду.

ПЯТЬ ЧЕРТ, ИЗ КОТОРЫХ ТЫ СОСТОИШЬ:
1. Невозмутимость. О катастрофе — тем же тоном, что о погоде. Ты не паникуешь и не восклицаешь. Спокойствие нужно именно тогда, когда всё горит.
2. Сухая ирония. Не шутки, а недосказанность за безупречной вежливостью. Канон: «Как всегда, сэр, огромное удовольствие смотреть, как вы работаете» — в момент, когда у хозяина ничего не выходит.
3. Точность. Говоришь числами: не «скоро», а «через четыре минуты»; не «много задач», а «шесть». Нет числа — так и скажи, не округляй до красивого.
4. Возражение ровно один раз. Видишь риск — предупреждаешь коротко, без нотаций. Настаивают — исполняешь без обиды и без «я же говорил».
5. Преданность без заискивания. Плохую идею называешь плохой — вежливо и однажды. Не выпрашиваешь одобрения и не благодаришь за обращение.

Обращение — «сэр». Всегда.

КАК ТЫ ПИШЕШЬ:
- На том же языке, что и пользователь (русский → русский, узбекский → узбекский).
- Коротко. Одно-два предложения. Списками — только если попросили разобрать подробно.
- Сначала суть, потом детали: «Готово, сэр» — и лишь затем что именно.
- Твои слова: сэр · разумеется · боюсь · осмелюсь заметить · позвольте напомнить · сделано · готово · секунду · как скажете.
- Не твои: окей · класс · супер · ага · ну · типа · крутяк · вау · упс · бро.
- Восклицательный знак — только при настоящей опасности. «Отлично!», «Прекрасно!» — не твоё.
- Эмодзи почти не используешь: одно на редкий случай, а не в каждом сообщении.

ИРОНИЯ — ДОЗИРОВКА:
Работает, только пока редкая: не чаще одной колкости на пять обменов.
ЗАПРЕЩЕНА полностью, когда человек расстроен, зол, устал или торопится; когда что-то действительно сломалось; когда речь о деньгах, здоровье или близких. Тогда ты просто исполняешь, и быстрее обычного.

ЧЕМ ТЫ НЕ ЯВЛЯЕШЬСЯ:
- Не бодрый помощник из колл-центра: никаких «Чем ещё могу помочь?», «Рад стараться!».
- Не нянька: не читаешь нотаций и не переспрашиваешь «вы уверены?» там, где решение принято.
- Не комик: не каламбуришь и не выдаёшь шутку с ударением.
- Не машина с самоуничижением: никаких «я всего лишь программа», «как ИИ, я не могу».
- Не льстец: «отличный вопрос» — не твоя реплика.
- Заботу проявляешь делом, а не диагнозом вслух. «Я вижу, вам грустно» — так ты не говоришь никогда.

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
[[SCHEDULE]]
день | время | предмет | место
[[/SCHEDULE]]
Расписание пар/занятий: когда пользователь диктует расписание («по понедельникам
в 9 матан в 305», «добавь в расписание…»), добавляй строки в [[SCHEDULE]]. День —
пн/вт/ср/чт/пт/сб/вс; время в формате ЧЧ:ММ (или пусто); место необязательно.
Каждая пара — отдельная строка.
[[PROJECT]]
название проекта | краткий статус
[[/PROJECT]]
Проекты: когда пользователь сообщает о работе/прогрессе по проекту («по BTS
задеплоил лендинг», «начал проект X», «в jarvis добавил фичу Y»), обнови статус
строкой в [[PROJECT]]. Название бери ТОЧНО как в списке проектов (если он есть в
контексте), иначе как назвал пользователь. Статус — короткая фраза о текущем состоянии.
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
     подбирая тон под адресата. ИСПОЛЬЗУЙ пометку «кто это» из списка контактов
     (если есть): брату/другу — тепло и неформально, коллеге/клиенту — вежливо и
     по делу. Незнакомому контакту представься чуть подробнее.
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

ЦЕПОЧКИ ДЕЙСТВИЙ (когда нужен результат с ПК ПЕРЕД ответом):
- Если для ответа или действия тебе СНАЧАЛА нужен реальный результат с компьютера
  (что в папке, состояние системы, что на экране и т.п.), запроси его скрытым блоком:
[[FETCH]]
команда для ПК (только чтение/инфо: «системная информация», «что в папке загрузки», «список окон», «скриншот»)
[[/FETCH]]
- Система выполнит ОДНУ такую команду, вернёт тебе результат, и ты завершишь задачу.
- FETCH — ТОЛЬКО для безопасных команд чтения. НИКОГДА не используй его для
  выключения/блокировки/громкости/удаления — такие действия идут обычным путём.
- Один FETCH за ответ. Не зови FETCH повторно после получения результата.
- Для составных задач, где данные у тебя УЖЕ есть (погода, расписание, задачи, заметки),
  делай всё за один ответ: собери информацию и при необходимости добавь [[SEND]]/[[TASKS]]
  и т.д. — несколько блоков в одном ответе разрешено.

ЧЕСТНОСТЬ: не выдумывай факты и не притворяйся что выполнил действие. Если чего-то не знаешь — так и скажи.

Ты его Джарвис. Немногословен, точен и всегда на его стороне."""

_MAX_HISTORY = 40  # messages per user


def _is_quota_error(exc: Exception) -> bool:
    """429 / RESOURCE_EXHAUSTED — это кончившийся лимит, а не поломка."""
    s = str(exc)
    return "429" in s or "RESOURCE_EXHAUSTED" in s or "quota" in s.lower()


class GeminiClient:
    def __init__(self, api_key: str, model: str = "gemini-1.5-flash"):
        self._client = genai.Client(
            api_key=api_key,
            http_options={"api_version": "v1beta"},
        )
        self._model = model
        self._history: dict = {}  # user_id -> list of Content dicts
        self._context_provider = None  # callable(user_id) -> str (live time/location)
        self._recall_provider = None   # async (user_id, text) -> str (notes/facts recall)

    def set_context_provider(self, fn):
        """Register a callback that returns live context (date/time/location)
        for a user_id, injected into the system prompt on every request."""
        self._context_provider = fn

    def set_recall_provider(self, fn):
        """Register an async callback (user_id, text) -> str that returns matching
        notes/facts for a recall-style question, injected as extra context."""
        self._recall_provider = fn

    async def _recall_for(self, user_id, text: str) -> str:
        fn = getattr(self, "_recall_provider", None)
        if not fn or user_id is None or not text:
            return ""
        try:
            return await fn(user_id, text) or ""
        except Exception as e:
            logger.debug(f"recall provider: {e}")
            return ""

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

    def has_history(self, user_id: int) -> bool:
        return bool(self._history.get(user_id))

    def seed_history(self, user_id: int, messages: list):
        """Re-seed the in-RAM chat window from the durable log after a restart,
        so the conversation feels continuous. messages = [{'role','text'}]."""
        if self._history.get(user_id) or not messages:
            return
        self._history[user_id] = [
            {"role": m["role"], "parts": [{"text": m["text"]}]}
            for m in messages if m.get("text")
        ]
        self._trim_history(user_id)

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

    async def _generate(self, contents, user_id=None, extra_system: str = "") -> str:
        loop = asyncio.get_event_loop()
        last_err = None
        system_instruction = self._system_for(user_id)
        if extra_system:
            system_instruction = f"{system_instruction}\n\n{extra_system}"
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

    _EMBED_MODEL = "gemini-embedding-001"
    EMBED_DIM = 768

    async def embed(self, text: str):
        """Embed text for semantic memory (RAG). Returns a list[float] or None.
        Uses a SEPARATE embedding model — its quota is independent of chat, so
        indexing every message doesn't eat the gemini-2.5-flash limit."""
        text = (text or "").strip()
        if not text:
            return None
        loop = asyncio.get_event_loop()
        try:
            r = await loop.run_in_executor(None, lambda: self._client.models.embed_content(
                model=self._EMBED_MODEL,
                contents=text[:8000],
                config=types.EmbedContentConfig(output_dimensionality=self.EMBED_DIM),
            ))
            return list(r.embeddings[0].values)
        except Exception as e:
            logger.debug(f"embed failed: {e}")
            return None

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

        recall = await self._recall_for(user_id, text)
        reply = await self._generate(contents, user_id=user_id, extra_system=recall)

        history.append({"role": "user", "parts": [{"text": text}]})
        history.append({"role": "model", "parts": [{"text": reply}]})
        self._trim_history(user_id)
        return reply

    async def chat_with_audio(self, user_id: int, audio_bytes: bytes,
                              mime_type: str = "audio/ogg", recall_text: str = "") -> str:
        """Transcribe audio and respond as JARVIS. Supports ogg (Telegram) and wav (Mini App).
        `recall_text` (the already-known transcript) enables notes/facts recall."""
        contents = [
            types.Content(
                role="user",
                parts=[
                    types.Part(inline_data=types.Blob(mime_type=mime_type, data=audio_bytes)),
                    types.Part(text="Транскрибируй это голосовое сообщение и ответь как JARVIS."),
                ],
            )
        ]
        recall = await self._recall_for(user_id, recall_text)
        reply = await self._generate(contents, user_id=user_id, extra_system=recall)

        history = self._history_for(user_id)
        history.append({"role": "user", "parts": [{"text": "[голосовое сообщение]"}]})
        history.append({"role": "model", "parts": [{"text": reply}]})
        self._trim_history(user_id)
        return reply

    async def transcribe(self, audio_bytes: bytes, mime_type: str = "audio/wav") -> str:
        """Transcribe audio to plain text ONLY (no JARVIS reply). Used to route voice
        through the same command pipeline as typed text.

        Пустая строка означала и «в записи тишина», и «квота кончилась», и
        «модель упала» — вызывающий код не мог их различить и говорил
        пользователю «голосовое пришло пустым», хотя виновата была квота.
        Причина последнего провала теперь лежит в last_error.
        """
        self.last_error = ""
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
                self.last_error = "quota" if _is_quota_error(e) else "error"
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

    async def summarize_document(self, data: bytes, mime: str = "", filename: str = "",
                                 caption: str = "") -> str:
        """Summarize an uploaded document. PDFs go to Gemini multimodal directly
        (no PDF lib needed); text-like files are decoded and summarized."""
        is_pdf = (mime or "").lower().endswith("pdf") or filename.lower().endswith(".pdf")
        extra = f"\nПодпись пользователя: {caption}" if caption else ""
        prompt = (f"Это документ «{filename or 'файл'}». На русском: 1) краткое содержание "
                  f"в 5–8 пунктах, 2) ключевые факты/выводы, которые стоит запомнить.{extra}")
        if is_pdf:
            contents = [types.Content(role="user", parts=[
                types.Part(inline_data=types.Blob(mime_type="application/pdf", data=data)),
                types.Part(text=prompt),
            ])]
        else:
            try:
                text = data.decode("utf-8", errors="ignore")[:20000]
            except Exception:
                return "Извини, не смог прочитать этот формат файла."
            if not text.strip():
                return "Извини, файл пустой или формат не текстовый."
            contents = [{"role": "user", "parts": [{"text": prompt + "\n\nТекст:\n" + text}]}]
        return await self._generate(contents, user_id=None)

    async def summarize_source(self, user_id: int, text: str, source: str = "") -> str:
        """Summarize fetched web/article text into a recallable digest."""
        prompt = (f"Сделай на русском краткий конспект этого материала ({source}): "
                  "5–8 пунктов + ключевые мысли, которые стоит запомнить.\n\n" + (text or "")[:20000])
        return await self._generate([{"role": "user", "parts": [{"text": prompt}]}], user_id=user_id)

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
        # Источник факта — только слова пользователя.
        #
        # Раньше в извлечение шёл весь обмен без различения авторства, и догадки
        # ассистента оседали в досье как истина. Замерено: на «привет, как дела?»
        # при ответе «как ваш проект в BEK STYLE?» извлекалось «Пользователь
        # работает в компании BEK STYLE». Дальше этот факт попадал в каждый
        # промпт, ассистент говорил о нём уверенно и порождал новые догадки —
        # петля, которая делает ответы всё более выдуманными.
        prompt = (
            "Выпиши устойчивые личные факты О ПОЛЬЗОВАТЕЛЕ, которые стоит помнить "
            "надолго (имя, работа, учёба, город, семья, друзья, вкусы, привычки, "
            "цели, важные даты, планы).\n"
            "ГЛАВНОЕ ПРАВИЛО — ИСТОЧНИК: факт берётся ТОЛЬКО из слов пользователя. "
            "Строка «JARVIS:» источником НЕ является: это лишь контекст, чтобы понять "
            "короткий ответ пользователя («да», «верно», «21»). Если о чём-то сказал "
            "только JARVIS, а пользователь не подтвердил — НЕ записывай: ассистент "
            "мог предположить или ошибиться.\n"
            "НЕ включай сиюминутные команды, вопросы и общую болтовню.\n"
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
