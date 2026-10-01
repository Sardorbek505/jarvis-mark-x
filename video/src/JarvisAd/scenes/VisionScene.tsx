import React from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";
import { AppWindow, HudVideo } from "../devices";
import { Headline, Label, SceneShell } from "../primitives";
import { MOVE, scene, tween } from "../theme";

// The HUD takes on its "screen" shape on entry; the result card lands 70 frames
// into the line (scripts/capture/capture_pc.py) — the camera pushes in to meet it.
const CARD = scene("vision").vo.from + 70;

export const VisionScene: React.FC = () => {
  const f = useCurrentFrame();
  const s = scene("vision");
  return (
    <SceneShell>
      <AbsoluteFill style={{ alignItems: "center", paddingTop: 54 }}>
        <Label delay={4}>02 — VISION</Label>
        <div style={{ height: 14 }} />
        <Headline lines={["It sees your screen."]} delay={8} size={72} align="center" />
      </AbsoluteFill>
      <AppWindow
        x={350}
        y={238}
        w={1220}
        delay={2}
        zoom={tween(f, [CARD - 8, CARD + 30], [1, 1.45], MOVE)}
        zoomOrigin="98% 16%"
      >
        <HudVideo absFrom={s.from} />
      </AppWindow>
    </SceneShell>
  );
};
