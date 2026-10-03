import React from "react";
import { AbsoluteFill, interpolate, random, useCurrentFrame } from "remotion";
import { APP_H, APP_W, AppWindow, HudCrop, HudVideo, ORB, PcShot } from "../devices";
import cardsJson from "../hudCards.json";
import {
  C,
  DISPLAY,
  ENTER,
  EXIT,
  MONO,
  MOVE,
  pop,
  Section,
  timeline,
  tween,
  ui,
  useVertical,
  voiceAt,
  wordTimings,
} from "../theme";

type Box = [number, number, number, number];
type HeadLayout = { x: number; y: number; w: number; align: "left" | "center"; size: number };
const CARDS = cardsJson as unknown as Record<string, Box>;

// Two layouts: 16:9 = text column left, app right; 9:16 = stacked.
const LAYOUT = {
  h: {
    head: { x: 96, y: 86, w: 660, align: "left", size: 92 } as HeadLayout,
    bubble: { x: 96, y: 300, w: 660 },
    reply: { x: 96, y: 488, w: 660, size: 46 },
    win: { x: 800, y: 132, w: 1040 },
    hero: { x: 96, y: 650, maxW: 660, maxH: 400 },
  },
  v: {
    head: { x: 60, y: 230, w: 960, align: "center", size: 84 } as HeadLayout,
    bubble: { x: 60, y: 410, w: 960 },
    win: { x: 40, y: 540, w: 1000 },
    reply: { x: 60, y: 1180, w: 960, size: 44 },
    hero: { x: 0, y: 1300, maxW: 980, maxH: 330 },
  },
};

/**
 * One feature of the app, shown working: the user's phrase → Jarvis thinking
 * (the real HUD takes the tool's shape) → the app's own result card, lifted
 * out of the window → Jarvis's reply. Timing comes from the section's marks.
 */
export const FeatureScene: React.FC<{ sec: Section }> = ({ sec }) => {
  const f = useCurrentFrame();
  const v = useVertical();
  const L = v ? LAYOUT.v : LAYOUT.h;
  const D = sec.durationInFrames;
  const m = sec.marks as Required<Section["marks"]>;
  const k = sec.index ?? 1;
  const dir = k % 2 ? 1 : -1;

  // transition: whip in from the side, push out
  const inP = tween(f, [0, 11], [0, 1]);
  const out = tween(f, [D - 6, D], [0, 1], EXIT);
  const winH = (L.win.w * APP_H) / APP_W;

  // camera inside the app: push to the orb while thinking, back out for the card
  const zoom = interpolate(f, [m.think - 6, m.think + 8, m.card - 2, m.card + 10], [1, 1.16, 1.16, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: MOVE,
  });
  const showPage = sec.page && f >= m.speak + 6;

  return (
    <AbsoluteFill
      style={{
        opacity: tween(f, [0, 5], [0, 1]) * (1 - out),
        translate: `${(1 - inP) * dir * 160}px 0`,
        scale: String(1.06 - 0.06 * inP + out * 0.04),
        filter: `blur(${(1 - inP) * 14 + out * 8}px)`,
      }}
    >
      <GiantWord text={sec.category ?? ""} dir={dir} vertical={v} />
      <Header sec={sec} k={k} L={L.head} />
      <Bubble sec={sec} f={f} m={m} L={L.bubble} vertical={v} />

      <AppWindow x={L.win.x} y={L.win.y} w={L.win.w} delay={3} zoom={zoom} zoomOrigin={`${(ORB.alone.x / APP_W) * 100}% ${(ORB.alone.y / APP_H) * 100}%`}>
        <div style={{ position: "relative", width: "100%", height: "100%" }}>
          <HudVideo absFrom={sec.from} />
          {sec.page ? (
            <div
              style={{
                position: "absolute",
                inset: 0,
                clipPath: `inset(0 0 0 ${showPage ? tween(f, [m.speak + 6, m.speak + 20], [100, 0], MOVE) : 100}%)`,
              }}
            >
              <PcShot page={sec.page} />
            </div>
          ) : null}
        </div>
      </AppWindow>
      <BeatGlow x={L.win.x} y={L.win.y} w={L.win.w} h={winH} />

      <HeroCard sec={sec} f={f} m={m} win={{ ...L.win, h: winH }} target={L.hero} vertical={v} />
      <Reply sec={sec} f={f} m={m} L={L.reply} align={v ? "center" : "left"} />
      <WhipFlash dir={dir} />
    </AbsoluteFill>
  );
};

