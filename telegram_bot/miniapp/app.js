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
    // Вкладку открыли, пока связи не было (или до первого соединения), — её
    // запрос молча пропал, и «Сводка» оставалась пустой. Догружаем сейчас.
    requestTabData(activeTab);
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
        if (activeTab !== 'chat') showToast('Снимок получен', 'success', { label: 'Открыть', onClick: () => switchTab('chat') });
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
        if (msg.online && activeTab === 'pc') send({ type: 'pc_macros' });
        break;
      case 'pc_macros':
        renderMacros(msg.items || [], msg.error || '');
        break;
      case 'pc_macro_result':
        macroResult(msg);
        break;
      case 'pc_edit_result':
        notifyHaptic(msg.ok ? 'success' : 'error');
        showToast(msg.text || (msg.ok ? 'Готово' : 'Не вышло'), msg.ok ? 'info' : 'error');
        break;
      case 'thinking':
        setState('processing');
        showTyping();
        break;
      case 'data':
        renderView(msg.view, msg.payload || {});
        if (msg.view === enterView) {                     // первый показ после смены вкладки
          enterView = '';
          const body = document.querySelector(`#view-${msg.view} .view-body`);
          cascade(body);
          if (body) countUp(body);
        }
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
    showToast(btn.textContent.trim());
    addMsg('user', cmd, { rich: false });
    if (!shapeFor(cmd)) { clearTimeout(shapeTimer); orbView.setShape('reactor'); }
    send({ type: 'text', text: cmd, tts: voiceEnabled && !!btn.dataset.say });
    setState('processing');
  });
});

// Свои команды ПК: кнопки из macros.json на компьютере. Команда с 🔒 —
// сначала «точно?», как на ПК голосом.
const MACRO_ICON = '<svg viewBox="0 0 24 24"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>';
const LOCK_ICON = '<svg viewBox="0 0 24 24"><rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>';
function renderMacros(items, error) {
  const box = $('pc-macros');
  box.replaceChildren();
  if (!items.length) {
    const empty = document.createElement('div');
    empty.className = 'macros-empty';
    empty.textContent = error ? `${error} — свои команды покажутся, когда он будет на связи`
      : 'Пока нет. На ПК: «Джарвис, создай команду…» или окно «Свои команды»';
    box.append(empty);
    return;
  }
  for (const it of items) {
    const b = document.createElement('button');
    b.className = 'pc-tile wide macro';
    b.innerHTML = it.confirm ? LOCK_ICON : MACRO_ICON;
    const t = document.createElement('span');
    t.className = 'macro-text';
    const n = document.createElement('b');
    n.textContent = it.name;
    const sub = document.createElement('small');
    sub.textContent = it.phrase ? `«${it.phrase}»` : (it.when || '');
    t.append(n, sub);
    b.append(t);
    b.addEventListener('click', () => runMacro(it.name, false, b));
    box.append(b);
  }
}
function runMacro(name, confirmed, btn) {
  if (!pcIsOnline) { showToast('ПК офлайн — команда не дойдёт', 'error'); notifyHaptic('warning'); return; }
  haptic('medium');
  if (btn) { btn.classList.add('sent'); setTimeout(() => btn.classList.remove('sent'), 700); }
  if (send({ type: 'pc_macro', name, confirmed })) showToast(`Выполняю «${name}»`);
}
function macroResult(msg) {
  if (msg.need_confirm) {
    const go = ok => ok && runMacro(msg.name, true);
    if (tg?.showConfirm) tg.showConfirm(msg.text, go); else go(window.confirm(msg.text));
    return;
  }
  notifyHaptic(msg.ok ? 'success' : 'error');
  addMsg('bot', msg.text, { rich: false });
  showToast(msg.text.split('\n')[0], msg.ok ? 'info' : 'error');
}

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
const TAB_TITLES = { chat: 'Джарвис', dashboard: 'Сводка', tasks: 'Дела', study: 'Учёба', habits: 'Привычки', pc: 'ПК-пульт' };
// ── Анимации: подсветка вкладки переезжает, карточки каскадом, числа набегают ──
// Только transform/opacity (их рисует видеокарта) и только при смене вкладки —
// обновления данных раз в 30 с не перезапускают каскад и не мигают.
const tabGlider = document.createElement('i');
tabGlider.id = 'tab-glider';
$('tabbar').prepend(tabGlider);
function placeGlider(animate = true) {
  const t = document.querySelector('.tab.active');
  if (!t) return;
  tabGlider.style.transition = animate ? '' : 'none';
  tabGlider.style.width = t.offsetWidth + 'px';
  tabGlider.style.transform = `translateX(${t.offsetLeft}px)`;
}
window.addEventListener('resize', () => placeGlider(false));
requestAnimationFrame(() => placeGlider(false));
const reduceMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
let enterView = '';
function cascade(el) {
  if (!el || reduceMotion) return;
  el.classList.remove('enter');
  void el.offsetWidth;                                    // перезапуск анимации
  el.classList.add('enter');
  setTimeout(() => el.classList.remove('enter'), 900);
}
function countUp(root) {
  if (reduceMotion) return;
  root.querySelectorAll('[data-count]').forEach(el => {
    const to = Number(el.dataset.count) || 0;
    if (to <= 0) return;
    const t0 = performance.now(), dur = 600;
    const step = now => {
      const k = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - k, 3);
      el.firstChild.nodeValue = String(Math.round(to * e));
      if (k < 1) requestAnimationFrame(step);
    };
    el.firstChild.nodeValue = '0';
    requestAnimationFrame(step);
  });
}

