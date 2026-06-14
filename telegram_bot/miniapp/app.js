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
const pcBadge  = document.getElementById('pc-badge');
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
  ws.binaryType = 'arraybuffer';

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
    let msg;
    try { msg = JSON.parse(data); } catch { return; }

    switch (msg.type) {
      case 'text':
        document.querySelector('.typing')?.remove();
        addMsg('bot', msg.text);
        setState('idle');
        break;

      case 'transcript_user':
        if (msg.text) addMsg('user', msg.text);
        break;

      case 'transcript_bot':
        document.querySelector('.typing')?.remove();
        if (msg.text) addMsg('bot', msg.text);
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
// Two separate AudioContexts to avoid sample rate conflict:
// recCtx  — any rate (browser default), worklet downsamples to 16 kHz
// playCtx — 24 000 Hz for Gemini audio playback
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
    // Recording context at browser's native rate (worklet downsamples internally)
    recCtx = new AudioContext();
    if (recCtx.state === 'suspended') await recCtx.resume();

    micStream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 }
    });

    // Load worklet — use absolute URL derived from page location
    const workletURL = new URL('./worklet.js', location.href).href;
    await recCtx.audioWorklet.addModule(workletURL);

    const src = recCtx.createMediaStreamSource(micStream);
    workletNode = new AudioWorkletNode(recCtx, 'pcm-processor');

    workletNode.port.onmessage = ({ data }) => {
      if (ws?.readyState !== WebSocket.OPEN) return;
      // Convert ArrayBuffer to base64
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
    ws.send(JSON.stringify({ type: 'stop_voice' }));
  } else {
    setState('idle');
  }
}

// ── Audio playback — 24 kHz int16 PCM from Gemini ────────────────────────────
let playChain = Promise.resolve();

async function playPCM(b64) {
  setState('speaking');
  playChain = playChain.then(async () => {
    try {
      // Decode base64 → Int16 samples → Float32
      const binary = atob(b64);
      const bytes  = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);

      const samples = new Int16Array(bytes.buffer);
      const f32     = new Float32Array(samples.length);
      for (let i = 0; i < samples.length; i++) f32[i] = samples[i] / 32768;

      // Playback context at 24 kHz — matches Gemini output
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
    } catch (e) {
      console.debug(`playPCM: ${e.message}`);
    }
  });
  await playChain;
}

// ── Boot ──────────────────────────────────────────────────────────────────────
connect();
