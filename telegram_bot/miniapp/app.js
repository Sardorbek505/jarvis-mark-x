/* JARVIS Mini App — client */

const tg = window.Telegram?.WebApp;

// Follow Telegram's light/dark mode with our own curated palettes (item #7).
// data-theme="light" flips the CSS token set; dark is the default.
function applyTheme() {
  const scheme = tg?.colorScheme || (window.matchMedia?.('(prefers-color-scheme: light)').matches ? 'light' : 'dark');
  const root = document.documentElement;
  if (scheme === 'light') root.setAttribute('data-theme', 'light');
  else root.removeAttribute('data-theme');
  // Stitch the Telegram chrome (header + background) to the active palette.
  const chrome = getComputedStyle(root).getPropertyValue('--chrome').trim() || '#07070f';
  try { tg?.setHeaderColor(chrome); tg?.setBackgroundColor(chrome); } catch {}
}

if (tg) {
  tg.ready();                                  // signal the WebApp is initialised
  tg.expand();
  tg.disableClosingConfirmation();
  applyTheme();
  tg.onEvent?.('themeChanged', applyTheme);    // react if the user switches theme
} else {
  applyTheme();                                // browser preview: honour OS preference
}

// Light haptic feedback on taps — no-op outside Telegram
function haptic(kind = 'light') {
  try { tg?.HapticFeedback?.impactOccurred(kind); } catch {}
}

const USER_ID = tg?.initDataUnsafe?.user?.id ?? 0;
const wsProto = location.protocol === 'https:' ? 'wss:' : 'ws:';
const WS_URL  = `${wsProto}//${location.host}/ws?user_id=${USER_ID}`;

// DOM refs
const orb       = document.getElementById('orb');
const orbLabel  = document.getElementById('orb-label');
const micBtn    = document.getElementById('mic-btn');
const msgs      = document.getElementById('messages');
const textIn    = document.getElementById('text-in');
const sendBtn   = document.getElementById('send-btn');
const pcBadge   = document.getElementById('pc-badge');
const connBadge = document.getElementById('conn-badge');

// Toggle a voice-state class on BOTH the hero orb and the input-bar mic button,
// so whichever is visible reflects listening/recording/hands-free state.
function voiceClass(name, on) {
  orb.classList.toggle(name, on);
  micBtn?.classList.toggle(name, on);
}

// ── State machine ─────────────────────────────────────────────────────────────
const S = {
  idle:       { label: 'Зажми — говори · тап — диалог', cls: 'idle' },
  listening:  { label: 'Слушаю…',  cls: 'listening' },
  processing: { label: 'Думаю…',   cls: 'processing' },
  speaking:   { label: 'Говорю…',  cls: 'speaking' },
};

function setState(name) {
  const s = S[name] ?? S.idle;
  orbLabel.textContent = s.label;
  orb.className = s.cls;
  if (micBtn) micBtn.className = s.cls;   // mic button mirrors state (red when listening)
}

// ── Message rendering ─────────────────────────────────────────────────────────
function addMsg(role, text) {
  document.querySelector('.typing')?.remove();
  // First real message turns the hero-orb empty state into a normal chat feed.
  if (role === 'user' || role === 'bot') exitEmptyChat();
  const d = document.createElement('div');
  d.className = `msg ${role}`;
  d.textContent = text;
  msgs.appendChild(d);
  msgs.parentElement.scrollTop = msgs.parentElement.scrollHeight;
}

function exitEmptyChat() {
  document.getElementById('view-chat')?.classList.remove('is-empty');
}

function addImage(b64, caption) {
  document.querySelector('.typing')?.remove();
  exitEmptyChat();
  const wrap = document.createElement('div');
  wrap.className = 'msg bot';
  wrap.style.padding = '6px';

  const img = document.createElement('img');
  img.src = `data:image/jpeg;base64,${b64}`;
  img.style.cssText = 'width:100%;border-radius:12px;display:block;';
  img.loading = 'lazy';
  wrap.appendChild(img);

  if (caption) {
    const cap = document.createElement('div');
    cap.textContent = caption;
    cap.style.cssText = 'font-size:12px;color:#9aa0b0;margin-top:5px;padding:0 4px;';
    wrap.appendChild(cap);
  }
  msgs.appendChild(wrap);
  msgs.parentElement.scrollTop = msgs.parentElement.scrollHeight;
}