function switchTab(name) {
  if (name !== activeTab) { haptic(); enterView = name; }
  activeTab = name;
  document.querySelectorAll('.view').forEach(v => v.classList.toggle('active', v.id === 'view-' + name));
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
  $('screen-title').textContent = TAB_TITLES[name] || 'Джарвис';
  placeGlider();
  const t = document.querySelector(`.tab[data-tab="${name}"]`);
  if (t && !reduceMotion) { t.classList.remove('pop'); void t.offsetWidth; t.classList.add('pop'); }
  requestTabData(name);
}
function requestTabData(name) {
  if (['dashboard', 'tasks', 'habits', 'study'].includes(name)) send({ type: 'get_data', view: name });
  if (name === 'pc') send({ type: 'pc_macros' });
}
document.querySelectorAll('.tab').forEach(t => t.addEventListener('click', () => switchTab(t.dataset.tab)));
window.switchTab = switchTab;

// ── Иконки ────────────────────────────────────────────────────────────────────
// Векторные, в одном стиле с нижней панелью (линия 2, скруглённые концы), как
// SF Symbols на iPhone. Раньше здесь были эмодзи 🔥 ✅ 📍 🎯 🔔 ✕ — каждый
// телефон рисует их по-своему, и рядом с тонкими линиями они смотрелись чужими.
const ICON_PATHS = {
  flame: '<path d="M12 22c4 0 7-2.8 7-7 0-3.6-2.4-6.2-4-8-.5 2-1.6 3.4-3 4 0-3.3-1.5-6.4-4-9 .3 3.6-3 6.2-3 11 0 5 3 9 7 9z"/><path d="M12 22c-1.7 0-3-1.3-3-3.2 0-1.8 1.4-3 3-4.8 1.6 1.8 3 3 3 4.8 0 1.9-1.3 3.2-3 3.2z"/>',
  check: '<polyline points="20 6 9 17 4 12"/>',
  checkCircle: '<circle cx="12" cy="12" r="9.5"/><polyline points="16.5 9 10.5 15 7.5 12"/>',
  pin: '<path d="M12 21.5s-7-6.1-7-11.5a7 7 0 0 1 14 0c0 5.4-7 11.5-7 11.5z"/><circle cx="12" cy="10" r="2.5"/>',
  target: '<circle cx="12" cy="12" r="9.5"/><circle cx="12" cy="12" r="5.5"/><circle cx="12" cy="12" r="1.5"/>',
  archive: '<rect x="3" y="4" width="18" height="5" rx="1.5"/><path d="M5 9v9.5A1.5 1.5 0 0 0 6.5 20h11a1.5 1.5 0 0 0 1.5-1.5V9"/><line x1="10" y1="13" x2="14" y2="13"/>',
  bell: '<path d="M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15z"/><path d="M10 21h4"/>',
  x: '<line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  sunrise: '<path d="M17 18a5 5 0 0 0-10 0"/><path d="M12 2v7M4.2 10.2l1.4 1.4M1 18h2M21 18h2M18.4 11.6l1.4-1.4M23 22H1M8 6l4-4 4 4"/>',
  sunset: '<path d="M17 18a5 5 0 0 0-10 0"/><path d="M12 9V2M4.2 10.2l1.4 1.4M1 18h2M21 18h2M18.4 11.6l1.4-1.4M23 22H1M16 5l-4 4-4-4"/>',
  moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
  leaf: '<path d="M11 20A7 7 0 0 1 9.8 6.1C15.5 5 17 4.5 19 2c1 2 2 4.2 2 8 0 5.5-4.8 10-10 10z"/><path d="M2 21c0-3 1.9-5.4 5.1-6C9.5 14.5 12 13 13 12"/>',
  phone: '<path d="M21.5 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 1.6 4.2 2 2 0 0 1 3.6 2h3a2 2 0 0 1 2 1.7c.1.9.4 1.8.7 2.7a2 2 0 0 1-.5 2.1L7.6 9.8a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.7.7a2 2 0 0 1 1.8 2z"/>',
  ball: '<circle cx="12" cy="12" r="9.5"/><polygon points="12 7.6 15.6 10.2 14.2 14.4 9.8 14.4 8.4 10.2" fill="currentColor"/><path d="M12 7.6V2.6M15.6 10.2l4.6-1.6M14.2 14.4l2.9 4.1M9.8 14.4l-2.9 4.1M8.4 10.2 3.8 8.6"/>',
  play: '<polygon points="7 4.5 19 12 7 19.5" fill="currentColor"/>',
  chevron: '<polyline points="6 9 12 15 18 9"/>',
  tray: '<polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.5 5.1 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.5-6.9A2 2 0 0 0 16.8 4H7.2a2 2 0 0 0-1.7 1.1z"/>',
};
function icon(name, cls = '') {
  return `<svg class="ico ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" `
       + `stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICON_PATHS[name] || ''}</svg>`;
}

