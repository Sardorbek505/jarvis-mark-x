/* J.A.R.V.I.S Mini App — клиент.
 *
 * Тот же Джарвис, что на ПК: шар из точек (orb.js) меняет цвет по состоянию
 * и фигуру по теме запроса, ответ идёт субтитрами под шаром. Разговор
 * хранится на сервере — приложение открывается с историей. Без связи
 * сообщения ждут в очереди и уходят, когда она вернётся.
 */
'use strict';

const tg = window.Telegram?.WebApp;
if (tg) {
  tg.ready();
  tg.expand();
  try { tg.setHeaderColor('#030609'); tg.setBackgroundColor('#030609'); tg.setBottomBarColor?.('#070c11'); } catch {}
  tg.disableVerticalSwipes?.();       // свайп вниз по шару не закрывает приложение
}
function haptic(kind = 'light') { try { tg?.HapticFeedback?.impactOccurred(kind); } catch {} }
function notifyHaptic(kind) { try { tg?.HapticFeedback?.notificationOccurred(kind); } catch {} }

const $ = (id) => document.getElementById(id);
const wsProto = location.protocol === 'https:' ? 'wss:' : 'ws:';
const WS_URL = `${wsProto}//${location.host}/ws?init_data=${encodeURIComponent(tg?.initData ?? '')}`;

const view = $('view-chat'), stage = $('stage'), msgs = $('messages'), chatBox = $('chat');
const textIn = $('text-in'), sendBtn = $('send-btn'), micBtn = $('mic-btn');
const pcBadge = $('pc-badge'), connBadge = $('conn-badge');
const statePill = $('state-pill'), subtitle = $('subtitle');

// ── Шар ───────────────────────────────────────────────────────────────────────
const orbView = new window.JarvisOrb.OrbView($('orb-canvas'));
const STATE_LABEL = {
  idle: 'ГОТОВ', listening: 'СЛУШАЮ', processing: 'ДУМАЮ', speaking: 'ГОВОРЮ', offline: 'НЕТ СВЯЗИ', boot: 'ЗАПУСК',
};
let uiState = 'boot';
function setState(name) {
  if (!STATE_LABEL[name]) name = 'idle';
  if (!online && name === 'idle') name = 'offline';
  uiState = name;
  orbView.setState(name);
  statePill.querySelector('span').textContent = STATE_LABEL[name];
  const [r, g, b] = window.JarvisOrb.STATE_RGB[name];
  document.documentElement.style.setProperty('--state', `${r}, ${g}, ${b}`);
  micBtn.classList.toggle('listening', name === 'listening');
  stage.classList.toggle('talking', name !== 'idle' && name !== 'offline');
  if (name === 'idle') releaseShapeSoon();
}

// Фигура держится, пока Джарвис работает над ответом, и ещё немного после.
let shapeTimer = null;
function shapeFor(text) {
  const shape = window.JarvisOrb.topicShape(text);
  if (!shape) return null;
  clearTimeout(shapeTimer);
  orbView.setShape(shape);
  return shape;
}
function releaseShapeSoon(ms = 6000) {
  clearTimeout(shapeTimer);
  shapeTimer = setTimeout(() => orbView.setShape('sphere'), ms);
}

// Субтитры: ответ под шаром, печатается по ходу речи.
let subTimer = null, subTyping = null;
function showSubtitle(text, msPerChar = 38) {
  clearTimeout(subTimer); clearInterval(subTyping);
  const clean = cleanForSpeech(text).slice(0, 260);
  if (!clean) return;
  let i = 0;
  subtitle.textContent = '';
  subtitle.classList.add('on');
  subTyping = setInterval(() => {
    i = Math.min(clean.length, i + 2);
    subtitle.textContent = clean.slice(0, i);
    if (i >= clean.length) { clearInterval(subTyping); hideSubtitleSoon(); }
  }, msPerChar * 2);
}
function hideSubtitleSoon(ms = 6000) {
  clearTimeout(subTimer);
  subTimer = setTimeout(() => subtitle.classList.remove('on'), ms);
}