/** Category word, huge and outlined, drifting behind everything. */
const GiantWord: React.FC<{ text: string; dir: number; vertical: boolean }> = ({ text, dir, vertical }) => {
  const f = useCurrentFrame();
  return (
    <div
      style={{
        position: "absolute",
        left: 0,
        right: 0,
        top: vertical ? 1180 : 560,
        whiteSpace: "nowrap",
        textAlign: "center",
        fontFamily: DISPLAY,
        fontWeight: 500,
        fontSize: vertical ? 300 : 420,
        lineHeight: 1,
        textTransform: "uppercase",
        color: "transparent",
        WebkitTextStroke: `2px rgba(48,208,190,0.10)`,
        translate: `${dir * (40 - f * 1.2)}px 0`,
        opacity: tween(f, [4, 16], [0, 1]),
      }}
    >
      {text}
    </div>
  );
};

const Header: React.FC<{ sec: Section; k: number; L: HeadLayout }> = ({ sec, k, L }) => {
  const f = useCurrentFrame();
  const total = timeline.featureCount;
  const title = sec.category ?? "";
  return (
    <div style={{ position: "absolute", left: L.x, top: L.y, width: L.w, textAlign: L.align }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 18,
          justifyContent: L.align === "center" ? "center" : "flex-start",
          fontFamily: MONO,
          fontSize: 26,
          letterSpacing: "0.2em",
          color: C.core,
          opacity: tween(f, [2, 8], [0, 1]),
        }}
      >
        <span>
          {String(k).padStart(2, "0")} / {String(total).padStart(2, "0")}
        </span>
        <div style={{ display: "flex", gap: 5 }}>
          {Array.from({ length: total }, (_, i) => {
            const done = i < k - 1;
            const cur = i === k - 1;
            return (
              <div
                key={i}
                style={{
                  width: cur ? 30 : 12,
                  height: 6,
                  borderRadius: 3,
                  background: done || cur ? C.core : "rgba(214,240,236,0.18)",
                  boxShadow: cur ? `0 0 ${10 + 8 * Math.abs(Math.sin(f / 4))}px ${C.core}` : "none",
                  scale: cur ? `${pop(f, 4, 7)} ${pop(f, 4, 7)}` : undefined,
                }}
              />
            );
          })}
        </div>
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", justifyContent: L.align === "center" ? "center" : "flex-start", marginTop: 10 }}>
        {title.split("").map((ch, i) => {
          const t = f - 3 - i * 1.5;
          const b = pop(f, 3 + i * 1.5, 8);
          return (
            <span
              key={i}
              style={{
                display: "inline-block",
                whiteSpace: "pre",
                fontFamily: DISPLAY,
                fontWeight: 500,
                fontSize: L.size,
                lineHeight: 1.05,
                color: C.ink,
                textShadow: `0 0 40px ${C.glow}`,
                opacity: tween(t, [0, 4], [0, 1]),
                translate: `0 ${(1 - b) * 70}px`,
                scale: String(Math.max(0, b)),
                filter: `blur(${tween(t, [0, 6], [8, 0])}px)`,
              }}
            >
              {ch}
            </span>
          );
        })}
      </div>
      <div
        style={{
          height: 4,
          marginTop: 12,
          width: `${tween(f, [6, 20], [0, 100], MOVE)}%`,
          marginLeft: L.align === "center" ? "auto" : 0,
          marginRight: L.align === "center" ? "auto" : 0,
          maxWidth: L.align === "center" ? 520 : undefined,
          background: `linear-gradient(90deg, ${C.core}, transparent)`,
          boxShadow: `0 0 18px ${C.glow}`,
        }}
      />
    </div>
  );
};