// ── Сворачиваемые карточки «Сводки» (как группы в настройках iPhone) ──────────
// «Сводка» перерисовывается раз в 30 с — открытость храним сами, иначе карточка
// закрывалась бы посреди чтения.
let foldOpen = {};
try { foldOpen = JSON.parse(localStorage.getItem('jarvis-fold') || '{}') || {}; } catch { foldOpen = {}; }
function fold(key, title, body, cls = '') {
  return `<details class="card fold ${cls}" data-fold="${key}" ${foldOpen[key] ? 'open' : ''}>`
       + `<summary><h3>${title}${icon('chevron', 'chev')}</h3></summary>${body}</details>`;
}

// ── Данные вкладок ────────────────────────────────────────────────────────────
function greeting() {
  const h = new Date().getHours();
  return h < 5 ? ['Доброй ночи', 'moon'] : h < 12 ? ['Доброе утро', 'sunrise'] : h < 18 ? ['Добрый день', 'sun'] : ['Добрый вечер', 'sunset'];
}

function renderView(name, p) {
  if (name === 'dashboard') renderDashboard(p);
  else if (name === 'habits') renderHabits(p);
  else if (name === 'tasks') renderTasks(p);
  else if (name === 'study') renderStudy(p);
}

let showAllFacts = false;
function renderDashboard(p) {
  const [g, gi] = greeting();
  const hello = p.name ? `${g}, ${esc(p.name)}` : g;
  const today = p.today_tasks || [];
  $('dash-body').innerHTML = `
    <div class="hero">
      <div class="hero-hi">${icon(gi, 'hero-ico')}${hello}</div>
      ${p.weather ? `<div class="hero-wx">${esc(p.weather)}</div>` : ''}
      <div class="hero-city">${icon('pin')}${esc(p.city || 'Город не задан')}</div>
    </div>
    <div class="dash-grid">
      <button class="stat" onclick="switchTab('habits')">
        <div class="stat-ico">${icon('flame')}</div>
        <div><div class="stat-num" data-count="${Number(p.habits_done) || 0}">${p.habits_done ?? 0}<span>/${p.habits_total ?? 0}</span></div>
        <div class="stat-lbl">привычки · серия ${p.best_streak ?? 0}</div></div>
      </button>
      <button class="stat" onclick="switchTab('tasks')">
        <div class="stat-ico">${icon('checkCircle')}</div>
        <div><div class="stat-num" data-count="${Number(p.open_tasks) || 0}">${p.open_tasks ?? 0}</div><div class="stat-lbl">задач открыто</div></div>
      </button>
    </div>
    <div class="card"><h3>На сегодня</h3>
      ${today.length ? `<div class="list">${today.map(t => `<div>${esc(t)}</div>`).join('')}</div>`
                     : `<div class="sub">Планов нет — добавь в «Дела» или скажи Джарвису</div>`}
    </div>
    <div class="card"><h3>Ближайшее напоминание</h3>
      <div class="sub">${p.next_reminder ? esc(p.next_reminder) : 'Напоминаний нет'}</div>
    </div>
    ${renderFootball(p.football || {}, p.pc_online)}
    ${renderMe(p.me || {}, p.pc_online)}
    ${renderCalls(p.calls || {})}
    ${renderMemory(p)}
    ${renderAbilities(p.abilities || [])}`;
}