// ── Сообщения ─────────────────────────────────────────────────────────────────
function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// Простое форматирование ответа: **жирный**, `код`, ссылки, списки.
// Сначала экранируем всё — разметка добавляется только нашими тегами.
function renderRich(text) {
  const lines = esc(text).split('\n').map(line => {
    let l = line
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
      .replace(/(^|\s)\*([^*\s][^*]*)\*(?=\s|$|[.,!?])/g, '$1<b>$2</b>')
      .replace(/(https?:\/\/[^\s<]+[^\s<.,!?)])/g, '<a href="$1" target="_blank" rel="noopener">$1</a>');
    const li = l.match(/^\s*(?:[-•*]|\d+[.)])\s+(.*)$/);
    return li ? `<span class="li">${li[1]}</span>` : l;
  });
  return lines.join('\n').replace(/<\/span>\n/g, '</span>');
}

function exitEmpty() { view.classList.remove('is-empty'); }
function scrollDown() { chatBox.scrollTop = chatBox.scrollHeight; }

function addMsg(role, text, { rich = role === 'bot', cls = '' } = {}) {
  removeTyping();
  if (role !== 'sys') exitEmpty();
  const d = document.createElement('div');
  d.className = `msg ${role} ${cls}`.trim();
  if (rich) d.innerHTML = renderRich(text); else d.textContent = text;
  msgs.appendChild(d);
  scrollDown();
  return d;
}

function addImage(b64, caption) {
  removeTyping();
  exitEmpty();
  const wrap = document.createElement('div');
  wrap.className = 'msg bot img';
  const img = document.createElement('img');
  img.src = `data:image/jpeg;base64,${b64}`;
  img.alt = caption || 'Снимок';
  img.addEventListener('load', scrollDown);
  wrap.appendChild(img);
  if (caption) {
    const cap = document.createElement('div');
    cap.className = 'cap';
    cap.textContent = caption;
    wrap.appendChild(cap);
  }
  msgs.appendChild(wrap);
  scrollDown();
}

function showTyping() {
  removeTyping();
  const d = document.createElement('div');
  d.className = 'msg bot typing';
  d.innerHTML = '<span></span><span></span><span></span>';
  msgs.appendChild(d);
  scrollDown();
}
function removeTyping() { msgs.querySelectorAll('.typing').forEach(el => el.remove()); }

function renderHistory(list) {
  if (!list?.length || msgs.querySelector('.msg.user, .msg.bot')) return;
  const sep = document.createElement('div');
  sep.className = 'day-sep';
  sep.textContent = 'ранее';
  msgs.appendChild(sep);
  for (const m of list) addMsg(m.role === 'user' ? 'user' : 'bot', m.text, { rich: m.role !== 'user' });
}

function showToast(text, type = 'info', action = null) {
  const box = $('toast-container');
  const t = document.createElement('div');
  t.className = `toast ${type}`;
  const s = document.createElement('span');
  s.textContent = text;
  t.appendChild(s);
  if (action) {
    const b = document.createElement('button');
    b.className = 'toast-action';
    b.textContent = action.label;
    b.onclick = () => { action.onClick(); t.remove(); };
    t.appendChild(b);
  }
  box.appendChild(t);
  setTimeout(() => { t.classList.add('hide'); setTimeout(() => t.remove(), 350); }, 3800);
}

// ── Связь ─────────────────────────────────────────────────────────────────────
let ws = null, online = false, retry = 0, retryTimer = null;
const outbox = [];                    // сообщения, набранные без связи