function showTyping() {
  // Idempotent: the client shows it on send AND the server sends 'thinking',
  // so remove any existing one first to avoid stacking two animations.
  document.querySelectorAll('.typing').forEach(el => el.remove());
  const d = document.createElement('div');
  d.className = 'msg bot typing';
  d.innerHTML = '<span></span><span></span><span></span>';
  msgs.appendChild(d);
  msgs.parentElement.scrollTop = msgs.parentElement.scrollHeight;
}

// One-time friendly empty state (shown on first connect, not on reconnects)
let welcomed = false;
function showWelcomeOnce() {
  if (welcomed || msgs.children.length) return;
  welcomed = true;
  addMsg('sys', 'JARVIS на связи. Напиши или зажми орб, чтобы говорить.');
}

// Toast notification banner
function showToast(text, type = 'info', action = null) {
  const container = document.getElementById('toast-container');
  if (!container) return;
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  const span = document.createElement('span');
  span.textContent = text;
  toast.appendChild(span);
  if (action) {
    const btn = document.createElement('button');
    btn.className = 'toast-action';
    btn.textContent = action.label || 'Открыть';
    btn.onclick = () => { action.onClick(); toast.remove(); };
    toast.appendChild(btn);
  }
  container.appendChild(toast);
  setTimeout(() => {
    toast.classList.add('hide');
    setTimeout(() => toast.remove(), 350);
  }, 4000);
}
window.showToast = showToast;

// ── WebSocket ─────────────────────────────────────────────────────────────────
let ws = null;

