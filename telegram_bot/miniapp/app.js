/* JARVIS Mini App — client */

const tg = window.Telegram?.WebApp;
if (tg) { tg.expand(); tg.disableClosingConfirmation(); }

const USER_ID = tg?.initDataUnsafe?.user?.id ?? 0;
const wsProto = location.protocol === 'https:' ? 'wss:' : 'ws:';
const WS_URL  = `${wsProto}//${location.host}/ws?user_id=${USER_ID}`;

// DOM refs
const orb       = document.getElementById('orb');
const orbLabel  = document.getElementById('orb-label');
const msgs      = document.getElementById('messages');
const textIn    = document.getElementById('text-in');
const sendBtn   = document.getElementById('send-btn');
const pcBadge   = document.getElementById('pc-badge');
const connBadge = document.getElementById('conn-badge');

// ── State machine ─────────────────────────────────────────────────────────────
const S = {
  idle:       { label: 'Нажми чтобы говорить', cls: 'idle' },
  listening:  { label: 'Слушаю...',             cls: 'listening' },
  processing: { label: 'Думаю...',              cls: 'processing' },
  speaking:   { label: 'Говорю...',             cls: 'speaking' },
};

function setState(name) {
  const s = S[name] ?? S.idle;
  orbLabel.textContent = s.label;
  orb.className = s.cls;
}

// ── Message rendering ─────────────────────────────────────────────────────────
function addMsg(role, text) {
  document.querySelector('.typing')?.remove();
  const d = document.createElement('div');
  d.className = `msg ${role}`;
  d.textContent = text;
  msgs.appendChild(d);
  msgs.parentElement.scrollTop = msgs.parentElement.scrollHeight;
}

function addImage(b64, caption) {
  document.querySelector('.typing')?.remove();
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

// ── WebSocket ─────────────────────────────────────────────────────────────────
let ws = null;

function connect() {
  ws = new WebSocket(WS_URL);
  ws.binaryType = 'arraybuffer';

  ws.onopen = () => {
    connBadge.className = 'badge online';
    setState('idle');
    addMsg('sys', '● JARVIS подключён');
    reportClientInfo();
  };

  ws.onclose = () => {
    connBadge.className = 'badge';
    pcBadge.className   = 'badge';
    setState('idle');
    addMsg('sys', '○ Переподключение...');
    setTimeout(connect, 3500);
  };

  ws.onerror = () => addMsg('sys', '✕ Ошибка соединения');

  ws.onmessage = async ({ data }) => {
    let msg;
    try { msg = JSON.parse(data); } catch { return; }

    switch (msg.type) {
      case 'text':
        addMsg('bot', msg.text);
        // The server will follow with either 'audio' (real JARVIS voice) or
        // 'tts_failed' (use browser fallback). Just wait — no timer race.
        awaitVoice(msg.text);
        break;

      case 'image':
        addImage(msg.data, msg.caption);
        setState('idle');
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
        await playPCM(msg.data);
        setState('idle');
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
  addMsg('user', text);
  ws.send(JSON.stringify({ type: 'text', text, tts: voiceEnabled }));
  textIn.value = '';
  setState('processing');
  showTyping();
}

// ── Quick PC actions ──────────────────────────────────────────────────────────
function pcCmd(text) {
  if (ws?.readyState !== WebSocket.OPEN) { addMsg('sys', '⚠ Нет соединения'); return; }
  addMsg('user', text);
  ws.send(JSON.stringify({ type: 'text', text, tts: voiceEnabled }));
  setState('processing');
  showTyping();
}

// Expose for inline onclick
window.pcCmd = pcCmd;

// ── Voice control ─────────────────────────────────────────────────────────────
let recCtx    = null;
let playCtx   = null;
let workletNode = null;
let micStream   = null;
let listening   = false;

orb.addEventListener('click', toggleVoice);
orb.addEventListener('touchend', e => { e.preventDefault(); toggleVoice(); });

async function toggleVoice() {
  if (ws?.readyState !== WebSocket.OPEN) {
    addMsg('sys', '⚠ Нет соединения с сервером'); return;
  }
  listening ? stopVoice() : await startVoice();
}

async function startVoice() {
  try {
    recCtx = new AudioContext();
    if (recCtx.state === 'suspended') await recCtx.resume();

    micStream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 }
    });

    const workletURL = new URL('./worklet.js', location.href).href;
    await recCtx.audioWorklet.addModule(workletURL);

    const src = recCtx.createMediaStreamSource(micStream);
    workletNode = new AudioWorkletNode(recCtx, 'pcm-processor');

    workletNode.port.onmessage = ({ data }) => {
      if (ws?.readyState !== WebSocket.OPEN) return;
      const bytes = new Uint8Array(data.pcm);
      let b64 = '';
      for (let i = 0; i < bytes.length; i++) b64 += String.fromCharCode(bytes[i]);
      ws.send(JSON.stringify({ type: 'audio', data: btoa(b64) }));
    };

    src.connect(workletNode);
    listening = true;
    setState('listening');
    ws.send(JSON.stringify({ type: 'start_voice' }));
  } catch (e) {
    addMsg('sys', `⚠ Микрофон: ${e.message}`);
    stopVoice();
  }
}

