/* Шар из точек — тот же, что в ПК-версии (orb.py), для телефона.
 *
 * Точки лежат на сфере равномерно (спираль Фибоначчи) и «дышат»: радиус
 * сдвигают несколько медленных волн. От голоса волны вспухают. Пока Джарвис
 * работает, точки перетекают в фигуру по теме — глобус, эквалайзер,
 * киноплёнка, экран, дуговой реактор — волной сверху вниз, и обратно в шар.
 *
 * Всё считается от реального времени, фаза и поворот накапливаются —
 * смена скорости не дёргает шар. На телефоне точек меньше (≈1100 против
 * 2400 на ПК), рисование — пачками по глубине, в скрытой вкладке кадры
 * не считаются.
 */
(function () {
  'use strict';

  const GOLDEN = Math.PI * (3 - Math.sqrt(5));
  const TILT = 0.32, PERSP = 3.6;
  const TAU = Math.PI * 2;

  // Цвета состояний — как на ПК (ui.py, _STATE_RGB).
  const STATE_RGB = {
    idle:       [48, 208, 190],   // бирюзовый — ждёт
    listening:  [70, 232, 128],   // зелёный — слушает
    processing: [182, 226, 64],   // жёлто-зелёный — думает
    speaking:   [255, 138, 52],   // оранжевый — говорит
    offline:    [255, 70, 96],    // красный — нет связи
    boot:       [130, 214, 255],  // голубой — запуск
  };

  // ── раскладки фигур (массивы [x,y,z] по n точек) ─────────────────────────
  function fibonacci(n) {
    const out = new Float32Array(n * 3);
    for (let i = 0; i < n; i++) {
      const y = 1 - 2 * (i + 0.5) / n, r = Math.sqrt(1 - y * y), t = GOLDEN * i;
      out[i * 3] = Math.cos(t) * r; out[i * 3 + 1] = y; out[i * 3 + 2] = Math.sin(t) * r;
    }
    return out;
  }

  function spread(n, lengths) {
    const sum = lengths.reduce((a, b) => a + b, 0);
    const raw = lengths.map(l => n * l / sum);
    const counts = raw.map(Math.floor);
    let left = n - counts.reduce((a, b) => a + b, 0);
    raw.map((r, i) => [r - counts[i], i]).sort((a, b) => b[0] - a[0])
       .slice(0, left).forEach(([, i]) => counts[i]++);
    return counts;
  }

  function byHeight(pts) {            // сверху вниз — точка летит на свою высоту
    return pts.sort((a, b) => b[1] - a[1]);
  }

  function globe(n) {
    const lats = [-60, -30, 0, 30, 60].map(d => d * Math.PI / 180);
    const lons = [0, 30, 60, 90, 120, 150].map(d => d * Math.PI / 180);
    const lines = lats.map(a => ['lat', a, TAU * Math.cos(a)]).concat(lons.map(a => ['lon', a, TAU]));
    const counts = spread(n, lines.map(l => l[2]));
    const pts = [];
    lines.forEach(([kind, a], li) => {
      const k = counts[li];
      for (let j = 0; j < k; j++) {
        const t = TAU * j / k;
        if (kind === 'lat') pts.push([Math.cos(t) * Math.cos(a), Math.sin(a), Math.sin(t) * Math.cos(a)]);
        else pts.push([Math.cos(t) * Math.cos(a), Math.sin(t), Math.cos(t) * Math.sin(a)]);
      }
    });
    return byHeight(pts);
  }

  function film(n) {
    const R = 1.05, frames = 15;
    const rings = [[0.44, 1], [-0.44, 1], [0.27, 1], [-0.27, 1], [0.355, 0.5], [-0.355, 0.5]];
    const counts = spread(n, rings.map(([, w]) => TAU * R * w).concat(Array(frames).fill(0.54)));
    const pts = [];
    rings.forEach(([y, w], ri) => {
      const k = counts[ri];
      let ts = [];
      for (let j = 0; j < k; j++) ts.push(TAU * j / k);
      if (w < 1) {                                   // перфорация — пунктир
        const holes = ts.filter(t => Math.floor(t / TAU * 96) % 2 === 0);
        ts = Array.from({ length: k }, (_, j) => holes[j % holes.length]);
      }
      ts.forEach(t => pts.push([Math.cos(t) * R, y, Math.sin(t) * R]));
    });
    for (let f = 0; f < frames; f++) {
      const k = counts[rings.length + f], a = TAU * f / frames;
      for (let j = 0; j < k; j++) pts.push([Math.cos(a) * R, -0.27 + 0.54 * j / Math.max(1, k - 1), Math.sin(a) * R]);
    }
    return byHeight(pts);
  }

  function screen(n) {
    const w = 0.95, h = 0.535, kBorder = Math.floor(n * 0.38);
    const pts = [];
    spread(kBorder, [w * 2, h * 2, w * 2, h * 2]).forEach((k, side) => {
      for (let j = 0; j < k; j++) {
        const t = -1 + 2 * j / k;
        if (side === 0) pts.push([t * w, h, 0]);
        else if (side === 1) pts.push([w, -t * h, 0]);
        else if (side === 2) pts.push([-t * w, -h, 0]);
        else pts.push([-w, t * h, 0]);
      }
    });
    const rest = n - kBorder, cols = Math.floor(Math.sqrt(rest * w / h)), rows = Math.ceil(rest / cols);
    for (let r = 0; r < rows; r++) for (let c = 0; c < cols && pts.length < n; c++) {
      pts.push([-w * 0.9 + 1.8 * w * 0.9 * c / (cols - 1), h * 0.85 - 1.7 * h * 0.85 * r / (rows - 1), 0]);
    }
    return byHeight(pts);
  }

  function reactor(n) {                // [радиус, угол, крутится, яркость]
    const parts = [];
    const ring = (r, k, rot = 0, z = 0) => { for (let j = 0; j < k; j++) parts.push([r, TAU * j / k, rot, z]); };
    const kCore = Math.floor(n * 0.12);
    for (let i = 0; i < kCore; i++) parts.push([0.18 * Math.sqrt((i + 0.5) / kCore), i * GOLDEN, 0, 0.55]);
    ring(0.23, Math.floor(n * 0.05), 0, 0.4);
    ring(0.30, Math.floor(n * 0.05), 0, 0.2);
    const per = Math.floor(Math.floor(n * 0.08) / 10);
    for (let c = 0; c < 10; c++) {
      const a = (c + 0.5) * TAU / 10;
      for (let j = 0; j < per; j++) parts.push([0.31 + 0.29 * j / (per - 1), a, 1, 0.1]);
    }
    ring(0.60, Math.floor(n * 0.06));
    const kOut = Math.floor(n * 0.13), perCoil = Math.floor((n - parts.length - kOut) / 10), rows = 8;
    for (let c = 0; c < 10; c++) {
      const base = c * TAU / 10;
      for (let ri = 0; ri < rows; ri++) {
        const k = Math.floor(perCoil / rows) + (ri < perCoil % rows ? 1 : 0), r = 0.65 + 0.21 * ri / (rows - 1);
        for (let j = 0; j < k; j++) parts.push([r, base + (-13 + 26 * j / Math.max(1, k - 1)) * Math.PI / 180, 1, 0.15]);
      }
    }
    const rest = n - parts.length;
    ring(0.92, Math.floor(rest / 2));
    ring(1.0, rest - Math.floor(rest / 2));
    return parts.slice(0, n).sort((a, b) => b[0] * Math.sin(b[1]) - a[0] * Math.sin(a[1]));
  }

  // Детерминированный генератор — шар всегда одинаковый.
  function rng(seed) {
    let s = seed >>> 0;
    return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296);
  }

  class DotOrb {
    constructor(n = 1100) {
      this.n = n;
      const rand = rng(7);
      this.base = fibonacci(n);
      this.waves = [[2.1, 0.9, 1.0], [2.9, -1.3, 0.8], [3.6, 1.7, 0.55], [1.6, -0.7, 0.9]];
      const wsum = this.waves.reduce((a, w) => a + w[2], 0);
      this.proj = new Float32Array(n * 4);
      this.waveOff = this.waves.map(() => rand() * TAU);
      const dirs = this.waves.map(() => {
        const g = () => Math.sqrt(-2 * Math.log(rand() + 1e-9)) * Math.cos(TAU * rand());
        const d = [g(), g(), g()], l = Math.hypot(...d);
        return d.map(v => v / l);
      });
      this.wWeight = this.waves.map(w => w[2] / wsum);
      for (let i = 0; i < n; i++) for (let k = 0; k < 4; k++) {
        const b = this.base, d = dirs[k];
        this.proj[i * 4 + k] = (b[i * 3] * d[0] + b[i * 3 + 1] * d[1] + b[i * 3 + 2] * d[2]) * this.waves[k][0];
      }
      this.shapes = { globe: globe(n), film: film(n), screen: screen(n), reactor: reactor(n) };
      this.BARS = 40;
      const perBar = Math.floor(n / this.BARS);
      this.barIdx = new Int16Array(n); this.barU = new Float32Array(n); this.barCol = new Float32Array(n);
      for (let i = 0; i < n; i++) {
        this.barIdx[i] = Math.min(Math.floor(i / perBar), this.BARS - 1);
        this.barU[i] = (i % perBar) / Math.max(1, perBar - 1);
        this.barCol[i] = i % 2 ? 1 : -1;
      }
      this.barSpeed = Array.from({ length: this.BARS }, () => 4 + 5 * rand());
      this.barPhase = Array.from({ length: this.BARS }, () => rand() * TAU);
      this.delay = new Float32Array(n); this.dur = new Float32Array(n);
      for (let i = 0; i < n; i++) {
        this.delay[i] = 0.35 * (1 - this.base[i * 3 + 1]) / 2 + rand() * 0.12;
        this.dur[i] = 0.55 + rand() * 0.4;
      }
      this.energy = 0; this.phase = 0; this.yaw = 0; this.clock = 0;
      this.shape = 'sphere'; this.from = null; this.since = 1e9;
      this.pos = new Float32Array(n * 3);
      this.tgt = new Float32Array(n * 3);
      this._target('sphere', this.pos);
    }

    _unrotate(x, y, z, out, i) {        // «на экране» → мир: встанет лицом к зрителю
      const k = (PERSP - z) / PERSP; x *= k; y *= k;
      const ct = Math.cos(TILT), st = Math.sin(TILT);
      const y2 = y * ct + z * st, z2 = -y * st + z * ct;
      const c = Math.cos(this.yaw), s = Math.sin(this.yaw);
      out[i * 3] = x * c - z2 * s; out[i * 3 + 1] = y2; out[i * 3 + 2] = x * s + z2 * c;
    }

    _target(shape, out) {
      const n = this.n, e = this.energy, t = this.clock;
      if (shape === 'globe' || shape === 'film') {
        const pts = this.shapes[shape], g = 1 + (shape === 'globe' ? 0.04 : 0.03) * e;
        for (let i = 0; i < n; i++) { out[i * 3] = pts[i][0] * g; out[i * 3 + 1] = pts[i][1] * g; out[i * 3 + 2] = pts[i][2] * g; }
        return;
      }
      if (shape === 'music') {
        const drive = 0.35 + 0.65 * e;
        for (let i = 0; i < n; i++) {
          const b = this.barIdx[i], a = TAU * b / this.BARS;
          const wave = 0.5 + 0.5 * Math.sin(t * this.barSpeed[b] + this.barPhase[b]);
          const h = 0.08 + 0.62 * drive * wave * (0.65 + 0.35 * Math.sin(2 * a + t * 0.9));
          const r = 0.52 + this.barU[i] * h, tang = this.barCol[i] * 0.014;
          this._unrotate(Math.cos(a) * r - Math.sin(a) * tang, Math.sin(a) * r + Math.cos(a) * tang,
                         0.5 * this.barU[i] * h, out, i);
        }
        return;
      }
      if (shape === 'reactor') {
        const pts = this.shapes.reactor, pulseK = Math.sin(t * 4) * (0.05 + 0.1 * e);
        for (let i = 0; i < n; i++) {
          let [r, a, rot, z] = pts[i];
          a += rot * t * 0.45;
          if (r < 0.25) r *= 1 + pulseK;
          this._unrotate(Math.cos(a) * r, Math.sin(a) * r, z * (0.6 + 0.8 * e), out, i);
        }
        return;
      }
      if (shape === 'screen') {
        const pts = this.shapes.screen, scan = 0.535 - (t * 0.55) % 1.07;
        for (let i = 0; i < n; i++) {
          const d = (pts[i][1] - scan) / 0.05;
          this._unrotate(pts[i][0], pts[i][1], 0.6 * Math.exp(-d * d), out, i);
        }
        return;
      }
      const amp = 0.045 + 0.30 * e, P = this.proj, B = this.base, ph = this.phase;
      const sp = this.waves.map(w => w[1]), off = this.waveOff, W = this.wWeight;
      for (let i = 0; i < n; i++) {
        let w = 0;
        for (let k = 0; k < 4; k++) w += Math.sin(P[i * 4 + k] + ph * sp[k] + off[k]) * W[k];
        const m = 1 + amp * w;
        out[i * 3] = B[i * 3] * m; out[i * 3 + 1] = B[i * 3 + 1] * m; out[i * 3 + 2] = B[i * 3 + 2] * m;
      }
    }

    setShape(shape) {
      if (shape === this.shape || !(shape === 'sphere' || this.shapes[shape] || shape === 'music')) return;
      this.from = Float32Array.from(this.pos);
      this.shape = shape; this.since = 0;
    }

    step(dt, level, active) {
      dt = Math.max(0, Math.min(dt, 0.1));
      level = Math.max(0, Math.min(1, level));
      const rate = level > this.energy ? 6.5 : 2.8;
      this.energy += (level - this.energy) * (1 - Math.exp(-rate * dt));
      this.phase += dt * (0.55 + (active ? 0.5 : 0) + 2.2 * this.energy);
      const spin = { globe: 0.35, film: 0.55 }[this.shape] || 0;
      this.yaw += dt * (0.16 + 0.25 * this.energy + spin);
      this.clock += dt; this.since += dt;
      if (!this.from) { this._target(this.shape, this.pos); return; }
      this._target(this.shape, this.tgt);
      let done = true;
      for (let i = 0; i < this.n; i++) {
        let k = (this.since - this.delay[i]) / this.dur[i];
        k = k < 0 ? 0 : k > 1 ? 1 : k;
        if (k < 1) done = false;
        k = k * k * (3 - 2 * k);
        for (let c = 0; c < 3; c++) {
          const j = i * 3 + c;
          this.pos[j] = this.from[j] + (this.tgt[j] - this.from[j]) * k;
        }
      }
      if (done) this.from = null;
    }
  }

  // ── Рисование ───────────────────────────────────────────────────────────
  class OrbView {
    constructor(canvas) {
      this.cv = canvas;
      this.ctx = canvas.getContext('2d');
      const lowEnd = (navigator.hardwareConcurrency || 4) <= 4;
      this.orb = new DotOrb(lowEnd ? 850 : 1150);
      this.rgb = STATE_RGB.boot.slice();
      this.want = STATE_RGB.idle;
      this.state = 'idle';
      this.level = 0;                 // громкость 0..1 (голос или ответ)
      this.ring = 0;
      this.last = performance.now();
      this.order = new Uint16Array(this.orb.n);
      this.zs = new Float32Array(this.orb.n);
      this.xs = new Float32Array(this.orb.n);
      this.ys = new Float32Array(this.orb.n);
      this._resize();
      window.addEventListener('resize', () => this._resize());
      const frame = (now) => { this._frame(now); requestAnimationFrame(frame); };
      requestAnimationFrame(frame);
    }

    _resize() {
      const r = this.cv.getBoundingClientRect(), dpr = Math.min(window.devicePixelRatio || 1, 2);
      this.dpr = dpr;
      this.cv.width = Math.max(1, Math.round(r.width * dpr));
      this.cv.height = Math.max(1, Math.round(r.height * dpr));
    }

    setState(s) { this.state = s; this.want = STATE_RGB[s] || STATE_RGB.idle; }
    setShape(s) { this.orb.setShape(s); }

    _col(a, lift = 0) {
      const [r, g, b] = this.rgb.map(c => Math.round(c + (255 - c) * lift));
      return `rgba(${r},${g},${b},${Math.max(0, Math.min(1, a)).toFixed(3)})`;
    }

    _frame(now) {
      const dt = Math.min(0.1, (now - this.last) / 1000);
      this.last = now;
      if (document.hidden) return;
      const r = this.cv.getBoundingClientRect();
      if (Math.abs(r.width * this.dpr - this.cv.width) > 2 || Math.abs(r.height * this.dpr - this.cv.height) > 2) this._resize();
      const W = this.cv.width, H = this.cv.height;
      if (W < 4 || H < 4) return;

      // цвет — плавно к цели состояния
      const k = 1 - Math.exp(-4 * dt);
      for (let i = 0; i < 3; i++) this.rgb[i] += (this.want[i] - this.rgb[i]) * k;

      let level = this.level;
      if (this.state === 'speaking' && level < 0.02) level = 0.35 + 0.25 * Math.sin(now / 90) * Math.sin(now / 230);
      const active = this.state === 'processing' || this.state === 'speaking';
      this.orb.step(dt, level, active);
      this.ring += dt * (18 + 40 * this.orb.energy + (active ? 30 : 0));

      const ctx = this.ctx, e = this.orb.energy;
      ctx.clearRect(0, 0, W, H);
      // ниже центра — сверху место под плашку состояния
      const cx = W / 2, cy = H / 2 + 12 * this.dpr, R = Math.min(W, H - 30 * this.dpr) * 0.28 * (1 + 0.05 * e);

      // свечение
      const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, R * 1.9);
      const ga = (46 + 70 * e) / 255;
      g.addColorStop(0, this._col(ga * 1.0)); g.addColorStop(0.45, this._col(ga * 0.36)); g.addColorStop(1, this._col(0));
      ctx.fillStyle = g;
      ctx.fillRect(cx - R * 1.9, cy - R * 1.9, R * 3.8, R * 3.8);

      // кольцо с делениями и две бегущие дуги
      const rr = R * 1.34, lw = this.dpr;
      ctx.lineWidth = lw;
      ctx.strokeStyle = this._col(0.16);
      ctx.beginPath(); ctx.arc(cx, cy, rr, 0, TAU); ctx.stroke();
      ctx.strokeStyle = this._col(0.22);
      ctx.beginPath();
      for (let d = 0; d < 360; d += 6) {
        const a = (d + this.ring * 0.25) * Math.PI / 180, ln = (d % 30 === 0 ? 5 : 2.5) * lw;
        const c = Math.cos(a), s = Math.sin(a);
        ctx.moveTo(cx + c * rr, cy + s * rr); ctx.lineTo(cx + c * (rr - ln), cy + s * (rr - ln));
      }
      ctx.stroke();
      ctx.lineCap = 'round'; ctx.lineWidth = 1.6 * lw;
      ctx.strokeStyle = this._col((150 + 90 * e) / 255);
      for (const base of [this.ring, this.ring + 180]) {
        const a0 = base * Math.PI / 180;
        ctx.beginPath(); ctx.arc(cx, cy, rr, a0, a0 + (26 + 30 * e) * Math.PI / 180); ctx.stroke();
      }

      // точки: проекция и пачки по глубине
      const o = this.orb, n = o.n, P = o.pos;
      const c = Math.cos(o.yaw), s = Math.sin(o.yaw), ct = Math.cos(TILT), st = Math.sin(TILT);
      for (let i = 0; i < n; i++) {
        const x = P[i * 3] * c + P[i * 3 + 2] * s, z0 = -P[i * 3] * s + P[i * 3 + 2] * c, y0 = P[i * 3 + 1];
        const y = y0 * ct - z0 * st, z = y0 * st + z0 * ct, p = PERSP / (PERSP - z);
        this.xs[i] = cx + x * R * p; this.ys[i] = cy - y * R * p; this.zs[i] = z;
        this.order[i] = i;
      }
      const zs = this.zs;
      this.order.sort((a, b) => zs[a] - zs[b]);
      const LAYERS = 6, per = Math.ceil(n / LAYERS), scale = Math.max(0.8, Math.min(W, H) / (700 * this.dpr)) * this.dpr;
      for (let li = 0; li < LAYERS; li++) {
        const t = (li + 0.5) / LAYERS, size = (1.1 + 2.1 * t) * scale, h = size / 2;
        ctx.fillStyle = this._col((34 + 221 * Math.pow(t, 1.3)) / 255, 0.5 * t * t * t);
        ctx.beginPath();
        for (let j = li * per; j < Math.min(n, (li + 1) * per); j++) {
          const i = this.order[j];
          ctx.rect(this.xs[i] - h, this.ys[i] - h, size, size);
        }
        ctx.fill();
      }
    }
  }

  // ── Фигура по теме запроса (как _TOOL_SHAPE на ПК) ─────────────────────
  const TOPICS = [
    ['music',   /музык|песн|трек|spotify|спотифа|плейлист|альбом|громче|тише|пауз|следующ|предыдущ/i],
    ['film',    /фильм|кино|сериал|мульт/i],
    ['screen',  /скрин|экран|камер|снимок|фото|ютуб|youtube|видео|клип|посмотри/i],
    ['globe',   /погод|найди|поиск|новост|курс|кто так|что так|переведи|перевод|брифинг|гугл|google|интернет|сайт|браузер|где наход/i],
    ['reactor', /пк|компьют|блокир|выключи|перезагр|рабочий стол|enter|систем|таймер|запусти|открой|закрой|окн/i],
  ];
  function topicShape(text) {
    for (const [shape, rx] of TOPICS) if (rx.test(text || '')) return shape;
    return null;
  }

  window.JarvisOrb = { OrbView, DotOrb, topicShape, STATE_RGB };
})();
