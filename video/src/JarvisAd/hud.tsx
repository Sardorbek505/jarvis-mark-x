import React from "react";
import { AbsoluteFill, random, useCurrentFrame, useVideoConfig } from "remotion";
import { C, timeline, tween } from "./theme";

/** Background: void, drifting grid, depth orbs, dust. Sits under every scene. */
export const Backdrop: React.FC = () => {
  const f = useCurrentFrame();
  return (
    <AbsoluteFill style={{ background: C.void, overflow: "hidden" }}>
      <AbsoluteFill
        style={{
          background:
            "radial-gradient(circle at 38% 42%, rgba(48,208,190,0.09), transparent 46%), radial-gradient(circle at 70% 64%, rgba(255,138,52,0.04), transparent 42%), linear-gradient(154deg, #061012, #03070a 58%, #05080c)",
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
  const { width, height } = useVideoConfig();
  const span = height + 100;
  return (
    <AbsoluteFill>
      {new Array(70).fill(0).map((_, i) => {
        const x = random(`dx${i}`) * width;
        const speed = 0.2 + random(`ds${i}`) * 0.7;
        const y = (((random(`dy${i}`) * span - f * speed) % span) + span) % span - 50;
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

/** Foreground optics over everything: corner marks, scanlines, grain, vignette. */
export const HudOverlay: React.FC = () => {
  const f = useCurrentFrame();
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


      {/* optics */}
      <AbsoluteFill
        style={{
          opacity: 0.18,
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
        opacity: 0.06,
        mixBlendMode: "screen",
        backgroundImage: GRAIN,
        backgroundSize: "256px 256px",
        backgroundPosition: `${Math.floor(random(`gx${f}`) * 256)}px ${Math.floor(random(`gy${f}`) * 256)}px`,
      }}
    />
  );
};