function connect() {
  ws = new WebSocket(WS_URL);
  ws.binaryType = 'arraybuffer';

  // Connection state lives in the header badge (green dot) — no chat spam.
  ws.onopen = () => {
    connBadge.className = 'badge online';
    connBadge.title = 'Сервер: на связи';
    setState('idle');
    showWelcomeOnce();
    reportClientInfo();
  };

  ws.onclose = () => {
    connBadge.className = 'badge';
    connBadge.title = 'Сервер: переподключение…';
    pcBadge.className   = 'badge';
    setState('idle');
    setTimeout(connect, 3500);
  };

  ws.onerror = () => { connBadge.title = 'Сервер: ошибка соединения'; };

  ws.onmessage = async ({ data }) => {
    let msg;
    try { msg = JSON.parse(data); } catch { return; }

    switch (msg.type) {
      case 'text':
        addMsg('bot', msg.text);
        // The server will follow with either 'audio' (real JARVIS voice) or
        // 'tts_failed' (use browser fallback). Just wait — no timer race.
        awaitVoice(msg.text);
        if (activeTab === 'pc') {
          showToast(msg.text.replace(/^🖥\s*/, ''));
        }
        break;

      case 'image':
        addImage(msg.data, msg.caption);
        setState('idle');
        if (activeTab === 'pc') {
          showToast('📸 Снимок получен', 'success', {
            label: 'Открыть в чате',
            onClick: () => switchTab('chat'),
          });
        }
        break;

      case 'transcript_user':
        if (msg.text) addMsg('user', msg.text);
        break;

      case 'transcript_bot':
        if (msg.text) {
          addMsg('bot', msg.text);
          awaitVoice(msg.text);
        }
        break;

      case 'audio':
        clearVoiceWait();
        window.speechSynthesis?.cancel();  // kill any fallback that may have started
        botSpeaking = true;
        await playPCM(msg.data);
        botSpeaking = false;
        setState(continuous ? 'listening' : 'idle');
        break;

      case 'tts_failed':
        clearVoiceWait();
        speak(pendingSpeech);  // real voice unavailable — use browser TTS once
        break;

      case 'status':
        setState(msg.state);
        break;

      case 'pc_status':
        pcBadge.className = msg.online ? 'badge online' : 'badge';
        pcBadge.title = msg.online ? 'Desktop JARVIS: онлайн' : 'Desktop JARVIS: офлайн';
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

// ── Client context: timezone + location (so JARVIS knows where/when you are) ──
function sendClientInfo(extra) {
  if (ws?.readyState !== WebSocket.OPEN) return;
  let tz = '';
  try { tz = Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch {}
  ws.send(JSON.stringify({ type: 'client_info', tz, ...extra }));
}

async function reportClientInfo() {
  // Always send timezone immediately (reliable, no permission needed)
  sendClientInfo({});

  // Then try precise location → reverse-geocode to a city name
  if (!navigator.geolocation) return;
  navigator.geolocation.getCurrentPosition(async (pos) => {
    const { latitude: lat, longitude: lon } = pos.coords;
    let city = '';
    try {
      const r = await fetch(
        `https://api.bigdatacloud.net/data/reverse-geocode-client?latitude=${lat}&longitude=${lon}&localityLanguage=ru`
      );
      const j = await r.json();
      city = j.city || j.locality || j.principalSubdivision || '';
    } catch {}
    sendClientInfo({ lat, lon, city });
  }, () => { /* permission denied — timezone already sent */ },
     { enableHighAccuracy: false, timeout: 8000, maximumAge: 600000 });
}

// ── Text input ────────────────────────────────────────────────────────────────
sendBtn.addEventListener('click', sendText);
textIn.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendText(); }
});

function sendText() {
  const text = textIn.value.trim();
  if (!text || ws?.readyState !== WebSocket.OPEN) return;
  haptic();
  addMsg('user', text);
  ws.send(JSON.stringify({ type: 'text', text, tts: voiceEnabled }));
  textIn.value = '';
  setState('processing');
  showTyping();
}

// ── Quick PC actions ──────────────────────────────────────────────────────────
function pcCmd(text, event) {
  if (event?.currentTarget) {
    const btn = event.currentTarget;
    btn.classList.add('pc-tile-active');
    setTimeout(() => btn.classList.remove('pc-tile-active'), 600);
  }
  if (ws?.readyState !== WebSocket.OPEN) {
    showToast('⚠ Нет соединения с сервером', 'error');
    addMsg('sys', '⚠ Нет соединения');
    return;
  }
  haptic('medium');
  showToast('🖥 ' + text);
  addMsg('user', text);
  ws.send(JSON.stringify({ type: 'text', text, tts: voiceEnabled }));
  setState('processing');
  showTyping();
}

// Expose for inline onclick
window.pcCmd = pcCmd;

// ── Voice control — hold-to-talk + tap for hands-free (WhatsApp-style) ──────────
let recCtx = null, playCtx = null, workletNode = null, micStream = null;
let micOpen = false;        // mic stream alive
let continuous = false;     // hands-free conversation mode
let botSpeaking = false;    // pause capture while JARVIS talks
let pressTs = 0;
const TAP_MS = 350;         // ≤ this = tap (hands-free), longer = push-to-talk
const VAD_THRESH = 0.016;   // speech energy threshold
const VAD_HANG_MS = 1100;   // silence after speech that ends an utterance
let vadSpoke = false, vadSilence = 0;

for (const el of [orb, micBtn]) {
  if (!el) continue;
  el.addEventListener('pointerdown', onOrbDown);
  el.addEventListener('pointerup', onOrbUp);
  el.addEventListener('pointercancel', onOrbUp);
}

async function onOrbDown(e) {
  e.preventDefault();
  if (ws?.readyState !== WebSocket.OPEN) { addMsg('sys', '⚠ Нет соединения'); return; }
  try { e.currentTarget.setPointerCapture(e.pointerId); } catch {}
  haptic('medium');
  pressTs = Date.now();
  if (continuous) return;          // already hands-free; release decides whether to stop
  await openMic();
  if (micOpen) beginSegment();
}

function onOrbUp(e) {
  e?.preventDefault();
  const dt = Date.now() - pressTs;
  if (continuous) {
    if (dt < TAP_MS) stopAll();    // tap while hands-free → stop
    return;
  }
  if (!micOpen) return;
  if (dt < TAP_MS) {
    continuous = true;             // quick tap → hands-free (mic already running)
    setState('listening');
    voiceClass('hands-free', true);
  } else {
    endSegment();                  // hold release → send this take, then stop
    closeMic();
  }
}

function stopAll() { endSegment(); closeMic(); }

function beginSegment() {
  vadSpoke = false; vadSilence = 0;
  setState('listening');
  voiceClass('rec', true);
  send({ type: 'start_voice' });
}
function endSegment() {
  voiceClass('rec', false);
  setState('processing');
  send({ type: 'stop_voice', tts: voiceEnabled });
}

let _spNode = null;   // ScriptProcessor fallback node (iOS / no AudioWorklet)

async function openMic() {
  if (micOpen) return;
  try {
    const AC = window.AudioContext || window.webkitAudioContext;
    recCtx = new AC();
    if (recCtx.state === 'suspended') await recCtx.resume();
    micStream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 }
    });
    const srcNode = recCtx.createMediaStreamSource(micStream);

    // Preferred path: AudioWorklet. Telegram's iOS webview often lacks it
    // (recCtx.audioWorklet is undefined) — fall back to ScriptProcessorNode.
    let useWorklet = !!(recCtx.audioWorklet && recCtx.audioWorklet.addModule);
    if (useWorklet) {
      try {
        await recCtx.audioWorklet.addModule(new URL('./worklet.js', location.href).href);
        workletNode = new AudioWorkletNode(recCtx, 'pcm-processor');
        workletNode.port.onmessage = ({ data }) => onAudioChunk(data.pcm);
        srcNode.connect(workletNode);
      } catch { useWorklet = false; }
    }
    if (!useWorklet) _startScriptProcessor(srcNode);

    micOpen = true;
  } catch (e) {
    addMsg('sys', '⚠ Нет доступа к микрофону. Разреши доступ или печатай текстом.');
    closeMic();
  }
}

