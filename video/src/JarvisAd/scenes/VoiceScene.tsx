import React from "react";
import { AbsoluteFill } from "remotion";
import { AppWindow, Beam, HudVideo, Phone } from "../devices";
import { Headline, Label, SceneShell } from "../primitives";
import { scene } from "../theme";

/** Same Jarvis on both screens: the PC app speaking live, the Telegram Mini App chat beside it. */
export const VoiceScene: React.FC = () => {
  const s = scene("voice");
  return (
    <SceneShell>
      <AbsoluteFill style={{ alignItems: "center", paddingTop: 54 }}>
        <Label delay={4}>01 — VOICE</Label>
        <div style={{ height: 14 }} />
        <Headline lines={["Speak. It answers."]} delay={8} size={72} align="center" />
      </AbsoluteFill>
      <AppWindow x={120} y={262} w={1110} rotY={9} delay={2}>
        <HudVideo absFrom={s.from} />
      </AppWindow>
      <Beam a={[1236, 560]} b={[1400, 520]} delay={14} pulses={[40, 96, 140]} />
      <Phone x={1400} y={214} w={340} rotY={-9} delay={8} screens={[{ src: "chat", from: 0 }]} />
    </SceneShell>
  );
};
