import React from "react";
import { useCurrentFrame } from "remotion";
import { Brackets, Chip, GlassFrame, Headline, Label, SceneShell, Shot } from "../primitives";
import { C, MOVE, tween } from "../theme";

const FRAME = { x: 720, y: 190, w: 1080, h: 608 };
const SCAN = [30, 66] as const; // data-scan SFX starts 1.0 s in (scripts/jarvis_ad/build.py)
const LOCK = 66;

/** Real "vision" art in a HUD frame; a scan beam sweeps, then brackets lock onto a finding. */
export const VisionScene: React.FC = () => {
  const f = useCurrentFrame();
  const beat = f - LOCK;
  return (
    <SceneShell>
      <div style={{ position: "absolute", left: 132, top: 300, width: 560 }}>
        <Label delay={4}>02 — VISION</Label>
        <div style={{ height: 26 }} />
        <Headline lines={["It sees", "your screen."]} sub="Screen, code and webcam analysis" delay={8} size={96} />
      </div>

      <GlassFrame {...FRAME} delay={2}>
        <Shot src="vision.jpg" from={1} to={1.08} origin="70% 45%" />
        {/* scan beam */}
        <div
          style={{
            position: "absolute",
            top: 0,
            bottom: 0,
            width: "10%",
            left: `${tween(f, [...SCAN], [40, 100], MOVE)}%`,
            opacity: tween(f, [SCAN[0], SCAN[0] + 4], [0, 1]) * tween(f, [SCAN[1] - 6, SCAN[1]], [1, 0]),
            background: "linear-gradient(90deg, transparent, rgba(46,200,255,0.25) 70%, rgba(217,247,255,0.9) 96%, transparent)",
            mixBlendMode: "screen",
          }}
        />
        {/* target lock on the optical-feed panel */}
        <div style={{ position: "absolute", left: "61%", top: "30%", width: 170, height: 170, opacity: beat >= 0 ? 1 : 0 }}>
          <Brackets delay={LOCK} color={C.amber} size={30} inset={0} />
          <div
            style={{
              position: "absolute",
              inset: 0,
              border: `1px solid ${C.amber}`,
              opacity: tween(beat, [4, 8], [0, 0.6]) * (beat > 8 && beat < 20 ? (Math.floor(beat / 2) % 2 ? 1 : 0.3) : 1),
              boxShadow: `0 0 30px ${C.amber}55 inset`,
            }}
          />
        </div>
      </GlassFrame>

      <div style={{ position: "absolute", left: FRAME.x + 290, top: FRAME.y + FRAME.h - 40 }}>
        <Chip delay={LOCK + 8} color={C.amber}>
          BUG FOUND · LINE 42
        </Chip>
      </div>
    </SceneShell>
  );
};