function connect() {
  clearTimeout(retryTimer);
  if (ws) {                 // старое соединение закрываем без его onclose —
    ws.onclose = null;      // иначе оно запланировало бы ещё одно переподключение
    try { ws.close(); } catch {}
  }
  ws = new WebSocket(WS_URL);

  ws.onopen = () => {
    online = true; retry = 0;
    connBadge.className = 'badge online';
    connBadge.title = 'Сервер: на связи';
    setState('idle');
    reportClientInfo();
    while (outbox.length && ws.readyState === WebSocket.OPEN) {
      const item = outbox.shift();
      item.el?.classList.remove('pending');
      ws.send(JSON.stringify(item.msg));
    }
  };

  ws.onclose = () => {
    online = false;
    connBadge.className = 'badge warn';
    connBadge.title = 'Сервер: переподключение…';
    pcOnline(false);
    if (micOpen) stopAll();
    setState('offline');
    // Пауза растёт: 1, 2, 4… до 15 с. Раньше — каждые 3,5 с бесконечно.
    const delay = Math.min(15000, 1000 * 2 ** retry++);
    retryTimer = setTimeout(connect, delay);
  };

  ws.onmessage = async ({ data }) => {
    let msg;
    try { msg = JSON.parse(data); } catch { return; }
    switch (msg.type) {
      case 'history':
        renderHistory(msg.messages);
        break;
      case 'text':
        addMsg('bot', msg.text);
        showSubtitle(msg.text);
        awaitVoice(msg.text);
        if (activeTab !== 'chat') showToast(cleanForSpeech(msg.text).slice(0, 120), 'info',
          { label: 'Открыть', onClick: () => switchTab('chat') });
        break;
      case 'image':
        addImage(msg.data, msg.caption);
        setState('idle');
        notifyHaptic('success');
        if (activeTab !== 'chat') showToast('📸 Снимок получен', 'success', { label: 'Открыть', onClick: () => switchTab('chat') });
        break;
      case 'transcript_user':
        if (msg.text) { addMsg('user', msg.text, { rich: false }); shapeFor(msg.text); }
        break;
      case 'transcript_bot':
        if (msg.text) { addMsg('bot', msg.text); showSubtitle(msg.text); awaitVoice(msg.text); }
        break;
      case 'audio':
        clearVoiceWait();
        window.speechSynthesis?.cancel();
        botSpeaking = true;
        await playPCM(msg.data);
        botSpeaking = false;
        setState(continuous ? 'listening' : 'idle');
        break;
      case 'tts_failed':
        clearVoiceWait();
        speak(pendingSpeech);
        break;
      case 'status':
        setState(msg.state);
        break;
      case 'pc_status':
        pcOnline(!!msg.online);
        break;
      case 'thinking':
        setState('processing');
        showTyping();
        break;
      case 'data':
        renderView(msg.view, msg.payload || {});
        break;
    }
  };
}

// Вернулись в приложение — сразу проверяем связь, не ждём таймер.
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && (!ws || ws.readyState > WebSocket.OPEN)) { retry = 0; connect(); }
});

function send(obj) {
  if (ws?.readyState === WebSocket.OPEN) { ws.send(JSON.stringify(obj)); return true; }
  return false;
}

function sendClientInfo(extra) {
  let tz = '';
  try { tz = Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch {}
  send({ type: 'client_info', tz, ...extra });
}
function reportClientInfo() {
  sendClientInfo({});
  if (!navigator.geolocation) return;
  navigator.geolocation.getCurrentPosition(async ({ coords: { latitude: lat, longitude: lon } }) => {
    let city = '';
    try {
      const r = await fetch(`https://api.bigdatacloud.net/data/reverse-geocode-client?latitude=${lat}&longitude=${lon}&localityLanguage=ru`);
      const j = await r.json();
      city = j.city || j.locality || j.principalSubdivision || '';
    } catch {}
    sendClientInfo({ lat, lon, city });
  }, () => {}, { enableHighAccuracy: false, timeout: 8000, maximumAge: 600000 });
}

// ── Ввод текстом ──────────────────────────────────────────────────────────────
textIn.addEventListener('input', () => { sendBtn.disabled = !textIn.value.trim(); });
textIn.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendText(); } });
sendBtn.addEventListener('click', () => sendText());

function say(text) {
  text = (text || '').trim();
  if (!text) return;
  haptic();
  const el = addMsg('user', text, { rich: false });
  shapeFor(text);
  const msg = { type: 'text', text, tts: voiceEnabled };
  if (send(msg)) {
    setState('processing');
    showTyping();
  } else {
    el.classList.add('pending');
    outbox.push({ msg, el });
    addMsg('sys', 'Нет связи — отправлю, как только она появится', { cls: 'warn' });
  }
}
function sendText() {
  const text = textIn.value.trim();
  if (!text) return;
  textIn.value = '';
  sendBtn.disabled = true;
  say(text);
}

// Режим шара: чат спрятан, ответ — субтитрами (как на ПК). Запоминается.
const focusBtn = $('focus-btn');
function setFocus(on) {
  view.classList.toggle('focus', on);
  focusBtn.setAttribute('aria-label', on ? 'Показать чат' : 'Режим шара: спрятать чат');
  try { localStorage.setItem('jarvis-focus', on ? '1' : '0'); } catch {}
  if (!on) scrollDown();
}
focusBtn.addEventListener('click', () => { haptic(); setFocus(!view.classList.contains('focus')); });
try { if (localStorage.getItem('jarvis-focus') === '1') setFocus(true); } catch {}

