import React from "react";
import { useCurrentFrame } from "remotion";
import { AppWindow, PcShot, Phone, Tap } from "../devices";
import { SceneHeader, SceneShell } from "../primitives";
import { tween, ui, UNIT, useVertical } from "../theme";

const PAGE_SWAP = 84; // PC: Study → About me
const TAP = 64; // phone: tap the «Учёба» tab
const PHONE_W = 340;

/** The real Study and About-me pages on the PC, the same data synced into the phone. */
export const MemoryScene: React.FC = () => {
  const f = useCurrentFrame();
  const t = f - PAGE_SWAP;
  const v = useVertical();
  return (
    <SceneShell>
      <SceneHeader label={ui("memoryLabel")} head={ui("memoryHead")} />
      <AppWindow {...(v ? { x: 40, y: 430, w: 1000, rotY: 0 } : { x: 110, y: 262, w: 1130, rotY: 8 })} delay={2}>
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
        {...(v ? { x: 680, y: 860, rotY: -6 } : { x: 1390, y: 214, rotY: -8 })}
        w={v ? 360 : PHONE_W}
        delay={8}
        screens={[
          { src: "dashboard", from: 0 },
          { src: "study", from: TAP + 6 },
        ]}
      >
        <Tap at={TAP} x={229} y={818} phoneW={v ? 360 : PHONE_W} />
      </Phone>
    </SceneShell>
  );
};