// Fallback PCM capture for browsers without AudioWorklet (iOS Telegram webview).
// Mirrors worklet.js: downsample to 16kHz int16, emit 100ms (1600-sample) chunks.
function _startScriptProcessor(srcNode) {
  const ratio = recCtx.sampleRate / 16000;
  const CHUNK = 1600;
  let acc = [];
  _spNode = recCtx.createScriptProcessor(4096, 1, 1);
  _spNode.onaudioprocess = (e) => {
    const ch = e.inputBuffer.getChannelData(0);
    for (let i = 0; i < ch.length; i += ratio) {
      const lo = ch[Math.floor(i)] ?? 0;
      const hi = ch[Math.min(Math.ceil(i), ch.length - 1)] ?? lo;
      acc.push(lo + (hi - lo) * (i % 1));
    }
    while (acc.length >= CHUNK) {
      const s = acc.splice(0, CHUNK);
      const pcm = new Int16Array(s.length);
      for (let i = 0; i < s.length; i++) pcm[i] = Math.max(-32768, Math.min(32767, s[i] * 32767));
      onAudioChunk(pcm.buffer);
    }
  };
  // ScriptProcessor only fires while connected to a destination; route through a
  // silent gain so the mic isn't echoed to the speakers.
  const sink = recCtx.createGain();
  sink.gain.value = 0;
  srcNode.connect(_spNode);
  _spNode.connect(sink);
  sink.connect(recCtx.destination);
}

function closeMic() {
  micStream?.getTracks().forEach(t => t.stop());
  workletNode?.disconnect();
  if (_spNode) { _spNode.onaudioprocess = null; _spNode.disconnect(); _spNode = null; }
  if (recCtx) { recCtx.close().catch(() => {}); recCtx = null; }
  micStream = workletNode = null;
  micOpen = false;
  continuous = false;
  voiceClass('rec', false);
  voiceClass('hands-free', false);
  setState('idle');
}

function onAudioChunk(buf) {
  if (ws?.readyState !== WebSocket.OPEN) return;
  if (continuous && botSpeaking) return;       // don't capture JARVIS's own voice
  // forward PCM to the server
  const bytes = new Uint8Array(buf);
  let b64 = '';
  for (let i = 0; i < bytes.length; i++) b64 += String.fromCharCode(bytes[i]);
  ws.send(JSON.stringify({ type: 'audio', data: btoa(b64) }));
  // Voice-activity detection drives hands-free segmentation
  if (!continuous) return;
  const i16 = new Int16Array(buf);
  let sum = 0; for (let i = 0; i < i16.length; i++) { const v = i16[i] / 32768; sum += v * v; }
  const rms = Math.sqrt(sum / i16.length);
  if (rms > VAD_THRESH) { vadSpoke = true; vadSilence = 0; voiceClass('rec', true); }
  else if (vadSpoke) {
    vadSilence += 100;                          // worklet chunk ≈ 100ms
    if (vadSilence >= VAD_HANG_MS) {            // end of utterance
      endSegment();                             // → server transcribes & replies
      beginSegment();                           // keep listening for the next one
    }
  }
}

