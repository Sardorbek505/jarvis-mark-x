import React from "react";
import { AbsoluteFill, Img, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { C, DISPLAY, DRIFT, EXIT, MONO, STAGGER, UNIT, tween } from "./theme";

/** Every scene enters with the same focus-pull and leaves with a short push — the one transition family. */
export const SceneShell: React.FC<{ children: React.ReactNode; exit?: boolean }> = ({ children, exit = true }) => {
  const f = useCurrentFrame();
  const { durationInFrames: d } = useVideoConfig();
  const out = exit ? tween(f, [d - 7, d], [0, 1], EXIT) : 0;
  return (
    <AbsoluteFill
      style={{
        opacity: tween(f, [0, 8], [0, 1]) * (1 - out),
        scale: String(tween(f, [0, UNIT + 4], [1.05, 1]) + out * 0.03),
        filter: `blur(${tween(f, [0, UNIT], [10, 0]) + out * 6}px)`,
      }}
    >
      {children}
    </AbsoluteFill>
  );
};

/**
 * Mask for text rising out of a line: clips only below the baseline, and only
 * while moving — an overflow:hidden box would also cut the glow into a rectangle.
 */
export const riseClip = (t: number): React.CSSProperties =>
  t < UNIT + 2 ? { clipPath: "inset(-200px -200px 0 -200px)" } : {};

/** Mono index label with a short rule, e.g. "02 — VISION". */
export const Label: React.FC<{ children: React.ReactNode; delay?: number; color?: string }> = ({
  children,
  delay = 0,
  color = C.core,
}) => {
  const f = useCurrentFrame() - delay;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 16, opacity: tween(f, [0, 8], [0, 1]) }}>
      <div style={{ width: tween(f, [0, UNIT], [0, 44]), height: 2, background: color, boxShadow: `0 0 12px ${color}` }} />
      <div
        style={{
          fontFamily: MONO,
          fontSize: 24,
          letterSpacing: "0.3em",
          color,
          translate: `${tween(f, [0, UNIT], [-12, 0])}px 0`,
        }}
      >
        {children}
      </div>
    </div>
  );
};

/** Headline lines rising out of masks, staggered; optional supporting line. */
export const Headline: React.FC<{
  lines: string[];
  sub?: string;
  delay?: number;
  size?: number;
  align?: "left" | "center";
}> = ({ lines, sub, delay = 0, size = 104, align = "left" }) => {
  const f = useCurrentFrame() - delay;
  return (
    <div style={{ textAlign: align }}>
      {lines.map((line, i) => {
        const t = f - i * (STAGGER + 1);
        return (
          <div key={line} style={{ padding: "0.04em 0 0.1em", ...riseClip(t) }}>
            <div
              style={{
                fontFamily: DISPLAY,
                fontWeight: 500,
                fontSize: size,
                lineHeight: 1.0,
                letterSpacing: "-0.01em",
                color: C.ink,
                textShadow: `0 0 40px ${C.glow}`,
                translate: `0 ${tween(t, [0, UNIT + 4], [110, 0])}%`,
                filter: `blur(${tween(t, [0, UNIT], [8, 0])}px)`,
              }}
            >
              {line}
            </div>
          </div>
        );
      })}
      {sub ? (
        <div
          style={{
            marginTop: 26,
            fontFamily: DISPLAY,
            fontSize: 36,
            color: C.muted,
            opacity: tween(f, [UNIT, UNIT * 2], [0, 1]),
            translate: `0 ${tween(f, [UNIT, UNIT * 2], [14, 0])}px`,
          }}
        >
          {sub}
        </div>
      ) : null}
    </div>
  );
};

/** Product art as a slow camera push (Ken Burns). */
export const Shot: React.FC<{
  src: string;
  from?: number;
  to?: number;
  origin?: string;
  style?: React.CSSProperties;
}> = ({ src, from = 1, to = 1.08, origin = "50% 50%", style }) => {
  const f = useCurrentFrame();
  const { durationInFrames: d } = useVideoConfig();
  return (
    <Img
      src={staticFile(`jarvis-ad/img/${src}`)}
      style={{
        width: "100%",
        height: "100%",
        objectFit: "cover",
        transformOrigin: origin,
        scale: String(tween(f, [0, d], [from, to], DRIFT)),
        ...style,
      }}
    />
  );
};

/** Four HUD corner brackets that close in on their box. */
export const Brackets: React.FC<{ delay?: number; size?: number; color?: string; inset?: number }> = ({
  delay = 0,
  size = 34,
  color = C.core,
  inset = -10,
}) => {
  const f = useCurrentFrame() - delay;
  const o = tween(f, [0, UNIT], [26, 0]);
  const corner = (rot: number, pos: React.CSSProperties) => (
    <div
      style={{
        position: "absolute",
        width: size,
        height: size,
        borderTop: `2px solid ${color}`,
        borderLeft: `2px solid ${color}`,
        rotate: `${rot}deg`,
        filter: `drop-shadow(0 0 8px ${color})`,
        opacity: tween(f, [0, 6], [0, 0.9]),
        ...pos,
      }}
    />
  );
  return (
    <>
      {corner(0, { left: inset - o, top: inset - o })}
      {corner(90, { right: inset - o, top: inset - o })}
      {corner(180, { right: inset - o, bottom: inset - o })}
      {corner(270, { left: inset - o, bottom: inset - o })}
    </>
  );
};

