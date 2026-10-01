import React from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";
import { AppWindow, PcShot, Phone, Tap } from "../devices";
import { Headline, Label, SceneShell } from "../primitives";
import { UNIT, tween } from "../theme";

const PAGE_SWAP = 84; // PC: Study → About me
const TAP = 64; // phone: tap the «Учёба» tab
const PHONE_W = 340;

/** The real Study and About-me pages on the PC, the same data synced into the phone. */
export const MemoryScene: React.FC = () => {
  const f = useCurrentFrame();
  const t = f - PAGE_SWAP;
  return (
    <SceneShell>
      <AbsoluteFill style={{ alignItems: "center", paddingTop: 54 }}>
        <Label delay={4}>03 — MEMORY</Label>
        <div style={{ height: 14 }} />
        <Headline lines={["Remembers what matters."]} delay={8} size={72} align="center" />
      </AbsoluteFill>
      <AppWindow x={110} y={262} w={1130} rotY={8} delay={2}>
        <div style={{ position: "relative", width: "100%", height: "100%" }}>
          <PcShot page="study" />
          <div
            style={{
              position: "absolute",
              inset: 0,
              opacity: tween(t, [0, 8], [0, 1]),
              translate: `${tween(t, [0, UNIT + 4], [6, 0])}% 0`,
            }}
          >
            <PcShot page="about" />
          </div>
        </div>
      </AppWindow>
      <Phone
        x={1390}
        y={214}
        w={PHONE_W}
        rotY={-8}
        delay={8}
        screens={[
          { src: "dashboard", from: 0 },
          { src: "study", from: TAP + 6 },
        ]}
      >
        <Tap at={TAP} x={229} y={818} phoneW={PHONE_W} />
      </Phone>
    </SceneShell>
  );
};
