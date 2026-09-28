import React from "react";
import { AbsoluteFill, random, useCurrentFrame } from "remotion";
import { C, DISPLAY, ENTER, MONO, SceneId, timeline, tween, voiceAt, wordTimings } from "./theme";

const SECTION: Record<SceneId, string> = {
  boot: "SYSTEM BOOT",
  title: "IDENTITY",
  voice: "01 / VOICE",
  vision: "02 / VISION",
  memory: "03 / MEMORY",
  control: "04 / CONTROL",
  end: "ONLINE",
};

const activeScene = (frame: number) =>
  timeline.scenes.find((s) => frame >= s.from && frame < s.from + s.durationInFrames) ??
  timeline.scenes[timeline.scenes.length - 1];

/** Background: void, drifting grid, depth orbs, dust. Sits under every scene. */
export const Backdrop: React.FC = () => {
  const f = useCurrentFrame();
  return (
    <AbsoluteFill style={{ background: C.void, overflow: "hidden" }}>
      <AbsoluteFill
        style={{
          background:
            "radial-gradient(circle at 38% 42%, rgba(46,200,255,0.10), transparent 46%), radial-gradient(circle at 70% 64%, rgba(63,240,200,0.06), transparent 42%), linear-gradient(154deg, #07121f, #02060d 58%, #050814)",
        }}
      />
      <AbsoluteFill
        style={{
          opacity: 0.22,
          backgroundImage:
            "linear-gradient(rgba(180,235,255,0.07) 1px, transparent 1px), linear-gradient(90deg, rgba(180,235,255,0.06) 1px, transparent 1px)",
          backgroundSize: "80px 80px",
          backgroundPosition: `${f * 0.25}px ${f * 0.4}px`,
          maskImage: "radial-gradient(circle at 50% 48%, #000, transparent 72%)",
        }}
      />
      <Dust />
    </AbsoluteFill>
  );
};

const Dust: React.FC = () => {
  const f = useCurrentFrame();
  return (
    <AbsoluteFill>
      {new Array(70).fill(0).map((_, i) => {
        const x = random(`dx${i}`) * 1920;
        const speed = 0.2 + random(`ds${i}`) * 0.7;
        const y = (((random(`dy${i}`) * 1180 - f * speed) % 1180) + 1180) % 1180 - 50;
        const size = 1 + random(`dz${i}`) * 2.4;
        const tw = 0.35 + 0.65 * Math.abs(Math.sin(f / (14 + random(`dt${i}`) * 30) + i));
        return (
          <div
            key={i}
            style={{
              position: "absolute",
              left: x,
              top: y,
              width: size,
              height: size,
              borderRadius: size,
              background: C.hot,
              boxShadow: `0 0 8px ${C.glow}`,
              opacity: 0.18 + 0.4 * tw * random(`do${i}`),
            }}
          />
        );
      })}
    </AbsoluteFill>
  );
};