function stopVoice() {
  micStream?.getTracks().forEach(t => t.stop());
  workletNode?.disconnect();
  if (recCtx) { recCtx.close().catch(() => {}); recCtx = null; }
  micStream = workletNode = null;
  listening = false;
  if (ws?.readyState === WebSocket.OPEN) {
    setState('processing');
    ws.send(JSON.stringify({ type: 'stop_voice', tts: voiceEnabled }));
  } else {
    setState('idle');
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
    u.onstart = () => setState('speaking');
    u.onend   = () => setState('idle');
    window.speechSynthesis.speak(u);
  } catch (e) { console.debug('speak:', e.message); }
}

// Voice on/off toggle
const voiceBtn = document.getElementById('voice-toggle');
if (voiceBtn) {
  voiceBtn.addEventListener('click', () => {
    voiceEnabled = !voiceEnabled;
    voiceBtn.textContent = voiceEnabled ? '🔊' : '🔇';
    voiceBtn.title = voiceEnabled ? 'Голос включён' : 'Голос выключен';
    if (!voiceEnabled) { clearVoiceWait(); window.speechSynthesis?.cancel(); }
  });
}

// ── Boot ──────────────────────────────────────────────────────────────────────
connect();

// ── Tabs & data views ─────────────────────────────────────────────────────────
let activeTab = 'chat';

function send(obj) {
  if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
}

function switchTab(name) {
  activeTab = name;
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.getElementById('view-' + name)?.classList.add('active');
  document.querySelectorAll('.tab').forEach(t =>
    t.classList.toggle('active', t.dataset.tab === name));
  // Data tabs fetch fresh state each time they're opened.
  if (['dashboard', 'tasks', 'habits'].includes(name)) {
    send({ type: 'get_data', view: name });
  }
}
window.switchTab = switchTab;

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
  const hello = p.name ? `Привет, ${esc(p.name)}!` : 'Привет!';
  const today = (p.today_tasks || []);
  body.innerHTML = `
    <div class="card wide" style="background:linear-gradient(135deg,#10243a,#0a0a1e)">
      <div class="big">${hello}</div>
      ${p.weather ? `<div class="sub">${esc(p.city)} · ${esc(p.weather)}</div>`
                  : `<div class="sub">${esc(p.city || '')}</div>`}
    </div>
    <div class="dash-grid">
      <div class="card"><h3>Привычки</h3><div class="big">${p.habits_done}/${p.habits_total}</div><div class="sub">сегодня · серия 🔥${p.best_streak}</div></div>
      <div class="card"><h3>Задачи</h3><div class="big">${p.open_tasks}</div><div class="sub">открыто всего</div></div>
      <div class="card wide"><h3>На сегодня</h3>
        ${today.length ? `<div class="dash-list">${today.map(t => `<div>• ${esc(t)}</div>`).join('')}</div>`
                       : `<div class="sub">Задач на сегодня нет — добавь во вкладке «Дела»</div>`}
      </div>
      <div class="card wide"><h3>Ближайшее напоминание</h3>
        <div class="sub" style="color:var(--text)">${p.next_reminder ? '🔔 ' + esc(p.next_reminder) : 'Напоминаний нет'}</div>
      </div>
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
      <button class="check ${h.done_today ? 'on' : ''}" onclick="habitToggle(${h.id})">${h.done_today ? '✓' : ''}</button>
      <div class="body"><div class="title">${esc(h.title)}</div></div>
      ${h.streak ? `<div class="streak">🔥${h.streak}</div>` : ''}
      <button class="x" onclick="habitDelete(${h.id})">✕</button>
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
        <button class="check" onclick="taskDone(${t.id})"></button>
        <div class="body"><div class="title">${esc(t.title)}</div>
          ${t.due ? `<div class="meta ${t.overdue ? 'overdue' : ''}">${esc(t.due)}</div>` : ''}</div>
      </div>`).join('');
  }
  if (reminders.length) {
    html += `<div class="section-label">Напоминания</div>`;
    html += reminders.map(r => `
      <div class="row">
        <div class="check on" style="cursor:default">🔔</div>
        <div class="body"><div class="title">${esc(r.text)}</div><div class="meta">${esc(r.when)}</div></div>
      </div>`).join('');
  }
  body.innerHTML = html || `<div class="empty">Пусто ✨<br>Добавь задачу или напиши «напомни в 15:00 …»</div>`;
}

// Actions (re-rendered automatically when the server echoes the updated view)
function habitToggle(id) { send({ type: 'habit_toggle', id }); }
function habitDelete(id) { send({ type: 'habit_delete', id }); }
function taskDone(id)    { send({ type: 'task_done', id }); }
window.habitToggle = habitToggle;
window.habitDelete = habitDelete;
window.taskDone = taskDone;

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
