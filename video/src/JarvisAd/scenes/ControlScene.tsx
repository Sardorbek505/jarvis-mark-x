import React from "react";
import { useVideoConfig } from "remotion";
import { AppWindow, Beam, HudVideo, Phone, Tap } from "../devices";
import { SceneHeader, SceneShell } from "../primitives";
import { scene, ui, useVertical } from "../theme";

// Taps on the Mini App's PC remote (393-wide CSS px of public/jarvis-ad/phone/pc.png):
// «Режим учёбы» (opens VS Code), volume down, «Скрин в TG».
const TAPS: [number, number][] = [
  [98, 322],
  [272, 675],
  [242, 553],
];
const PHONE_W = 340;

/** Cause and effect: a tap on the phone, a pulse across, the real result card on the PC.
 * The phone is drawn last: on 9:16 it overlaps the window. */
export const ControlScene: React.FC = () => {
  const { durationInFrames } = useVideoConfig();
  const s = scene("control");
  const v = useVertical();
  // The PC capture shows each card at tick - 4 and the bleep plays on tick (build.py).
  const ticks = [1, 2, 3].map((k) => Math.round((k * durationInFrames) / 4));
  return (
    <SceneShell>
      <SceneHeader label={ui("controlLabel")} head={ui("controlHead")} />
      <AppWindow {...(v ? { x: 40, y: 430, w: 1000, rotY: 0 } : { x: 660, y: 262, w: 1140, rotY: -7 })} delay={8}>
        <HudVideo absFrom={s.from} />
      </AppWindow>
      <Beam a={v ? [400, 900] : [520, 560]} b={v ? [820, 560] : [668, 540]} delay={10} pulses={ticks.map((t) => t - 16)} />
      <Phone {...(v ? { x: 40, y: 860, rotY: 6 } : { x: 170, y: 214, rotY: 9 })} w={v ? 360 : PHONE_W} delay={2} screens={[{ src: "pc", from: 0 }]}>
        {TAPS.map(([x, y], k) => (
          <Tap key={k} at={ticks[k] - 18} x={x} y={y} phoneW={v ? 360 : PHONE_W} />
        ))}
      </Phone>
    </SceneShell>
  );
};