document.querySelectorAll('#chips .chip').forEach(c => c.addEventListener('click', () => say(c.dataset.say)));

// ── ПК-пульт ──────────────────────────────────────────────────────────────────
let pcIsOnline = false;
function pcOnline(on) {
  pcIsOnline = on;
  pcBadge.className = on ? 'badge online' : 'badge';
  pcBadge.title = on ? 'ПК: онлайн' : 'ПК: офлайн';
  $('view-pc').classList.toggle('offline', !on);
  $('pc-status').classList.toggle('online', on);
  document.querySelector('.pc-status-title').textContent = on ? 'ПК на связи' : 'ПК офлайн';
  document.querySelector('.pc-status-sub').textContent = on
    ? 'Команды выполняются сразу'
    : 'Запусти компьютер — или Джарвиса на нём кнопкой ниже';
}

document.querySelectorAll('#view-pc [data-cmd], #view-pc [data-say]').forEach(btn => {
  btn.addEventListener('click', () => {
    const cmd = btn.dataset.cmd || btn.dataset.say;
    const launch = btn.classList.contains('pc-launch');
    if (!online) { showToast('Нет связи с сервером', 'error'); notifyHaptic('error'); return; }
    if (!pcIsOnline && !launch && btn.dataset.cmd) {
      showToast('ПК офлайн — команда не дойдёт', 'error'); notifyHaptic('warning'); return;
    }
    haptic('medium');
    btn.classList.add('sent');
    setTimeout(() => btn.classList.remove('sent'), 700);
    showToast('🖥 ' + btn.textContent.trim());
    addMsg('user', cmd, { rich: false });
    if (!shapeFor(cmd)) { clearTimeout(shapeTimer); orbView.setShape('reactor'); }
    send({ type: 'text', text: cmd, tts: voiceEnabled && !!btn.dataset.say });
    setState('processing');
  });
});

// ── Голос: зажми — говори, тап — диалог без рук ───────────────────────────────
let recCtx = null, playCtx = null, analyser = null, workletNode = null, micStream = null, spNode = null;
let micOpen = false, continuous = false, botSpeaking = false, pressTs = 0;
const TAP_MS = 350, VAD_THRESH = 0.016, VAD_HANG_MS = 1100;
let vadSpoke = false, vadSilence = 0;

for (const el of [$('orb-canvas'), micBtn]) {
  el.addEventListener('pointerdown', onPressDown);
  el.addEventListener('pointerup', onPressUp);
  el.addEventListener('pointercancel', onPressUp);
  el.addEventListener('contextmenu', e => e.preventDefault());
}

async function onPressDown(e) {
  e.preventDefault();
  if (!online) { showToast('Нет связи с сервером', 'error'); return; }
  try { e.currentTarget.setPointerCapture(e.pointerId); } catch {}
  haptic('medium');
  pressTs = Date.now();
  if (continuous) return;
  await openMic();
  if (micOpen) beginSegment();
}

function onPressUp(e) {
  e?.preventDefault();
  const dt = Date.now() - pressTs;
  if (continuous) { if (dt < TAP_MS) stopAll(); return; }
  if (!micOpen) return;
  if (dt < TAP_MS) {
    continuous = true;
    micBtn.classList.add('hands-free');
    showToast('Режим диалога: говорите, тап — стоп');
  } else {
    endSegment();
    closeMic();
  }
}

function stopAll() { endSegment(); closeMic(); }
function beginSegment() {
  vadSpoke = false; vadSilence = 0;
  setState('listening');
  micBtn.classList.add('rec');
  send({ type: 'start_voice' });
}
function endSegment() {
  micBtn.classList.remove('rec');
  setState('processing');
  send({ type: 'stop_voice', tts: voiceEnabled });
}

