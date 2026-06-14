/* JARVIS Mini App — client */

const tg = window.Telegram?.WebApp;
if (tg) { tg.expand(); tg.disableClosingConfirmation(); }

const USER_ID = tg?.initDataUnsafe?.user?.id ?? 0;
const wsProto = location.protocol === 'https:' ? 'wss:' : 'ws:';
const WS_URL  = `${wsProto}//${location.host}/ws?user_id=${USER_ID}`;

// DOM refs
const orb      = document.getElementById('orb');
const orbLabel = document.getElementById('orb-label');
const msgs     = document.getElementById('messages');
const textIn   = document.getElementById('text-in');
const sendBtn  = document.getElementById('send-btn');
const pcDot    = document.getElementById('pc-dot');
const pcBadge  = document.getElementById('pc-badge');
const connDot  = document.getElementById('conn-dot');
const connBadge= document.getElementById('conn-badge');

// ── State ─────────────────────────────────────────────────────────────────────
const S = {
  idle:       { label: 'Нажми чтобы говорить',    cls: 'idle' },
  listening:  { label: 'Слушаю...',               cls: 'listening' },
  processing: { label: 'Думаю...',                cls: 'processing' },
  speaking:   { label: 'Говорю...',               cls: 'speaking' },
};

function setState(name) {
  const s = S[name] ?? S.idle;
  orbLabel.textContent = s.label;
  orb.className = s.cls;
}

// ── Messages ──────────────────────────────────────────────────────────────────
function addMsg(role, text) {
  document.querySelector('.typing')?.remove();
  const d = document.createElement('div');
  d.className = `msg ${role}`;
  d.textContent = text;
  msgs.appendChild(d);
  msgs.parentElement.scrollTop = msgs.parentElement.scrollHeight;
}

function showTyping() {
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

  ws.onopen = () => {
    connBadge.className = 'badge online';
    setState('idle');
    addMsg('sys', '● JARVIS подключён');
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
    const msg = JSON.parse(data);

    switch (msg.type) {
      case 'text':
        document.querySelector('.typing')?.remove();
        addMsg('bot', msg.text);
        setState('idle');
        break;

      case 'transcript_user':
        addMsg('user', msg.text);
        break;

      case 'transcript_bot':
        document.querySelector('.typing')?.remove();
        addMsg('bot', msg.text);
        break;

      case 'audio':
        await playPCM(msg.data);
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
    }
  };
}

// ── Text send ─────────────────────────────────────────────────────────────────
sendBtn.addEventListener('click', sendText);
textIn.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendText(); }
});

function sendText() {
  const text = textIn.value.trim();
  if (!text || ws?.readyState !== WebSocket.OPEN) return;
  addMsg('user', text);
  ws.send(JSON.stringify({ type: 'text', text }));
  textIn.value = '';
  setState('processing');
  showTyping();
}

// ── Voice ─────────────────────────────────────────────────────────────────────
let audioCtx    = null;
let workletNode = null;
let micStream   = null;
let listening   = false;

orb.addEventListener('click', toggleVoice);
orb.addEventListener('touchend', e => { e.preventDefault(); toggleVoice(); });

async function toggleVoice() {
  listening ? stopVoice() : await startVoice();
}

async function startVoice() {
  try {
    if (!audioCtx) audioCtx = new AudioContext();
    if (audioCtx.state === 'suspended') await audioCtx.resume();

    micStream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, sampleRate: 16000 }
    });

    await audioCtx.audioWorklet.addModule('worklet.js');
    const src = audioCtx.createMediaStreamSource(micStream);
    workletNode = new AudioWorkletNode(audioCtx, 'pcm-processor');

    workletNode.port.onmessage = ({ data }) => {
      if (ws?.readyState !== WebSocket.OPEN) return;
      const b64 = btoa(String.fromCharCode(...new Uint8Array(data.pcm)));
      ws.send(JSON.stringify({ type: 'audio', data: b64 }));
    };

    src.connect(workletNode);
    listening = true;
    setState('listening');
    ws?.send(JSON.stringify({ type: 'start_voice' }));
  } catch (e) {
    addMsg('sys', `⚠ Микрофон: ${e.message}`);
  }
}

function stopVoice() {
  micStream?.getTracks().forEach(t => t.stop());
  workletNode?.disconnect();
  micStream = workletNode = null;
  listening = false;
  setState('processing');
  ws?.send(JSON.stringify({ type: 'stop_voice' }));
}

// ── Audio playback (24 kHz int16 PCM) ────────────────────────────────────────
let playChain = Promise.resolve();

async function playPCM(b64) {
  setState('speaking');
  playChain = playChain.then(async () => {
    try {
      const bytes   = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
      const samples = new Int16Array(bytes.buffer);
      const f32     = new Float32Array(samples.length);
      for (let i = 0; i < samples.length; i++) f32[i] = samples[i] / 32768;

      if (!audioCtx) audioCtx = new AudioContext({ sampleRate: 24000 });
      const buf = audioCtx.createBuffer(1, f32.length, 24000);
      buf.copyToChannel(f32, 0);

      await new Promise(res => {
        const src = audioCtx.createBufferSource();
        src.buffer = buf;
        src.connect(audioCtx.destination);
        src.onended = res;
        src.start();
      });
    } catch (_) { /* ignore stale chunks */ }
  });
  await playChain;
}

// ── Boot ──────────────────────────────────────────────────────────────────────
connect();
