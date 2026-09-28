import React from "react";
import { useCurrentFrame, useVideoConfig } from "remotion";
import { GlassFrame, Headline, Label, SceneShell, Shot } from "../primitives";
import { C, DISPLAY, MONO, UNIT, tween } from "../theme";

const COMMANDS = ["Open VS Code", "Volume down 20%", "Send this to my phone"];

const Mic: React.FC<{ color: string }> = ({ color }) => (
  <svg width={30} height={30} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={2} strokeLinecap="round">
    <rect x={9} y={3} width={6} height={11} rx={3} />
    <path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
  </svg>
);

/** Spoken commands type in and resolve to DONE on the bleeps; the remote-control art sits right. */
export const ControlScene: React.FC = () => {
  const f = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  const step = durationInFrames / 4; // same spacing as the bleeps in scripts/jarvis_ad/build.py

  return (
    <SceneShell>
      <div style={{ position: "absolute", left: 132, top: 170, width: 680 }}>
        <Label delay={4}>04 — CONTROL</Label>
        <div style={{ height: 26 }} />
        <Headline lines={["Your PC,", "on command."]} sub="Apps, sound, windows, Telegram" delay={8} size={96} />
      </div>

      <div style={{ position: "absolute", left: 132, top: 530, display: "flex", flexDirection: "column", gap: 16 }}>
        {COMMANDS.map((cmd, k) => {
          const tick = Math.round((k + 1) * step);
          const t = f - (tick - 26);
          const typed = Math.floor(tween(t, [4, 18], [0, cmd.length], (x) => x));
          const done = f >= tick;
          const flash = tween(f, [tick, tick + 10], [1, 0]);
          return (
            <div
              key={cmd}
              style={{
                width: 700,
                display: "flex",
                alignItems: "center",
                gap: 20,
                padding: "16px 26px",
                borderRadius: 18,
                background: "rgba(6,18,30,0.75)",
                border: `1px solid ${done ? C.teal : C.line}`,
                boxShadow: `0 0 ${20 + flash * 40}px ${done ? `rgba(63,240,200,${0.12 + flash * 0.3})` : "transparent"}, inset 0 1px rgba(255,255,255,0.08)`,
                opacity: tween(t, [0, 8], [0, 1]),
                translate: `${tween(t, [0, UNIT], [-40, 0])}px 0`,
              }}
            >
              <Mic color={done ? C.teal : C.core} />
              <div style={{ flex: 1, fontFamily: DISPLAY, fontSize: 34, color: C.ink, whiteSpace: "pre" }}>
                “{cmd.slice(0, typed)}
                {typed < cmd.length ? <span style={{ color: C.core }}>▌</span> : "”"}
              </div>
              <div
                style={{
                  fontFamily: MONO,
                  fontSize: 22,
                  letterSpacing: "0.2em",
                  whiteSpace: "nowrap",
                  color: done ? C.teal : C.dim,
                  scale: String(done ? 1 + flash * 0.15 : 1),
                }}
              >
                {done ? "DONE ✓" : "···"}
              </div>
            </div>
          );
        })}
      </div>

      <GlassFrame x={880} y={200} w={920} h={518} delay={2}>
        <Shot src="remote.jpg" from={1} to={1.07} origin="30% 50%" />
      </GlassFrame>
    </SceneShell>
  );
};