/** The user's command: a chat bubble typing itself, with a live mic wave. */
const Bubble: React.FC<{
  sec: Section;
  f: number;
  m: Required<Section["marks"]>;
  L: { x: number; y: number; w: number };
  vertical: boolean;
}> = ({ sec, f, m, L, vertical }) => {
  const text = sec.phrase ?? "";
  const t = f - m.phrase;
  const typed = Math.floor(interpolate(t, [3, m.think - m.phrase - 4], [0, text.length], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }));
  const listening = f >= m.phrase && f < m.think;
  return (
    <div
      style={{
        position: "absolute",
        left: L.x,
        top: L.y,
        width: L.w,
        display: "flex",
        justifyContent: vertical ? "flex-end" : "flex-start",
        opacity: tween(t, [0, 4], [0, 1]),
      }}
    >
      <div
        style={{
          scale: String(Math.max(0, pop(f, m.phrase, 9))),
          transformOrigin: vertical ? "100% 100%" : "0% 100%",
          maxWidth: L.w,
          padding: "18px 26px",
          borderRadius: 26,
          borderBottomRightRadius: vertical ? 6 : 26,
          borderBottomLeftRadius: vertical ? 26 : 6,
          background: "linear-gradient(135deg, rgba(48,208,190,0.92), rgba(28,160,146,0.92))",
          boxShadow: `0 12px 40px rgba(0,0,0,0.45), 0 0 ${listening ? 36 : 14}px rgba(70,232,128,${listening ? 0.45 : 0.15})`,
          color: "#03110f",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 12, fontFamily: MONO, fontSize: 18, letterSpacing: "0.18em", opacity: 0.75 }}>
          <MicWave active={listening} f={f} />
          {ui("you").toUpperCase()}
        </div>
        <div style={{ marginTop: 6, fontFamily: DISPLAY, fontWeight: 500, fontSize: vertical ? 42 : 38, lineHeight: 1.2, whiteSpace: "pre-wrap" }}>
          {text.slice(0, typed)}
          {typed < text.length ? <span style={{ opacity: Math.floor(f / 4) % 2 ? 1 : 0.2 }}>▌</span> : null}
        </div>
      </div>
    </div>
  );
};

const MicWave: React.FC<{ active: boolean; f: number }> = ({ active, f }) => (
  <div style={{ display: "flex", alignItems: "center", gap: 3, height: 22 }}>
    {Array.from({ length: 7 }, (_, i) => {
      const h = active ? 5 + 16 * Math.abs(Math.sin(f * 0.6 + i * 1.3)) * (0.5 + 0.5 * random(`mw${i}${Math.floor(f / 2)}`)) : 4;
      return <div key={i} style={{ width: 4, height: h, borderRadius: 2, background: "#03110f" }} />;
    })}
  </div>
);

/** The app's result card flies out of the window into close-up, with a burst. */
const HeroCard: React.FC<{
  sec: Section;
  f: number;
  m: Required<Section["marks"]>;
  win: { x: number; y: number; w: number; h: number };
  target: { x: number; y: number; maxW: number; maxH: number };
  vertical: boolean;
}> = ({ sec, f, m, win, target, vertical }) => {
  const box = CARDS[sec.id];
  if (!box) return null;
  const [bx, by, bw, bh] = box;
  const ws = win.w / APP_W; // window scale
  const hs = Math.min(target.maxW / bw, target.maxH / bh); // hero scale
  const t0 = m.card + 8; // let the app's own card animation play first
  if (f < t0) return null;
  const p = pop(f, t0, 11, 120); // overshoots the slot a little and settles — a bouncy landing
  // start exactly over the card inside the window, end at the hero slot
  const sx = win.x + bx * ws;
  const sy = win.y + by * ws;
  const tx = vertical ? (1080 - bw * hs) / 2 : target.x;
  const ty = target.y;
  const arc = Math.sin(p * Math.PI) * -60;
  const scale = ws / hs + (1 - ws / hs) * p; // HudCrop is rendered at hero scale
  const tilt = (1 - Math.min(1, p)) * 18;
  const burst = f - (t0 + 10);
  return (
    <>
      <div
        style={{
          position: "absolute",
          left: sx + (tx - sx) * p,
          top: sy + (ty - sy) * p + arc,
          transformOrigin: "0 0",
          transform: `perspective(1600px) scale(${scale}) rotateX(${tilt}deg) rotateY(${-tilt * 0.6}deg)`,
          borderRadius: 20,
          boxShadow: `0 40px 90px rgba(0,0,0,0.7), 0 0 ${30 + 40 * p}px rgba(255,138,52,${0.25 * p})`,
          outline: `2px solid rgba(255,138,52,${0.5 * p})`,
          outlineOffset: -2,
          overflow: "hidden",
        }}
      >
        <HudCrop absFrom={sec.from} box={box} scale={hs} />
        {/* sheen as it lands */}
        <div
          style={{
            position: "absolute",
            top: 0,
            bottom: 0,
            width: "30%",
            left: `${tween(f, [t0 + 10, t0 + 30], [-40, 140], MOVE)}%`,
            background: "linear-gradient(100deg, transparent, rgba(255,255,255,0.22), transparent)",
          }}
        />
      </div>
      {burst >= 0 && burst < 24
        ? Array.from({ length: 18 }, (_, i) => {
            const a = (i / 18) * Math.PI * 2 + random(`ba${sec.id}${i}`);
            const r = tween(burst, [0, 22], [0, 160 + 90 * random(`br${sec.id}${i}`)], ENTER);
            const cx = tx + (bw * hs) / 2;
            const cy = ty + (bh * hs) / 2;
            return (
              <div
                key={i}
                style={{
                  position: "absolute",
                  left: cx + Math.cos(a) * r * 1.6,
                  top: cy + Math.sin(a) * r,
                  width: 6,
                  height: 6,
                  borderRadius: 6,
                  background: i % 3 ? C.amber : C.hot,
                  boxShadow: `0 0 12px ${C.amber}`,
                  opacity: tween(burst, [0, 22], [1, 0]),
                }}
              />
            );
          })
        : null}
    </>
  );
};