/** Glass HUD panel holding a product shot; a single light sheen crosses it on arrival. */
export const GlassFrame: React.FC<{
  x: number;
  y: number;
  w: number;
  h: number;
  delay?: number;
  children: React.ReactNode;
}> = ({ x, y, w, h, delay = 0, children }) => {
  const f = useCurrentFrame() - delay;
  return (
    <div
      style={{
        position: "absolute",
        left: x,
        top: y,
        width: w,
        height: h,
        opacity: tween(f, [0, 10], [0, 1]),
        translate: `${tween(f, [0, UNIT + 6], [60, 0])}px 0`,
      }}
    >
      <div
        style={{
          position: "absolute",
          inset: 0,
          overflow: "hidden",
          borderRadius: 22,
          border: `1px solid ${C.line}`,
          borderTopColor: "rgba(235,253,255,0.3)",
          boxShadow: `0 40px 120px rgba(0,0,0,0.6), 0 0 80px rgba(46,200,255,0.12), inset 0 1px rgba(255,255,255,0.12)`,
          background: C.void,
        }}
      >
        {children}
        <div
          style={{
            position: "absolute",
            top: "-10%",
            bottom: "-10%",
            width: "22%",
            left: `${tween(f, [8, 8 + UNIT * 3], [-30, 130])}%`,
            background: "linear-gradient(105deg, transparent, rgba(220,248,255,0.16) 50%, transparent)",
            rotate: "12deg",
          }}
        />
      </div>
      <Brackets delay={delay + 6} />
    </div>
  );
};

/** Small glass pill used for chips and status tags. */
export const Chip: React.FC<{
  children: React.ReactNode;
  delay?: number;
  color?: string;
  style?: React.CSSProperties;
}> = ({ children, delay = 0, color = C.core, style }) => {
  const f = useCurrentFrame() - delay;
  return (
    <div
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 14,
        padding: "14px 26px",
        borderRadius: 999,
        border: `1px solid ${color}55`,
        background: "rgba(6, 18, 30, 0.72)",
        boxShadow: `0 0 30px ${color}22, inset 0 1px rgba(255,255,255,0.1)`,
        fontFamily: MONO,
        fontSize: 26,
        letterSpacing: "0.12em",
        color: C.ink,
        opacity: tween(f, [0, 8], [0, 1]),
        scale: String(tween(f, [0, UNIT], [0.9, 1])),
        ...style,
      }}
    >
      <div style={{ width: 9, height: 9, borderRadius: 9, background: color, boxShadow: `0 0 12px ${color}` }} />
      {children}
    </div>
  );
};

/** Arc-reactor style rings; `draw` (0–1) strokes them on ring by ring, `energy` brightens them. */
export const CoreRings: React.FC<{ size: number; draw: number; energy?: number; spin?: number }> = ({
  size,
  draw,
  energy = 0.5,
  spin = 0,
}) => {
  // solid rings stroke on; dashed rings (which can't be stroked on cleanly) fade in instead
  const ring = (r: number, i: number, style: React.SVGProps<SVGCircleElement>) => {
    const { opacity = 1, strokeDasharray, ...rest } = style;
    const c = 2 * Math.PI * r;
    const p = Math.max(0, Math.min(1, draw * 4 - i * 0.7));
    const dashed = strokeDasharray !== undefined;
    return (
      <circle
        {...rest}
        cx={300}
        cy={300}
        r={r}
        fill="none"
        strokeDasharray={dashed ? strokeDasharray : `${c}`}
        strokeDashoffset={dashed ? 0 : c * (1 - p)}
        opacity={(dashed ? p : 1) * Number(opacity)}
      />
    );
  };
  return (
    <svg
      viewBox="0 0 600 600"
      width={size}
      height={size}
      style={{
        overflow: "visible",
        filter: `drop-shadow(0 0 ${6 + energy * 18}px ${C.glow}) drop-shadow(0 0 ${30 + energy * 60}px rgba(46,200,255,${0.2 + energy * 0.3}))`,
      }}
    >
      <g style={{ transformOrigin: "300px 300px", rotate: `${spin * 0.4}deg` }}>
        {ring(282, 0, { stroke: "rgba(206,247,255,0.22)", strokeWidth: 2 })}
      </g>
      <g style={{ transformOrigin: "300px 300px", rotate: `${-spin}deg` }}>
        {ring(256, 1, { stroke: C.core, strokeWidth: 3, strokeDasharray: "4 16", opacity: 0.8 })}
      </g>
      <g style={{ transformOrigin: "300px 300px", rotate: `${spin * 1.6}deg` }}>
        {ring(226, 2, { stroke: C.hot, strokeWidth: 9, strokeLinecap: "round", strokeDasharray: "420 1000" })}
      </g>
      <g style={{ transformOrigin: "300px 300px", rotate: `${-spin * 0.8}deg` }}>
        {ring(198, 3, { stroke: C.teal, strokeWidth: 3, strokeLinecap: "round", strokeDasharray: "110 40 24 70", opacity: 0.75 })}
      </g>
      {ring(150, 4, { stroke: "rgba(206,247,255,0.28)", strokeWidth: 2 })}
      <circle cx={300} cy={300} r={132} fill={`rgba(46,200,255,${0.04 + energy * 0.1})`} opacity={draw} />
      <circle cx={300} cy={300} r={18 + energy * 10} fill={C.hot} opacity={Math.min(1, draw * 1.4) * (0.5 + energy * 0.5)} />
    </svg>
  );
};

export const Center: React.FC<{ children: React.ReactNode; style?: React.CSSProperties }> = ({ children, style }) => (
  <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", ...style }}>{children}</AbsoluteFill>
);