async function openMic() {
  if (micOpen) return;
  try {
    const AC = window.AudioContext || window.webkitAudioContext;
    recCtx = new AC();
    if (recCtx.state === 'suspended') await recCtx.resume();
    micStream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
    });
    const src = recCtx.createMediaStreamSource(micStream);
    let worklet = !!recCtx.audioWorklet?.addModule;
    if (worklet) {
      try {
        await recCtx.audioWorklet.addModule(new URL('./worklet.js', location.href).href);
        workletNode = new AudioWorkletNode(recCtx, 'pcm-processor');
        workletNode.port.onmessage = ({ data }) => onAudioChunk(data.pcm);
        src.connect(workletNode);
      } catch { worklet = false; }
    }
    if (!worklet) startScriptProcessor(src);   // iOS-вебвью без AudioWorklet
    micOpen = true;
  } catch {
    addMsg('sys', 'Нет доступа к микрофону — разреши его в настройках или пиши текстом', { cls: 'warn' });
    closeMic();
  }
}

function startScriptProcessor(src) {
  const ratio = recCtx.sampleRate / 16000, CHUNK = 1600;
  let acc = [];
  spNode = recCtx.createScriptProcessor(4096, 1, 1);
  spNode.onaudioprocess = (e) => {
    const ch = e.inputBuffer.getChannelData(0);
    for (let i = 0; i < ch.length; i += ratio) {
      const lo = ch[Math.floor(i)] ?? 0, hi = ch[Math.min(Math.ceil(i), ch.length - 1)] ?? lo;
      acc.push(lo + (hi - lo) * (i % 1));
    }
    while (acc.length >= CHUNK) {
      const s = acc.splice(0, CHUNK), pcm = new Int16Array(s.length);
      for (let i = 0; i < s.length; i++) pcm[i] = Math.max(-32768, Math.min(32767, s[i] * 32767));
      onAudioChunk(pcm.buffer);
    }
  };
  const sink = recCtx.createGain();
  sink.gain.value = 0;
  src.connect(spNode); spNode.connect(sink); sink.connect(recCtx.destination);
}

function closeMic() {
  micStream?.getTracks().forEach(t => t.stop());
  workletNode?.disconnect();
  if (spNode) { spNode.onaudioprocess = null; spNode.disconnect(); spNode = null; }
  recCtx?.close().catch(() => {});
  recCtx = micStream = workletNode = null;
  micOpen = continuous = false;
  micBtn.classList.remove('rec', 'hands-free');
  orbView.level = 0;
  if (uiState === 'listening') setState('idle');
}

function onAudioChunk(buf) {
  if (ws?.readyState !== WebSocket.OPEN) return;
  if (continuous && botSpeaking) return;          // не слушаем собственный голос
  const bytes = new Uint8Array(buf);
  let bin = '';
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  ws.send(JSON.stringify({ type: 'audio', data: btoa(bin) }));
  const i16 = new Int16Array(buf);
  let sum = 0;
  for (let i = 0; i < i16.length; i++) { const v = i16[i] / 32768; sum += v * v; }
  const rms = Math.sqrt(sum / i16.length);
  orbView.level = Math.min(1, rms * 9);           // шар дышит твоим голосом
  if (!continuous) return;
  if (rms > VAD_THRESH) { vadSpoke = true; vadSilence = 0; micBtn.classList.add('rec'); }
  else if (vadSpoke && (vadSilence += 100) >= VAD_HANG_MS) { endSegment(); beginSegment(); }
}

// ── Ответ голосом ─────────────────────────────────────────────────────────────
let playChain = Promise.resolve();
const levelBuf = new Float32Array(512);
function trackPlaybackLevel() {
  if (!analyser || !botSpeaking) { orbView.level = 0; return; }
  analyser.getFloatTimeDomainData(levelBuf);
  let sum = 0;
  for (let i = 0; i < levelBuf.length; i++) sum += levelBuf[i] * levelBuf[i];
  orbView.level = Math.min(1, Math.sqrt(sum / levelBuf.length) * 5);
  requestAnimationFrame(trackPlaybackLevel);
}

