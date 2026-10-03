import React from "react";
import { Img, OffthreadVideo, interpolate, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { C, ENTER, UNIT, tween } from "./theme";

// Captured by scripts/capture/: the PC app at 1920×1200, the Mini App at 393×852 CSS px (3×).
export const APP_W = 1920;
export const APP_H = 1200;
export const PHONE_W = 393;
export const PHONE_H = 852;
/** Centre of the HUD orb inside the PC app capture — the app slides it left while a result card is open. */
export const ORB = { alone: { x: 1027, y: 590 }, withCard: { x: 747, y: 570 } };

/** The live HUD recording, frame-locked to the ad's absolute timeline. */
export const HudVideo: React.FC<{ absFrom: number; style?: React.CSSProperties }> = ({ absFrom, style }) => (
  <OffthreadVideo
    src={staticFile("jarvis-ad/pc/hud.mp4")}
    trimBefore={absFrom}
    muted
    style={{ width: "100%", height: "100%", display: "block", ...style }}
  />
);

export const PcShot: React.FC<{ page: string }> = ({ page }) => (
  <Img src={staticFile(`jarvis-ad/pc/${page}.png`)} style={{ width: "100%", height: "100%", display: "block" }} />
);

/**
 * The desktop app window, floating in 3D. `zoom` pushes the camera into the
 * UI (origin in % of the window) without growing the window itself.
 */
export const AppWindow: React.FC<{
  x: number;
  y: number;
  w: number;
  delay?: number;
  rotY?: number;
  zoom?: number;
  zoomOrigin?: string;
  children: React.ReactNode;
}> = ({ x, y, w, delay = 0, rotY = 0, zoom = 1, zoomOrigin = "50% 50%", children }) => {
  const f = useCurrentFrame() - delay;
  const h = (w * APP_H) / APP_W;
  const inP = tween(f, [0, UNIT * 2], [0, 1]);
  return (
    <div
      style={{
        position: "absolute",
        left: x,
        top: y,
        width: w,
        height: h,
        opacity: tween(f, [0, 8], [0, 1]),
        transform: `perspective(2600px) translateY(${(1 - inP) * 70}px) rotateX(${(1 - inP) * 16}deg) rotateY(${rotY * inP + (1 - inP) * rotY * 1.6}deg)`,
        transformStyle: "preserve-3d",
      }}
    >
      {/* soft floor glow */}
      <div
        style={{
          position: "absolute",
          left: "8%",
          right: "8%",
          bottom: -60,
          height: 90,
          borderRadius: "50%",
          background: `radial-gradient(ellipse, ${C.glow}, transparent 70%)`,
          filter: "blur(24px)",
          opacity: 0.45,
        }}
      />
      <div
        style={{
          position: "absolute",
          inset: 0,
          overflow: "hidden",
          borderRadius: 16,
          border: "1px solid rgba(255,255,255,0.14)",
          boxShadow: "0 50px 140px rgba(0,0,0,0.75), 0 0 0 1px rgba(0,0,0,0.6), 0 0 90px rgba(48,208,190,0.10)",
          background: C.void,
        }}
      >
        <div style={{ width: "100%", height: "100%", scale: String(zoom), transformOrigin: zoomOrigin }}>{children}</div>
        {/* glass reflection */}
        <div
          style={{
            position: "absolute",
            inset: 0,
            background: "linear-gradient(125deg, rgba(255,255,255,0.07), transparent 32%, transparent 70%, rgba(255,255,255,0.03))",
            pointerEvents: "none",
          }}
        />
      </div>
    </div>
  );
};

export type Screen = { src: string; from: number };

/** An iPhone-style frame showing Mini App screenshots; later screens slide in over earlier ones. */
export const Phone: React.FC<{
  x: number;
  y: number;
  w: number;
  screens: Screen[];
  delay?: number;
  rotY?: number;
  children?: React.ReactNode;
}> = ({ x, y, w, screens, delay = 0, rotY = 0, children }) => {
  const frame = useCurrentFrame();
  const f = frame - delay;
  const bezel = w * 0.034;
  const sw = w - 2 * bezel;
  const sh = (sw * PHONE_H) / PHONE_W;
  const inP = tween(f, [0, UNIT * 2], [0, 1]);
  const current = screens.filter((s) => frame >= s.from);
  const shown = current.slice(-2);
  return (
    <div
      style={{
        position: "absolute",
        left: x,
        top: y,
        width: w,
        height: sh + 2 * bezel,
        opacity: tween(f, [0, 8], [0, 1]),
        transform: `perspective(2200px) translateY(${(1 - inP) * 90}px) rotateY(${rotY}deg) rotateZ(${(1 - inP) * -4}deg)`,
      }}
    >
      <div
        style={{
          position: "absolute",
          inset: 0,
          borderRadius: w * 0.16,
          background: "linear-gradient(145deg, #2c343c, #0b0f13 40%, #1c2329)",
          boxShadow: "0 60px 120px rgba(0,0,0,0.75), inset 0 0 0 2px rgba(255,255,255,0.08), 0 0 70px rgba(48,208,190,0.12)",
        }}
      />
      <div
        style={{
          position: "absolute",
          left: bezel,
          top: bezel,
          width: sw,
          height: sh,
          overflow: "hidden",
          borderRadius: w * 0.13,
          background: C.void,
        }}
      >
        {shown.map((s, i) => {
          const t = frame - s.from;
          const isNew = i === shown.length - 1 && current.length > 1;
          return (
            <Img
              key={s.src + s.from}
              src={staticFile(`jarvis-ad/phone/${s.src}.png`)}
              style={{
                position: "absolute",
                inset: 0,
                width: "100%",
                height: "100%",
                opacity: isNew ? tween(t, [0, 8], [0, 1]) : 1,
                translate: isNew ? `${tween(t, [0, UNIT], [26, 0])}px 0` : undefined,
              }}
            />
          );
        })}
        <div style={{ position: "absolute", inset: 0 }}>{children}</div>
        {/* dynamic island */}
        <div
          style={{
            position: "absolute",
            left: "50%",
            top: sw * 0.022,
            width: sw * 0.29,
            height: sw * 0.082,
            translate: "-50% 0",
            borderRadius: 999,
            background: "#000",
          }}
        />
        <div
          style={{
            position: "absolute",
            inset: 0,
            background: "linear-gradient(120deg, rgba(255,255,255,0.08), transparent 30%)",
            pointerEvents: "none",
          }}
        />
      </div>
    </div>
  );
};

/** A finger tap at Mini App CSS coordinates (393-wide space), for use inside <Phone>. */
export const Tap: React.FC<{ at: number; x: number; y: number; phoneW: number }> = ({ at, x, y, phoneW }) => {
  const t = useCurrentFrame() - at;
  if (t < -4 || t > 22) return null;
  const k = (phoneW * (1 - 2 * 0.034)) / PHONE_W;
  const press = interpolate(t, [-4, 0, 4], [0.6, 1, 0.85], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  return (
    <div style={{ position: "absolute", left: x * k, top: y * k }}>
      <div
        style={{
          position: "absolute",
          width: 44,
          height: 44,
          margin: -22,
          borderRadius: 44,
          background: "rgba(255,255,255,0.55)",
          boxShadow: `0 0 24px ${C.glow}`,
          scale: String(press),
          opacity: tween(t, [-4, 0], [0, 1]) * tween(t, [6, 14], [1, 0]),
        }}
      />
      <div
        style={{
          position: "absolute",
          width: 44,
          height: 44,
          margin: -22,
          borderRadius: 44,
          border: `2px solid ${C.core}`,
          scale: String(tween(t, [0, 18], [1, 2.6], ENTER)),
          opacity: tween(t, [0, 18], [0.9, 0]),
        }}
      />
    </div>
  );
};

/** Dashed link between two points with a pulse that travels a → b at each `pulses` frame. */
export const Beam: React.FC<{
  a: [number, number];
  b: [number, number];
  delay?: number;
  pulses?: number[];
  color?: string;
}> = ({ a, b, delay = 0, pulses = [], color = C.core }) => {
  const f = useCurrentFrame();
  const { width, height } = useVideoConfig();
  const draw = tween(f - delay, [0, UNIT * 2], [0, 1]);
  const mx = (a[0] + b[0]) / 2;
  const my = Math.min(a[1], b[1]) - 80;
  const d = `M${a[0]},${a[1]} Q${mx},${my} ${b[0]},${b[1]}`;
  const at = (p: number) => {
    const u = 1 - p;
    return [u * u * a[0] + 2 * u * p * mx + p * p * b[0], u * u * a[1] + 2 * u * p * my + p * p * b[1]];
  };
  return (
    <svg width={width} height={height} style={{ position: "absolute", inset: 0, overflow: "visible", pointerEvents: "none" }}>
      <path
        d={d}
        fill="none"
        stroke={color}
        strokeWidth={2}
        strokeDasharray="6 10"
        strokeDashoffset={-f * 1.5}
        opacity={0.55 * draw}
        style={{ filter: `drop-shadow(0 0 6px ${color})` }}
      />
      {pulses.map((p0) => {
        const t = (f - p0) / 10;
        if (t < 0 || t > 1) return null;
        const [px, py] = at(ENTER(t));
        return <circle key={p0} cx={px} cy={py} r={7} fill={C.hot} style={{ filter: `drop-shadow(0 0 12px ${color})` }} />;
      })}
    </svg>
  );
};

/** Close-up of the live HUD orb: the capture is shifted so the orb sits at the box centre, then scaled. */
export const OrbView: React.FC<{
  absFrom: number;
  size: number;
  center: { x: number; y: number };
  scale?: number;
  style?: React.CSSProperties;
}> = ({ absFrom, size, center, scale = 1, style }) => (
  <div style={{ position: "relative", width: size, height: size, overflow: "hidden", ...style }}>
    <div
      style={{
        position: "absolute",
        left: size / 2 - center.x,
        top: size / 2 - center.y,
        width: APP_W,
        height: APP_H,
        transformOrigin: `${center.x}px ${center.y}px`,
        scale: String(scale),
      }}
    >
      <HudVideo absFrom={absFrom} />
    </div>
  </div>
);

/**
 * A rectangle of the live HUD recording, shown at `scale` — used to lift the
 * app's result card out of the window in close-up. `box` is [x, y, w, h] in
 * capture pixels (measured per feature by capture_pc.py -> hudCards.json).
 */
export const HudCrop: React.FC<{
  absFrom: number;
  box: [number, number, number, number];
  scale: number;
  style?: React.CSSProperties;
}> = ({ absFrom, box: [x, y, w, h], scale, style }) => (
  <div style={{ position: "relative", width: w * scale, height: h * scale, overflow: "hidden", ...style }}>
    <div
      style={{
        position: "absolute",
        left: -x * scale,
        top: -y * scale,
        width: APP_W * scale,
        height: APP_H * scale,
      }}
    >
      <HudVideo absFrom={absFrom} />
    </div>
  </div>
);