// ── Футбол: карточка любимого клуба (снимок с ПК, эмблемы — картинки ESPN) ─────
const FB_PALETTE = ['63,208,189', '255,138,52', '120,170,255', '236,90,120', '217,226,90', '180,130,255'];
let fbLastScore = {};                                  // счёт прошлой отрисовки — цифра «подпрыгивает» на гол
let fbLive = false;

function fbRgb(hex, name) {
  let r, g, b;
  if (/^#?[0-9a-f]{6}$/i.test(hex || '')) {
    const h = hex.replace('#', '');
    [r, g, b] = [0, 2, 4].map(i => parseInt(h.slice(i, i + 2), 16));
  } else {
    let n = 0; for (const ch of name || '') n += ch.charCodeAt(0);
    [r, g, b] = FB_PALETTE[n % FB_PALETTE.length].split(',').map(Number);
  }
  const lum = 0.3 * r + 0.59 * g + 0.11 * b;             // тёмно-синий не пропадает на чёрном
  if (lum < 90) { const k = (90 - lum) / 255 + 0.25; [r, g, b] = [r, g, b].map(c => Math.round(c + (255 - c) * k)); }
  return `${r},${g},${b}`;
}
function fbAbbr(abbr, name) { return (abbr || (name || '').replace(/[^A-Za-zА-Яа-яЁё]/g, '').slice(0, 3)).toUpperCase(); }
function fbCrest(url, abbr, name, rgb, big) {
  return `<span class="fb-crest ${big ? 'big' : ''}" style="--c:${rgb}">`
    + (url ? `<img src="${esc(url)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()">` : '')
    + `<b>${esc(fbAbbr(abbr, name))}</b></span>`;
}
function fbDay(d) {
  const t = new Date(); t.setHours(0, 0, 0, 0);
  const x = new Date(d); x.setHours(0, 0, 0, 0);
  const days = Math.round((x - t) / 86400000);
  return days === 0 ? 'Сегодня' : days === 1 ? 'Завтра' : days === -1 ? 'Вчера'
    : d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' });
}
function fbHM(d) { return d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' }); }
function fbCountdown(iso) {
  let s = Math.floor((new Date(iso) - Date.now()) / 1000);
  if (!(s > 0)) return 'вот-вот начнётся';
  const d = Math.floor(s / 86400); s -= d * 86400;
  const h = Math.floor(s / 3600); s -= h * 3600;
  const m = Math.floor(s / 60); s -= m * 60;
  if (d) return `через ${d} д ${h} ч`;
  if (h) return `через ${h} ч ${String(m).padStart(2, '0')} мин`;
  return `через ${m}:${String(s).padStart(2, '0')}`;
}
setInterval(() => document.querySelectorAll('.fb-count[data-when]').forEach(el => { el.textContent = fbCountdown(el.dataset.when); }), 1000);
// Идёт матч — сводка обновляется сама раз в 30 с (счёт с ПК приходит раз в минуту).
setInterval(() => { if (fbLive && activeTab === 'dashboard' && !document.hidden) send({ type: 'get_data', view: 'dashboard' }); }, 30000);

function renderFootball(f, online) {
  fbLive = false;
  if (!f.has) return `<div class="card fb"><h3>${icon('ball')}Футбол</h3>
    <div class="sub">Скажи Джарвису «я болею за Реал» или укажи клуб в «Обо мне» — здесь будут матчи, счёт и новости.</div></div>`;
  const m = f.next;
  let match = '<div class="sub">Ближайших матчей не нашёл</div>', goal = false;
  if (m) {
    const live = m.state === 'in', done = m.state === 'post';
    fbLive = live;
    const when = new Date(m.when);
    const hc = fbRgb(m.home_color, m.home), ac = fbRgb(m.away_color, m.away);
    const prev = fbLastScore[m.id];
    // Гол — только когда число выросло: начало матча («» → 0) не салютует.
    const popped = side => (live && prev && /^\d+$/.test(prev[side] || '') && Number(m[side]) > Number(prev[side])) ? 'pop' : '';
    goal = !!(popped('hs') || popped('as'));
    fbLastScore = { [m.id]: { hs: m.hs, as: m.as } };
    const center = (live || done)
      ? `<div class="fb-score"><span class="${popped('hs')}" style="--p:${hc}">${esc(m.hs || '0')}</span><i>:</i>`
        + `<span class="${popped('as')}" style="--p:${ac}">${esc(m.as || '0')}</span></div>`
        + (live ? `<div class="fb-live"><i></i>${esc(m.detail || 'LIVE')}</div>` : `<div class="fb-when">ФИНАЛ</div>`)
      : `<div class="fb-time">${fbHM(when)}</div><div class="fb-when">${fbDay(when)}</div>`
        + `<div class="fb-count" data-when="${esc(m.when)}">${fbCountdown(m.when)}</div>`;
    const soon = live || (!done && when - Date.now() < 3600e3);
    const confetti = goal ? `<div class="fb-confetti">${Array.from({ length: 18 }, (_, i) =>
      `<i style="--x:${Math.round(Math.cos(i * 2.4) * (60 + (i * 37) % 70))}px;--y:${Math.round(Math.sin(i * 2.4) * 50 - 30)}px;`
      + `--r:${(i * 97) % 360}deg;--c:${[hc, ac, '255,255,255'][i % 3]}"></i>`).join('')}</div>` : '';
    match = `<div class="fb-match ${live ? 'live' : ''} ${goal ? 'goal' : ''}" style="--h:${hc};--a:${ac}">
      ${goal ? '<div class="fb-league fb-goal">ГОЛ!</div>' : m.league ? `<div class="fb-league">${esc(m.league)}</div>` : ''}
      <div class="fb-row">
        <div class="fb-team">${fbCrest(m.home_logo, m.home_abbr, m.home, hc, true)}<span class="fb-name">${esc(m.home)}</span></div>
        <div class="fb-mid">${center}</div>
        <div class="fb-team">${fbCrest(m.away_logo, m.away_abbr, m.away, ac, true)}<span class="fb-name">${esc(m.away)}</span></div>
      </div>${confetti}
    </div>
    ${soon ? `<button class="fb-watch" data-fbwatch ${online ? '' : 'data-off="1"'}>${icon('play')}Смотреть на ПК · Кинопоиск</button>` : ''}`;
    if (goal) notifyHaptic('success');
  }
  const res = f.results || [];
  const form = res.length ? `<div class="me-group">Форма</div><div class="fb-form">${res.slice().reverse().map(r =>
      `<span class="fb-res r${r.res === 'В' ? 'w' : r.res === 'П' ? 'l' : 'd'}" title="${esc(r.home)} ${esc(r.hs)}:${esc(r.as)} ${esc(r.away)}">${esc(r.res || '–')}</span>`).join('')}
      <span class="sub small fb-lastres">${esc(res[0].home)} ${esc(res[0].hs)}:${esc(res[0].as)} ${esc(res[0].away)}</span></div>` : '';
  const ups = (f.upcoming || []).map(u => {
    const d = new Date(u.when);
    return `<div class="fb-up"><span class="fb-up-when">${fbDay(d)} ${fbHM(d)}</span>`
      + `${fbCrest(u.home_logo, u.home_abbr, u.home, fbRgb(u.home_color, u.home))}<span class="fb-up-t">${esc(u.home)} — ${esc(u.away)}</span>`
      + `${fbCrest(u.away_logo, u.away_abbr, u.away, fbRgb(u.away_color, u.away))}</div>`;
  }).join('');
  const news = (f.news || []).map(n => `<button class="fb-news" data-link="${esc(n.link || '')}">
      <span>${esc(n.title)}</span><small>${esc(n.source || '')}</small></button>`).join('');
  return `<div class="card fb"><h3>${icon('ball')}${esc(f.name || 'Футбол')}${m && m.state === 'in' ? '<span class="fb-badge">LIVE</span>' : ''}</h3>
    ${match}${form}
    ${ups ? `<div class="me-group">Дальше</div>${ups}` : ''}
    ${news ? `<div class="me-group">Новости</div>${news}` : ''}
    ${f.updated ? `<div class="sub small">Обновлено с ПК: ${esc(f.updated)}</div>` : ''}</div>`;
}

// ── С ПК: «Обо мне», звонки, «Что умею» ──────────────────────────────────────
function renderMe(me, online) {
  if (!me.has) return `<div class="card"><h3>Обо мне</h3><div class="sub">Анкета приходит с ПК — включи компьютер с Джарвисом.</div></div>`;
  let html = '';
  for (const g of me.groups || []) {
    html += `<div class="me-group">${esc(g.title)}</div>`;
    html += g.questions.map(q => `
      <button class="me-row" data-me="${esc(q.key)}" data-label="${esc(q.label)}" data-value="${esc(q.value || '')}"
              data-ph="${esc(q.placeholder || '')}" ${online ? '' : 'data-off="1"'}>
        <span class="me-label">${esc(q.label)}</span>
        <span class="me-value ${q.value ? '' : 'unset'}">${esc(q.value || 'не указано')}</span>
      </button>`).join('');
  }
  return fold('me', `Обо мне <span class="count">${Number(me.known)}/${Number(me.total)}</span>`,
              html + `<div class="sub small">Нажми, чтобы изменить — сохранится на ПК.</div>`, 'me');
}

function renderCalls(c) {
  const calls = c.calls || [];
  if (!calls.length) return '';
  return `<div class="card"><h3>Звонки <span class="count">${calls.length}</span></h3>` + calls.map(call => `
    <details class="call">
      <summary>${icon('phone')}<span class="call-who">${call.who === 'вам' ? 'Звонок вам' : esc(call.who)}</span>
        <span class="call-when">${esc(call.when)} · ${Number(call.min)} мин</span></summary>
      ${call.result ? `<div class="sub small">${esc(call.result)}</div>` : ''}
      ${(call.lines || []).length ? (call.lines || []).map(l => `
        <div class="call-line ${l.who === 'Джарвис' ? 'j' : ''}"><b>${esc(l.who)}</b>${esc(l.text)}</div>`).join('')
        : '<div class="sub small">Расшифровки нет — ничего не расслышано.</div>'}
    </details>`).join('') + `</div>`;
}

function renderAbilities(list) {
  if (!list.length) return '';
  return fold('abilities', 'Что я умею', `<div class="sub small">Нажми — отправлю Джарвису.</div>` +
    list.map(s => `<div class="me-group">${esc(s.title)}</div><div class="ability-chips">` +
      s.phrases.map(ph => `<button class="chip" data-try="${esc(ph)}">${esc(ph)}</button>`).join('') + `</div>`).join(''));
}

// toggle не всплывает — ловим на погружении.
$('dash-body').addEventListener('toggle', (e) => {
  const d = e.target;
  if (!d.dataset || !d.dataset.fold) return;
  foldOpen[d.dataset.fold] = d.open;
  try { localStorage.setItem('jarvis-fold', JSON.stringify(foldOpen)); } catch { /* приватный режим */ }
}, true);

$('dash-body').addEventListener('click', (e) => {
  const tryBtn = e.target.closest('[data-try]');
  if (tryBtn) { switchTab('chat'); say(tryBtn.dataset.try); return; }
  const watch = e.target.closest('[data-fbwatch]');
  if (watch) {
    if (watch.dataset.off) { showToast('ПК офлайн — включи компьютер с Джарвисом', 'error'); return; }
    haptic('medium');
    if (send({ type: 'football_watch' })) showToast('Включаю матч на ПК…', 'info');
    return;
  }
  const link = e.target.closest('[data-link]');
  if (link) {
    if (link.dataset.link) { haptic(); if (tg?.openLink) tg.openLink(link.dataset.link); else window.open(link.dataset.link, '_blank'); }
    return;
  }
  const row = e.target.closest('[data-me]');
  if (!row || row.querySelector('input')) return;
  if (row.dataset.off) { showToast('ПК офлайн — изменить можно, когда он включён', 'error'); return; }
  const input = document.createElement('input');
  input.className = 'me-input';
  input.value = row.dataset.value;
  input.placeholder = row.dataset.ph;
  const val = row.querySelector('.me-value');
  val.replaceWith(input);
  input.focus();
  const done = (save) => {
    if (save && input.value.trim() !== row.dataset.value) {
      send({ type: 'about_answer', key: row.dataset.me, value: input.value.trim() });
      haptic('medium');
    }
    input.replaceWith(val);
  };
  input.addEventListener('keydown', ev => { if (ev.key === 'Enter') done(true); if (ev.key === 'Escape') done(false); });
  input.addEventListener('blur', () => done(true));
});

// ── Учёба ────────────────────────────────────────────────────────────────────
let studyDay = null;
const STUDY_GROUPS = [['overdue', 'Просрочено'], ['today', 'Сегодня'], ['tomorrow', 'Завтра'],
  ['week', 'На неделе'], ['later', 'Позже'], ['nodate', 'Без срока'], ['done', 'Сделано']];
let lastStudy = null;
function renderStudy(p) {
  lastStudy = p;
  const box = $('study-body');
  if (!p.has) {
    box.innerHTML = `<div class="empty">${icon('tray', 'empty-ico')}<br>Расписание — с ПК<br>
      Открой на компьютере «Учёба», добавь пары — через минуту они будут здесь, даже когда ПК выключен.</div>`;
    return;
  }
  const week = p.week || [];
  if (studyDay === null) studyDay = Math.max(0, week.findIndex(d => d.today));
  const day = week[studyDay] || { lessons: [] };
  let html = '';
  if (p.now_next) html += `<div class="card now-next">${icon('bell')}<span>${esc(p.now_next)}</span></div>`;
  if (p.has_lessons) {
    html += `<div class="week-chips">` + week.map((d, i) => `
      <button class="day-chip ${i === studyDay ? 'on' : ''} ${d.today ? 'today' : ''}" data-day="${i}">
        <b>${esc(d.day)}</b><span>${esc(d.date)}</span>${d.lessons.length ? `<i>${d.lessons.length}</i>` : ''}</button>`).join('') + `</div>`;
    html += `<div class="section-label">${esc(day.day)} · неделя ${esc(p.parity)}</div>`;
    html += day.lessons.length ? day.lessons.map(l => `
      <div class="row lesson"><div class="lesson-time">${esc(l.start)}${l.end ? `<span>${esc(l.end)}</span>` : ''}</div>
        <div class="body"><div class="title">${esc(l.subject)}</div>
        <div class="meta">${[l.kind, l.room ? 'ауд. ' + l.room : '', l.teacher].filter(Boolean).map(esc).join(' · ')}</div></div></div>`).join('')
      : `<div class="sub pad">Пар нет — отдыхай.</div>`;
  }
  const tasks = p.tasks || [];
  for (const [g, title] of STUDY_GROUPS) {
    const items = tasks.filter(t => t.group === g);
    if (!items.length) continue;
    html += `<div class="section-label ${g === 'overdue' ? 'warn' : ''}">${title} · ${items.length}</div>` + items.map(t => `
      <div class="row ${t.done ? 'done' : ''}">
        <button class="check ${t.done ? 'on' : ''}" data-study-done="${esc(t.id)}" aria-label="Сделано">${t.done ? icon('check') : ''}</button>
        <div class="body"><div class="title">${esc(t.title)}</div>
          <div class="meta ${g === 'overdue' ? 'overdue' : ''}">${[t.subject, t.kind !== 'домашка' ? t.kind : '', t.due].filter(Boolean).map(esc).join(' · ')}</div></div>
      </div>`).join('');
  }
  if (!tasks.length) html += `<div class="sub pad">Заданий нет. Добавь сверху: «реферат по матану», срок «в пятницу».</div>`;
  html += `<div class="sub small pad">С ПК: ${esc(p.updated || '—')}${p.pc_online ? '' : ' · ПК офлайн — правки подождут'}</div>`;
  box.innerHTML = html;
}
$('study-body').addEventListener('click', (e) => {
  const d = e.target.closest('[data-day]');
  if (d) { haptic(); studyDay = Number(d.dataset.day); renderStudy(lastStudy); return; }
  const c = e.target.closest('[data-study-done]');
  if (c) {
    haptic('medium');
    c.classList.toggle('on');
    c.closest('.row')?.classList.toggle('done');
    send({ type: 'study_done', id: c.dataset.studyDone });
  }
});
function studyAdd() {
  const t = $('study-in'), d = $('study-due');
  const title = t.value.trim();
  if (!title) { t.focus(); return; }
  if (!send({ type: 'study_add', title, due: d.value.trim() })) { showToast('Нет связи — попробуй через минуту', 'error'); return; }
  haptic(); t.value = ''; d.value = '';
}
window.studyAdd = studyAdd;

// Общая память с ПК: что Джарвис знает — видно и можно поправить.
function renderMemory(p) {
  const a = p.about || {}, facts = a.facts || [];
  const voice = p.pc_voice || [], eps = p.pc_episodes || [];
  const shown = showAllFacts ? facts : facts.slice(0, 8);
  let html = '';
  if (a.about) html += `<div class="sub" style="color:var(--text);margin-bottom:6px">${esc(a.about)}</div>`;
  if (a.goals) html += `<div class="sub" style="margin-bottom:6px">${icon('target')}${esc(a.goals)}</div>`;
  if (facts.length) {
    html += `<div class="facts">${shown.map(f =>
      `<div class="fact"><span>${esc(f)}</span><button class="x" data-fact="${esc(f)}" aria-label="Забыть">${icon('x')}</button></div>`).join('')}</div>`;
    if (facts.length > shown.length) html += `<button class="more" onclick="toggleFacts()">Показать все (${facts.length})</button>`;
  } else {
    html += `<div class="sub">Пока пусто — расскажи о себе, я запомню. Память общая с Джарвисом на ПК.</div>`;
  }
  html = fold('memory', `Что я о тебе знаю <span class="count">${facts.length}</span>`, html);
  if (eps.length || voice.length) {
    html += fold('voice', 'Недавно голосом на ПК',
      eps.map(e => `<div class="voice-line">${icon('archive')}${esc(e)}</div>`).join('') +
      voice.map(v => `<div class="voice-line"><b>${esc(v.who)}:</b> ${esc(v.text)}</div>`).join(''));
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
      <button class="check ${h.done_today ? 'on' : ''}" onclick="habitToggle(${Number(h.id)})" aria-label="Отметить">${h.done_today ? icon('check') : ''}</button>
      <div class="body"><div class="title">${esc(h.title)}</div>
        <div class="meta">${h.done_today ? 'Сегодня отмечено' : 'Сегодня ещё нет'}</div></div>
      ${h.streak ? `<div class="streak">${icon('flame')}${Number(h.streak)}</div>` : ''}
      <button class="x" onclick="habitDelete(${Number(h.id)})" aria-label="Удалить">${icon('x')}</button>
    </div>`).join('')
    : `<div class="empty">${icon('leaf', 'empty-ico')}<br>Привычек пока нет<br>Добавь сверху и отмечай каждый день — серия будет расти.</div>`;
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
        <button class="x" onclick="taskDelete(${Number(t.id)})" aria-label="Удалить">${icon('x')}</button>
      </div>`).join('');
  }
  if (reminders.length) {
    // «📞 …» — напоминание звонком: вместо эмодзи в тексте — иконка телефона.
    html += `<div class="section-label">Напоминания · ${reminders.length}</div>` + reminders.map(r => {
      const call = String(r.text || '').startsWith('📞');
      const text = call ? r.text.replace(/^📞\s*/, '') : r.text;
      return `
      <div class="row">
        <button class="check bell" onclick="reminderDone(${Number(r.id)})" aria-label="Завершить">${icon(call ? 'phone' : 'bell')}</button>
        <div class="body"><div class="title">${esc(text)}</div><div class="meta">${call ? 'Позвоню · ' : ''}${esc(r.when)}</div></div>
        <button class="x" onclick="reminderDelete(${Number(r.id)})" aria-label="Удалить">${icon('x')}</button>
      </div>`;
    }).join('');
  }
  $('tasks-body').innerHTML = html || `<div class="empty">${icon('tray', 'empty-ico')}<br>Пусто<br>Добавь задачу или напиши «напомни в 15:00 позвонить маме»</div>`;
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