async function playPCM(b64) {
  setState('speaking');
  playChain = playChain.then(async () => {
    try {
      const bin = atob(b64), bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      const s16 = new Int16Array(bytes.buffer), f32 = new Float32Array(s16.length);
      for (let i = 0; i < s16.length; i++) f32[i] = s16[i] / 32768;
      if (!playCtx || playCtx.state === 'closed') {
        playCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 24000 });
        analyser = playCtx.createAnalyser();
        analyser.fftSize = 1024;
        analyser.connect(playCtx.destination);
      }
      if (playCtx.state === 'suspended') await playCtx.resume();
      const buf = playCtx.createBuffer(1, f32.length, 24000);
      buf.copyToChannel(f32, 0);
      await new Promise(res => {
        const src = playCtx.createBufferSource();
        src.buffer = buf;
        src.connect(analyser);
        src.onended = res;
        src.start();
        requestAnimationFrame(trackPlaybackLevel);
      });
    } catch (e) { console.debug('playPCM', e.message); }
  });
  await playChain;
}

let voiceEnabled = true, ruVoice = null, pendingSpeech = '', voiceWaitTimer = null;
try { voiceEnabled = localStorage.getItem('jarvis-voice') !== 'off'; } catch {}

function awaitVoice(text) {
  clearVoiceWait();
  pendingSpeech = text;
  if (!voiceEnabled) { setState('idle'); return; }
  setState('processing');
  voiceWaitTimer = setTimeout(() => { voiceWaitTimer = null; setState('idle'); }, 20000);
}
function clearVoiceWait() { if (voiceWaitTimer) { clearTimeout(voiceWaitTimer); voiceWaitTimer = null; } }

function pickVoice() {
  const v = window.speechSynthesis?.getVoices() || [];
  ruVoice = v.find(x => /ru[-_]/i.test(x.lang) && /google|yandex|milena|premium/i.test(x.name)) || v.find(x => /ru[-_]/i.test(x.lang)) || null;
}
if (window.speechSynthesis) { pickVoice(); window.speechSynthesis.onvoiceschanged = pickVoice; }

