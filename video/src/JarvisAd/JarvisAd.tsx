import { Audio } from "@remotion/media";
import React from "react";
import { AbsoluteFill, Series, staticFile, useCurrentFrame } from "remotion";
import { Backdrop, Captions, HudOverlay } from "./hud";
import { BootScene } from "./scenes/BootScene";
import { ControlScene } from "./scenes/ControlScene";
import { EndScene } from "./scenes/EndScene";
import { MemoryScene } from "./scenes/MemoryScene";
import { TitleScene } from "./scenes/TitleScene";
import { VisionScene } from "./scenes/VisionScene";
import { VoiceScene } from "./scenes/VoiceScene";
import { C, SceneId, timeline, tween } from "./theme";

export const SCENES: Record<SceneId, React.FC> = {
  boot: BootScene,
  title: TitleScene,
  voice: VoiceScene,
  vision: VisionScene,
  memory: MemoryScene,
  control: ControlScene,
  end: EndScene,
};

const FadeOut: React.FC = () => {
  const f = useCurrentFrame();
  const d = timeline.durationInFrames;
  return <AbsoluteFill style={{ background: "#000", opacity: tween(f, [d - 14, d], [0, 1]) }} />;
};

/** 41-second motion-design ad for JARVIS Mark X — voice-over, score and cut points come from timeline.json. */
export const JarvisAd: React.FC = () => (
  <AbsoluteFill style={{ background: C.void }}>
    <Backdrop />
    <Series>
      {timeline.scenes.map((s) => {
        const Scene = SCENES[s.id];
        return (
          <Series.Sequence key={s.id} name={s.id} durationInFrames={s.durationInFrames}>
            <Scene />
          </Series.Sequence>
        );
      })}
    </Series>
    <HudOverlay />
    <Captions hide={["title", "end"]} />
    <FadeOut />
    <Audio src={staticFile("jarvis-ad/mix.mp3")} />
  </AbsoluteFill>
);

/** A single scene on its own timeline, for previewing in Studio. */
export const JarvisAdScene: React.FC<{ id: SceneId }> = ({ id }) => {
  const Scene = SCENES[id];
  return (
    <AbsoluteFill style={{ background: C.void }}>
      <Backdrop />
      <Scene />
    </AbsoluteFill>
  );
};
