import React from "react";
import { useCurrentFrame } from "remotion";
import { AppWindow, HudVideo } from "../devices";
import { SceneHeader, SceneShell } from "../primitives";
import { MOVE, scene, tween, ui, useVertical } from "../theme";

// The HUD takes on its "screen" shape on entry; the result card lands 70 frames
// into the line (scripts/capture/capture_pc.py) — the camera pushes in to meet it.
const CARD = scene("vision").vo.from + 70;

export const VisionScene: React.FC = () => {
  const f = useCurrentFrame();
  const s = scene("vision");
  const v = useVertical();
  return (
    <SceneShell>
      <SceneHeader label={ui("visionLabel")} head={ui("visionHead")} />
      <AppWindow
        {...(v ? { x: 40, y: 600, w: 1000 } : { x: 350, y: 238, w: 1220 })}
        delay={2}
        zoom={tween(f, [CARD - 8, CARD + 30], [1, 1.45], MOVE)}
        zoomOrigin="98% 16%"
      >
        <HudVideo absFrom={s.from} />
      </AppWindow>
    </SceneShell>
  );
};
