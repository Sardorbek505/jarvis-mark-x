import React from "react";
import { AppWindow, Beam, HudVideo, Phone } from "../devices";
import { SceneHeader, SceneShell } from "../primitives";
import { scene, ui, useVertical } from "../theme";

/** Same Jarvis on both screens: the PC app speaking live, the Telegram Mini App chat beside it. */
export const VoiceScene: React.FC = () => {
  const s = scene("voice");
  const v = useVertical();
  return (
    <SceneShell>
      <SceneHeader label={ui("voiceLabel")} head={ui("voiceHead")} />
      <AppWindow {...(v ? { x: 40, y: 430, w: 1000, rotY: 0 } : { x: 120, y: 262, w: 1110, rotY: 9 })} delay={2}>
        <HudVideo absFrom={s.from} />
      </AppWindow>
      <Beam a={v ? [700, 1000] : [1236, 560]} b={v ? [750, 890] : [1400, 520]} delay={14} pulses={[40, 96, 140]} />
      <Phone {...(v ? { x: 680, y: 860, w: 360, rotY: -6 } : { x: 1400, y: 214, w: 340, rotY: -9 })} delay={8} screens={[{ src: "chat", from: 0 }]} />
    </SceneShell>
  );
};