function cleanForSpeech(text) {
  return (text || '')
    .replace(/[\u{1F000}-\u{1FFFF}\u{2600}-\u{27BF}\u{2190}-\u{21FF}\u{2B00}-\u{2BFF}]/gu, '')
    .replace(/[*_`#>•]/g, '').replace(/https?:\/\/\S+/g, '').replace(/\s+/g, ' ').trim();
}

function speak(text) {
  setState('idle');
  if (!voiceEnabled || !window.speechSynthesis) return;
  const clean = cleanForSpeech(text);
  if (!clean) return;
  try {
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(clean);
    u.lang = 'ru-RU'; u.rate = 1.05;
    if (ruVoice) u.voice = ruVoice;
    u.onstart = () => { botSpeaking = true; setState('speaking'); };
    u.onend = () => { botSpeaking = false; setState(continuous ? 'listening' : 'idle'); };
    window.speechSynthesis.speak(u);
  } catch (e) { console.debug('speak', e.message); }
}

const voiceBtn = $('voice-toggle');
function paintVoiceBtn() {
  voiceBtn.classList.toggle('muted', !voiceEnabled);
  voiceBtn.title = voiceEnabled ? 'Голос включён' : 'Голос выключен';
}
paintVoiceBtn();
voiceBtn.addEventListener('click', () => {
  voiceEnabled = !voiceEnabled;
  try { localStorage.setItem('jarvis-voice', voiceEnabled ? 'on' : 'off'); } catch {}
  paintVoiceBtn();
  haptic();
  showToast(voiceEnabled ? 'Джарвис отвечает голосом' : 'Только текст');
  if (!voiceEnabled) { clearVoiceWait(); window.speechSynthesis?.cancel(); }
});

// ── Вкладки ───────────────────────────────────────────────────────────────────
let activeTab = 'chat';
const TAB_TITLES = { chat: 'J.A.R.V.I.S', dashboard: 'СВОДКА', tasks: 'ДЕЛА', habits: 'ПРИВЫЧКИ', pc: 'ПК-ПУЛЬТ' };
function switchTab(name) {
  if (name !== activeTab) haptic();
  activeTab = name;
  document.querySelectorAll('.view').forEach(v => v.classList.toggle('active', v.id === 'view-' + name));
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
  $('screen-title').textContent = TAB_TITLES[name] || 'J.A.R.V.I.S';
  if (['dashboard', 'tasks', 'habits'].includes(name)) send({ type: 'get_data', view: name });
}
document.querySelectorAll('.tab').forEach(t => t.addEventListener('click', () => switchTab(t.dataset.tab)));
window.switchTab = switchTab;

// ── Данные вкладок ────────────────────────────────────────────────────────────
function greeting() {
  const h = new Date().getHours();
  return h < 5 ? ['Доброй ночи', '🌙'] : h < 12 ? ['Доброе утро', '🌅'] : h < 18 ? ['Добрый день', '☀️'] : ['Добрый вечер', '🌆'];
}

function renderView(name, p) {
  if (name === 'dashboard') renderDashboard(p);
  else if (name === 'habits') renderHabits(p);
  else if (name === 'tasks') renderTasks(p);
}

let showAllFacts = false;
function renderDashboard(p) {
  const [g, gi] = greeting();
  const hello = p.name ? `${g}, ${esc(p.name)}` : g;
  const today = p.today_tasks || [];
  $('dash-body').innerHTML = `
    <div class="hero">
      <div class="hero-hi">${gi} ${hello}</div>
      ${p.weather ? `<div class="hero-wx">${esc(p.weather)}</div>` : ''}
      <div class="hero-city">📍 ${esc(p.city || 'Город не задан')}</div>
    </div>
    <div class="dash-grid">
      <button class="stat" onclick="switchTab('habits')">
        <div class="stat-ico">🔥</div>
        <div><div class="stat-num">${p.habits_done ?? 0}<span>/${p.habits_total ?? 0}</span></div>
        <div class="stat-lbl">привычки · серия ${p.best_streak ?? 0}</div></div>
      </button>
      <button class="stat" onclick="switchTab('tasks')">
        <div class="stat-ico">✅</div>
        <div><div class="stat-num">${p.open_tasks ?? 0}</div><div class="stat-lbl">задач открыто</div></div>
      </button>
    </div>
    <div class="card"><h3>На сегодня</h3>
      ${today.length ? `<div class="list">${today.map(t => `<div>${esc(t)}</div>`).join('')}</div>`
                     : `<div class="sub">Планов нет — добавь в «Дела» или скажи Джарвису</div>`}
    </div>
    <div class="card"><h3>Ближайшее напоминание</h3>
      <div class="sub">${p.next_reminder ? esc(p.next_reminder) : 'Напоминаний нет'}</div>
    </div>
    ${renderMemory(p)}`;
}

// Общая память с ПК: что Джарвис знает — видно и можно поправить.
function renderMemory(p) {
  const a = p.about || {}, facts = a.facts || [];
  const voice = p.pc_voice || [], eps = p.pc_episodes || [];
  const shown = showAllFacts ? facts : facts.slice(0, 8);
  let html = `<div class="card"><h3>Что я о тебе знаю <span class="count">${facts.length}</span></h3>`;
  if (a.about) html += `<div class="sub" style="color:var(--text);margin-bottom:6px">${esc(a.about)}</div>`;
  if (a.goals) html += `<div class="sub" style="margin-bottom:6px">🎯 ${esc(a.goals)}</div>`;
  if (facts.length) {
    html += `<div class="facts">${shown.map(f =>
      `<div class="fact"><span>${esc(f)}</span><button class="x" data-fact="${esc(f)}" aria-label="Забыть">✕</button></div>`).join('')}</div>`;
    if (facts.length > shown.length) html += `<button class="more" onclick="toggleFacts()">Показать все (${facts.length})</button>`;
  } else {
    html += `<div class="sub">Пока пусто — расскажи о себе, я запомню. Память общая с Джарвисом на ПК.</div>`;
  }
  html += `</div>`;
  if (eps.length || voice.length) {
    html += `<div class="card"><h3>Недавно голосом на ПК</h3>`;
    html += eps.map(e => `<div class="voice-line">🗂 ${esc(e)}</div>`).join('');
    html += voice.map(v => `<div class="voice-line"><b>${esc(v.who)}:</b> ${esc(v.text)}</div>`).join('');
    html += `</div>`;
  }
  return html;
}
function toggleFacts() { showAllFacts = !showAllFacts; send({ type: 'get_data', view: 'dashboard' }); }
window.toggleFacts = toggleFacts;

$('dash-body').addEventListener('click', (e) => {
  const btn = e.target.closest('[data-fact]');
  if (!btn) return;
  const fact = btn.dataset.fact;
  const doIt = () => { haptic('rigid'); btn.closest('.fact').style.opacity = '.3'; send({ type: 'fact_delete', text: fact }); };
  if (tg?.showConfirm) tg.showConfirm(`Забыть: «${fact}»?`, ok => ok && doIt());
  else if (confirm(`Забыть: «${fact}»?`)) doIt();
});

function renderHabits(p) {
  const habits = p.habits || [];
  $('habits-body').innerHTML = habits.length ? habits.map(h => `
    <div class="row ${h.done_today ? 'done-today' : ''}">
      <button class="check ${h.done_today ? 'on' : ''}" onclick="habitToggle(${Number(h.id)})" aria-label="Отметить">${h.done_today ? '✓' : ''}</button>
      <div class="body"><div class="title">${esc(h.title)}</div>
        <div class="meta">${h.done_today ? 'Сегодня отмечено' : 'Сегодня ещё нет'}</div></div>
      ${h.streak ? `<div class="streak">🔥 ${Number(h.streak)}</div>` : ''}
      <button class="x" onclick="habitDelete(${Number(h.id)})" aria-label="Удалить">✕</button>
    </div>`).join('')
    : `<div class="empty">Привычек пока нет 🌱<br>Добавь сверху и отмечай каждый день — серия будет расти.</div>`;
}

function renderTasks(p) {
  const tasks = p.tasks || [], reminders = p.reminders || [];
  let html = '';
  if (tasks.length) {
    html += `<div class="section-label">Задачи · ${tasks.length}</div>` + tasks.map(t => `
      <div class="row">
        <button class="check" onclick="taskDone(${Number(t.id)}, this)" aria-label="Выполнено"></button>
        <div class="body"><div class="title">${esc(t.title)}</div>
          ${t.due ? `<div class="meta ${t.overdue ? 'overdue' : ''}">${t.overdue ? 'Просрочено · ' : ''}${esc(t.due)}</div>` : ''}</div>
        <button class="x" onclick="taskDelete(${Number(t.id)})" aria-label="Удалить">✕</button>
      </div>`).join('');
  }
  if (reminders.length) {
    html += `<div class="section-label">Напоминания · ${reminders.length}</div>` + reminders.map(r => `
      <div class="row">
        <button class="check bell" onclick="reminderDone(${Number(r.id)})" aria-label="Завершить">🔔</button>
        <div class="body"><div class="title">${esc(r.text)}</div><div class="meta">${esc(r.when)}</div></div>
        <button class="x" onclick="reminderDelete(${Number(r.id)})" aria-label="Удалить">✕</button>
      </div>`).join('');
  }
  $('tasks-body').innerHTML = html || `<div class="empty">Пусто ✨<br>Добавь задачу или напиши «напомни в 15:00 позвонить маме»</div>`;
}

function habitToggle(id) { haptic('medium'); send({ type: 'habit_toggle', id }); }
function habitDelete(id) { haptic('rigid'); send({ type: 'habit_delete', id }); }
function taskDone(id, el) {
  haptic('medium'); notifyHaptic('success');
  el?.classList.add('on'); el?.closest('.row')?.classList.add('done');
  setTimeout(() => send({ type: 'task_done', id }), 250);
}
function taskDelete(id) { haptic('rigid'); send({ type: 'task_delete', id }); }
function reminderDone(id) { haptic('medium'); send({ type: 'reminder_done', id }); }
function reminderDelete(id) { haptic('rigid'); send({ type: 'reminder_delete', id }); }
Object.assign(window, { habitToggle, habitDelete, taskDone, taskDelete, reminderDone, reminderDelete });

function addHabit() {
  const el = $('habit-in'), t = el.value.trim();
  if (!t) return;
  if (!send({ type: 'habit_add', title: t })) { showToast('Нет связи — попробуй через минуту', 'error'); return; }
  haptic(); el.value = '';
}
function addTaskOrReminder() {
  const el = $('task-in'), t = el.value.trim();
  if (!t) return;
  const ok = /^\s*(напомни|таймер|remind)/i.test(t) ? send({ type: 'reminder_add', text: t }) : send({ type: 'task_add', text: t });
  if (!ok) { showToast('Нет связи — попробуй через минуту', 'error'); return; }
  haptic(); el.value = '';
}
Object.assign(window, { addHabit, addTaskOrReminder });

// ── Старт ─────────────────────────────────────────────────────────────────────
setState('boot');
connect();