// ── PCM audio playback (Gemini 24 kHz) ───────────────────────────────────────
let playChain = Promise.resolve();

async function playPCM(b64) {
  setState('speaking');
  playChain = playChain.then(async () => {
    try {
      const binary = atob(b64);
      const bytes  = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
      const samples = new Int16Array(bytes.buffer);
      const f32     = new Float32Array(samples.length);
      for (let i = 0; i < samples.length; i++) f32[i] = samples[i] / 32768;

      if (!playCtx || playCtx.state === 'closed') {
        playCtx = new AudioContext({ sampleRate: 24000 });
      }
      if (playCtx.state === 'suspended') await playCtx.resume();

      const buf = playCtx.createBuffer(1, f32.length, 24000);
      buf.copyToChannel(f32, 0);
      await new Promise(res => {
        const src = playCtx.createBufferSource();
        src.buffer = buf;
        src.connect(playCtx.destination);
        src.onended = res;
        src.start();
      });
    } catch (e) { console.debug(`playPCM: ${e.message}`); }
  });
  await playChain;
}

// ── Voice output ──────────────────────────────────────────────────────────────
// Primary: real JARVIS voice (Charon) synthesized server-side by Gemini and
// streamed as PCM → playPCM(). Fallback: browser speechSynthesis, used ONLY if
// the server audio doesn't arrive (e.g. TTS quota hit).
let voiceEnabled = true;
let ruVoice = null;
let pendingSpeech = '';
let voiceWaitTimer = null;

// After a bot text, wait for the server's 'audio' or 'tts_failed'. No early
// browser TTS — that caused the robotic voice to overlap the real one.
// A long safety timeout only handles the rare case where neither arrives
// (e.g. a direct error message): it just drops back to idle, never speaks.
function awaitVoice(text) {
  clearVoiceWait();
  pendingSpeech = text;
  if (!voiceEnabled) { setState('idle'); return; }
  setState('processing');
  voiceWaitTimer = setTimeout(() => { voiceWaitTimer = null; setState('idle'); }, 20000);
}

function clearVoiceWait() {
  if (voiceWaitTimer) { clearTimeout(voiceWaitTimer); voiceWaitTimer = null; }
}

function pickVoice() {
  const voices = window.speechSynthesis?.getVoices() || [];
  ruVoice = voices.find(v => /ru[-_]/i.test(v.lang) && /google|yandex|milena|premium/i.test(v.name))
         || voices.find(v => /ru[-_]/i.test(v.lang))
         || null;
}
if (window.speechSynthesis) {
  pickVoice();
  window.speechSynthesis.onvoiceschanged = pickVoice;
}

