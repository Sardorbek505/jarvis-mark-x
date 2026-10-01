import React from "react";
import { AbsoluteFill, useVideoConfig } from "remotion";
import { AppWindow, Beam, HudVideo, Phone, Tap } from "../devices";
import { Headline, Label, SceneShell } from "../primitives";
import { scene } from "../theme";

// Taps on the Mini App's PC remote (393-wide CSS px of public/jarvis-ad/phone/pc.png):
// «Режим учёбы» (opens VS Code), volume down, «Скрин в TG».
const TAPS: [number, number][] = [
  [98, 322],
  [272, 675],
  [242, 553],
];
const PHONE_W = 340;

/** Cause and effect: a tap on the phone, a pulse across, the real result card on the PC. */
export const ControlScene: React.FC = () => {
  const { durationInFrames } = useVideoConfig();
  const s = scene("control");
  // The PC capture shows each card at tick - 4 and the bleep plays on tick (build.py).
  const ticks = [1, 2, 3].map((k) => Math.round((k * durationInFrames) / 4));
  return (
    <SceneShell>
      <AbsoluteFill style={{ alignItems: "center", paddingTop: 54 }}>
        <Label delay={4}>04 — CONTROL</Label>
        <div style={{ height: 14 }} />
        <Headline lines={["Your PC, on command."]} delay={8} size={72} align="center" />
      </AbsoluteFill>
      <Phone x={170} y={214} w={PHONE_W} rotY={9} delay={2} screens={[{ src: "pc", from: 0 }]}>
        {TAPS.map(([x, y], k) => (
          <Tap key={k} at={ticks[k] - 18} x={x} y={y} phoneW={PHONE_W} />
        ))}
      </Phone>
      <Beam a={[520, 560]} b={[668, 540]} delay={10} pulses={ticks.map((t) => t - 16)} />
      <AppWindow x={660} y={262} w={1140} rotY={-7} delay={8}>
        <HudVideo absFrom={s.from} />
      </AppWindow>
    </SceneShell>
  );
};
