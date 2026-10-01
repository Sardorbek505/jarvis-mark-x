import React from "react";
import { useCurrentFrame } from "remotion";
import { AppWindow, HudVideo } from "../devices";
import { SceneShell } from "../primitives";
import { DRIFT, scene, tween } from "../theme";

/** The real app launches: its window rises out of the dark while the HUD boots (reactor → idle → speaking). */
export const BootScene: React.FC = () => {
  const f = useCurrentFrame();
  const s = scene("boot");
  const push = tween(f, [0, s.durationInFrames], [0.94, 1.0], DRIFT);
  return (
    <SceneShell>
      <div style={{ position: "absolute", inset: 0, scale: String(push) }}>
        <AppWindow x={190} y={66} w={1540} delay={2}>
          <HudVideo absFrom={s.from} />
        </AppWindow>
      </div>
    </SceneShell>
  );
};