function cleanForSpeech(text) {
  return (text || '')
    .replace(/[\u{1F000}-\u{1FFFF}\u{2600}-\u{27BF}←-⇿⬀-⯿]/gu, '') // emoji/symbols
    .replace(/[*_`#>•]/g, '')                 // markdown
    .replace(/https?:\/\/\S+/g, '')           // urls
    .replace(/\s+/g, ' ')
    .trim();
}

function speak(text) {
  setState('idle');
  if (!voiceEnabled || !window.speechSynthesis) return;
  const clean = cleanForSpeech(text);
  if (!clean) return;
  try {
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(clean);
    u.lang = 'ru-RU';
    u.rate = 1.05;
    u.pitch = 1.0;
    if (ruVoice) u.voice = ruVoice;
    u.onstart = () => { botSpeaking = true; setState('speaking'); };
    u.onend   = () => { botSpeaking = false; setState(continuous ? 'listening' : 'idle'); };
    window.speechSynthesis.speak(u);
  } catch (e) { console.debug('speak:', e.message); }
}

// Voice on/off toggle
const voiceBtn = document.getElementById('voice-toggle');
if (voiceBtn) {
  voiceBtn.addEventListener('click', () => {
    voiceEnabled = !voiceEnabled;
    voiceBtn.classList.toggle('muted', !voiceEnabled);
    voiceBtn.title = voiceEnabled ? 'Голос включён' : 'Голос выключен';
    if (!voiceEnabled) { clearVoiceWait(); window.speechSynthesis?.cancel(); }
  });
}

// ── Boot ──────────────────────────────────────────────────────────────────────
connect();

// ── Tabs & data views ─────────────────────────────────────────────────────────
let activeTab = 'chat';
const TAB_TITLES = {
  chat: '💬 Чат', dashboard: '📊 Сводка', tasks: '✅ Дела',
  habits: '🔁 Привычки', pc: '🖥 ПК-пульт',
};

function send(obj) {
  if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
}

function switchTab(name) {
  haptic();
  activeTab = name;
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.getElementById('view-' + name)?.classList.add('active');
  document.querySelectorAll('.tab').forEach(t =>
    t.classList.toggle('active', t.dataset.tab === name));
  const title = document.getElementById('screen-title');
  if (title) title.textContent = TAB_TITLES[name] || 'JARVIS';
  // Data tabs fetch fresh state each time they're opened.
  if (['dashboard', 'tasks', 'habits'].includes(name)) {
    send({ type: 'get_data', view: name });
  }
}
window.switchTab = switchTab;

function greeting() {
  const h = new Date().getHours();
  if (h < 5)  return ['Доброй ночи', '🌙'];
  if (h < 12) return ['Доброе утро', '🌅'];
  if (h < 18) return ['Добрый день', '☀️'];
  return ['Добрый вечер', '🌆'];
}

function esc(s) {
  return (s || '').replace(/[&<>"]/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function renderView(view, p) {
  if (view === 'dashboard') return renderDashboard(p);
  if (view === 'habits')    return renderHabits(p);
  if (view === 'tasks')     return renderTasks(p);
}

function renderDashboard(p) {
  const body = document.getElementById('dash-body');
  const [g, gi] = greeting();
  const hello = p.name ? `${g}, ${esc(p.name)}!` : `${g}!`;
  const today = (p.today_tasks || []);
  body.innerHTML = `
    <div class="hero">
      <div class="hero-row"><div class="hero-hi">${gi} ${hello}</div></div>
      ${p.weather
        ? `<div class="hero-wx">${esc(p.weather)}</div><div class="hero-city">📍 ${esc(p.city)}</div>`
        : `<div class="hero-city">📍 ${esc(p.city || 'Город не задан')}</div>`}
    </div>
    <div class="dash-grid">
      <div class="stat stat-habits" onclick="switchTab('habits')" role="button" tabindex="0" title="Открыть привычки">
        <div class="stat-ico">🔁</div>
        <div><div class="stat-num">${p.habits_done}<span>/${p.habits_total}</span></div>
        <div class="stat-lbl">привычки · 🔥${p.best_streak}</div></div>
      </div>
      <div class="stat stat-tasks" onclick="switchTab('tasks')" role="button" tabindex="0" title="Открыть дела">
        <div class="stat-ico">✅</div>
        <div><div class="stat-num">${p.open_tasks}</div>
        <div class="stat-lbl">задач открыто</div></div>
      </div>
    </div>
    <div class="card wide"><h3>📅 На сегодня</h3>
      ${today.length ? `<div class="dash-list">${today.map(t => `<div>• ${esc(t)}</div>`).join('')}</div>`
                     : `<div class="sub">Планов нет — добавь во вкладке «Дела» или скажи в чат</div>`}
    </div>
    <div class="card wide"><h3>🔔 Ближайшее напоминание</h3>
      <div class="sub" style="color:var(--text);font-size:14px">${p.next_reminder ? esc(p.next_reminder) : 'Напоминаний нет'}</div>
    </div>
    ${renderAboutMe(p)}`;
}

function renderAboutMe(p) {
  const a = p.about || {};
  const facts = (a.facts || []);
  const mem = p.mem || {};
  const chips = [];
  if (mem.semantic) chips.push(`🧠 память ${mem.semantic}`);
  const hasContent = a.about || a.goals || facts.length || p.journal_last || chips.length;
  if (!hasContent) return '';
  return `
    <div class="card wide"><h3>🧠 Обо мне</h3>
      ${a.about ? `<div class="sub" style="color:var(--text);font-size:14px;margin-bottom:6px">${esc(a.about)}</div>` : ''}
      ${a.goals ? `<div class="sub" style="margin-bottom:8px">🎯 ${esc(a.goals)}</div>` : ''}
      ${chips.length ? `<div class="dash-chips">${chips.map(c => `<span class="chip">${esc(c)}</span>`).join('')}</div>` : ''}
      ${facts.length ? `<div class="dash-list" style="margin-top:8px">${facts.map(f => `<div>• ${esc(f)}</div>`).join('')}</div>` : ''}
      ${p.journal_last ? `<div class="sub" style="margin-top:8px;font-style:italic">📔 ${esc(p.journal_last)}</div>` : ''}
    </div>`;
}

function renderHabits(p) {
  const body = document.getElementById('habits-body');
  const habits = p.habits || [];
  if (!habits.length) {
    body.innerHTML = `<div class="empty">Привычек пока нет 🌱<br>Добавь сверху — и отмечай каждый день.</div>`;
    return;
  }
  body.innerHTML = habits.map(h => `
    <div class="row">
      <button class="check ${h.done_today ? 'on' : ''}" onclick="habitToggle(${h.id})" title="Отметить">${h.done_today ? '✓' : ''}</button>
      <div class="body"><div class="title">${esc(h.title)}</div></div>
      ${h.streak ? `<div class="streak">🔥${h.streak}</div>` : ''}
      <button class="x" onclick="habitDelete(${h.id})" title="Удалить">✕</button>
    </div>`).join('');
}

function renderTasks(p) {
  const body = document.getElementById('tasks-body');
  const tasks = p.tasks || [], reminders = p.reminders || [];
  let html = '';
  if (tasks.length) {
    html += `<div class="section-label">Задачи</div>`;
    html += tasks.map(t => `
      <div class="row">
        <button class="check" onclick="taskDone(${t.id})" title="Выполнено"></button>
        <div class="body"><div class="title">${esc(t.title)}</div>
          ${t.due ? `<div class="meta ${t.overdue ? 'overdue' : ''}">${esc(t.due)}</div>` : ''}</div>
        <button class="x" onclick="taskDelete(${t.id})" title="Удалить">✕</button>
      </div>`).join('');
  }
  if (reminders.length) {
    html += `<div class="section-label">Напоминания</div>`;
    html += reminders.map(r => `
      <div class="row">
        <button class="check on" onclick="reminderDone(${r.id})" title="Завершить" style="font-size:13px;line-height:1">🔔</button>
        <div class="body"><div class="title">${esc(r.text)}</div><div class="meta">${esc(r.when)}</div></div>
        <button class="x" onclick="reminderDelete(${r.id})" title="Удалить">✕</button>
      </div>`).join('');
  }
  body.innerHTML = html || `<div class="empty">Пусто ✨<br>Добавь задачу или напиши «напомни в 15:00 …»</div>`;
}

// Actions (re-rendered automatically when the server echoes the updated view)
function habitToggle(id)   { haptic('medium'); send({ type: 'habit_toggle', id }); }
function habitDelete(id)   { haptic('rigid');  send({ type: 'habit_delete', id }); }
function taskDone(id)      { haptic('medium'); send({ type: 'task_done', id }); }
function taskDelete(id)    { haptic('rigid');  send({ type: 'task_delete', id }); }
function reminderDone(id)  { haptic('medium'); send({ type: 'reminder_done', id }); }
function reminderDelete(id){ haptic('rigid');  send({ type: 'reminder_delete', id }); }
window.habitToggle = habitToggle;
window.habitDelete = habitDelete;
window.taskDone = taskDone;
window.taskDelete = taskDelete;
window.reminderDone = reminderDone;
window.reminderDelete = reminderDelete;

function addHabit() {
  const el = document.getElementById('habit-in');
  const t = el.value.trim();
  if (!t) return;
  send({ type: 'habit_add', title: t });
  el.value = '';
}
window.addHabit = addHabit;

function addTaskOrReminder() {
  const el = document.getElementById('task-in');
  const t = el.value.trim();
  if (!t) return;
  // «напомни …» → reminder, otherwise a task
  if (/^\s*(напомни|таймер|remind)/i.test(t)) send({ type: 'reminder_add', text: t });
  else send({ type: 'task_add', text: t });
  el.value = '';
}
window.addTaskOrReminder = addTaskOrReminder;

// Enter to add in task/habit inputs
document.getElementById('task-in')?.addEventListener('keydown', e => {
  if (e.key === 'Enter') { e.preventDefault(); addTaskOrReminder(); }
});
document.getElementById('habit-in')?.addEventListener('keydown', e => {
  if (e.key === 'Enter') { e.preventDefault(); addHabit(); }
});