/** Jarvis's answer, word by word, with the word being spoken lit up. */
export const Reply: React.FC<{
  sec: Section;
  f: number;
  m: Required<Section["marks"]>;
  L: { x: number; y: number; w: number; size: number };
  align: "left" | "center";
}> = ({ sec, f, m, L, align }) => {
  const t = f - m.speak;
  if (t < -2) return null;
  const words = wordTimings(sec.reply, sec.voFrames);
  const level = voiceAt(sec.from + f);
  return (
    <div style={{ position: "absolute", left: L.x, top: L.y, width: L.w, textAlign: align }}>
      <div
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 12,
          fontFamily: MONO,
          fontSize: 20,
          letterSpacing: "0.24em",
          color: C.amber,
          opacity: tween(t, [0, 6], [0, 1]),
        }}
      >
        <div
          style={{
            width: 12,
            height: 12,
            borderRadius: 12,
            background: C.amber,
            boxShadow: `0 0 ${10 + 30 * level}px ${C.amber}`,
            scale: String(1 + level * 0.6),
          }}
        />
        {ui("jarvis").toUpperCase()}
      </div>
      <div style={{ marginTop: 8, fontFamily: DISPLAY, fontWeight: 500, fontSize: L.size, lineHeight: 1.18 }}>
        {words.map((w, i) => {
          const wt = t - w.start;
          const speaking = t >= w.start && t < w.end + 2;
          return (
            <span
              key={i}
              style={{
                display: "inline-block",
                whiteSpace: "pre",
                color: speaking ? "#ffd2ad" : C.ink,
                textShadow: speaking ? `0 0 24px ${C.amber}` : `0 0 18px rgba(0,0,0,0.6)`,
                opacity: tween(wt, [-2, 2], [0, 1]),
                translate: `0 ${(1 - pop(t, w.start - 2, 9)) * 26}px`,
                scale: String(Math.max(0, pop(t, w.start - 2, 9))),
              }}
            >
              {w.word}
              {i < words.length - 1 ? " " : ""}
            </span>
          );
        })}
      </div>
    </div>
  );
};

/** The window's rim breathes on every beat of the music. */
const BeatGlow: React.FC<{ x: number; y: number; w: number; h: number }> = ({ x, y, w, h }) => {
  const f = useCurrentFrame();
  const b = timeline.beatFrames;
  const pulse = Math.exp(-(f % b) / 4);
  return (
    <div
      style={{
        position: "absolute",
        left: x - 2,
        top: y - 2,
        width: w + 4,
        height: h + 4,
        borderRadius: 18,
        border: `2px solid rgba(48,208,190,${0.12 + 0.35 * pulse})`,
        boxShadow: `0 0 ${20 + 30 * pulse}px rgba(48,208,190,${0.1 + 0.2 * pulse})`,
        pointerEvents: "none",
      }}
    />
  );
};

/** A bright streak across the frame on the cut. */
const WhipFlash: React.FC<{ dir: number }> = ({ dir }) => {
  const f = useCurrentFrame();
  if (f > 10) return null;
  const p = tween(f, [0, 10], [0, 1], MOVE);
  return (
    <AbsoluteFill style={{ pointerEvents: "none" }}>
      <div
        style={{
          position: "absolute",
          top: 0,
          bottom: 0,
          width: "18%",
          left: `${dir > 0 ? -20 + p * 130 : 100 - p * 130}%`,
          background: "linear-gradient(90deg, transparent, rgba(214,255,248,0.35), transparent)",
          filter: "blur(6px)",
          opacity: 1 - p,
        }}
      />
    </AbsoluteFill>
  );
};