/** Foreground HUD: corners, top bar, voice meter, scanlines, grain, vignette. */
export const HudOverlay: React.FC = () => {
  const f = useCurrentFrame();
  const s = activeScene(f);
  const local = f - s.from;
  const secs = f / timeline.fps;
  const tc = `T+00:${String(Math.floor(secs)).padStart(2, "0")}:${String(f % timeline.fps).padStart(2, "0")}`;
  const intro = tween(f, [4, 30], [0, 1]);
  const outro = tween(f, [timeline.durationInFrames - 14, timeline.durationInFrames], [1, 0]);

  const corner = (rot: number, pos: React.CSSProperties) => (
    <div
      style={{
        position: "absolute",
        width: 64,
        height: 64,
        borderTop: `2px solid ${C.core}`,
        borderLeft: `2px solid ${C.core}`,
        opacity: 0.4,
        rotate: `${rot}deg`,
        filter: `drop-shadow(0 0 10px ${C.glow})`,
        ...pos,
      }}
    />
  );

  return (
    <AbsoluteFill style={{ pointerEvents: "none", opacity: intro * outro }}>
      {corner(0, { left: 48, top: 48 })}
      {corner(90, { right: 48, top: 48 })}
      {corner(180, { right: 48, bottom: 48 })}
      {corner(270, { left: 48, bottom: 48 })}

      {/* top bar */}
      <div
        style={{
          position: "absolute",
          left: 132,
          right: 132,
          top: 62,
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          fontFamily: MONO,
          fontSize: 20,
          letterSpacing: "0.26em",
          color: C.muted,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <div style={{ width: 8, height: 8, borderRadius: 8, background: C.hot, boxShadow: `0 0 14px ${C.core}` }} />
          J.A.R.V.I.S // MARK X
        </div>
        <div style={{ overflow: "hidden", height: 26 }}>
          <div
            key={s.id}
            style={{
              color: C.core,
              translate: `0 ${tween(local, [0, 10], [100, 0], ENTER)}%`,
            }}
          >
            {SECTION[s.id]}
          </div>
        </div>
        <div>{tc}</div>
      </div>

      <VoiceMeter />

      {/* optics */}
      <AbsoluteFill
        style={{
          opacity: 0.35,
          backgroundImage: "repeating-linear-gradient(0deg, rgba(220,250,255,0.035) 0 1px, transparent 1px 4px)",
        }}
      />
      <Grain />
      <AbsoluteFill
        style={{ background: "radial-gradient(ellipse at 50% 48%, transparent 45%, rgba(0,0,0,0.25) 70%, rgba(0,0,0,0.8) 100%)" }}
      />
    </AbsoluteFill>
  );
};

const GRAIN = `url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='.5'/%3E%3C/svg%3E")`;

const Grain: React.FC = () => {
  const f = useCurrentFrame();
  return (
    <AbsoluteFill
      style={{
        opacity: 0.09,
        mixBlendMode: "screen",
        backgroundImage: GRAIN,
        backgroundSize: "256px 256px",
        backgroundPosition: `${Math.floor(random(`gx${f}`) * 256)}px ${Math.floor(random(`gy${f}`) * 256)}px`,
      }}
    />
  );
};

/** Bottom-left equalizer that follows the voice-over. */
const VoiceMeter: React.FC = () => {
  const f = useCurrentFrame();
  return (
    <div style={{ position: "absolute", left: 132, bottom: 70, display: "flex", alignItems: "flex-end", gap: 5, height: 40 }}>
      {new Array(14).fill(0).map((_, i) => {
        const level = voiceAt(f - i * 0.7);
        const h = 4 + 34 * level * (0.55 + 0.45 * Math.abs(Math.sin(f / 3 + i * 1.7)));
        return <div key={i} style={{ width: 5, height: h, background: C.core, opacity: 0.35 + level * 0.6, borderRadius: 2 }} />;
      })}
      <div style={{ marginLeft: 14, fontFamily: MONO, fontSize: 16, letterSpacing: "0.24em", color: C.dim }}>VOICE LINK</div>
    </div>
  );
};

/** Karaoke-style captions for the voice-over (for muted autoplay). */
export const Captions: React.FC<{ hide?: SceneId[] }> = ({ hide = [] }) => {
  const f = useCurrentFrame();
  const s = activeScene(f);
  if (hide.includes(s.id)) return null;
  const t = f - s.from - s.vo.from;
  const d = s.vo.durationInFrames;
  const vis = tween(t, [-6, 4], [0, 1]) * tween(t, [d + 4, d + 14], [1, 0]);
  if (vis <= 0) return null;
  const words = wordTimings(s.vo.text, d);
  return (
    <AbsoluteFill style={{ justifyContent: "flex-end", alignItems: "center", paddingBottom: 92, opacity: vis }}>
      <div
        style={{
          maxWidth: 1360,
          textAlign: "center",
          fontFamily: DISPLAY,
          fontSize: 42,
          lineHeight: 1.3,
          padding: "12px 30px",
          borderRadius: 14,
          background: "rgba(2,8,16,0.55)",
          translate: `0 ${tween(t, [-6, 6], [12, 0])}px`,
        }}
      >
        {words.map((w, i) => {
          const on = tween(t, [w.start - 2, w.start + 3], [0, 1]);
          return (
            <span key={i} style={{ color: on > 0.5 ? C.ink : C.dim, opacity: 0.45 + 0.55 * on }}>
              {w.word}
              {i < words.length - 1 ? " " : ""}
            </span>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};
