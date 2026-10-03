import { Audio } from "@remotion/media";
import React from "react";
import { AbsoluteFill, Series, staticFile, useCurrentFrame } from "remotion";
import { Backdrop, HudOverlay } from "./hud";
import { BootScene } from "./scenes/BootScene";
import { EndScene } from "./scenes/EndScene";
import { FeatureScene } from "./scenes/FeatureScene";
import { PhoneScene } from "./scenes/PhoneScene";
import { TitleScene } from "./scenes/TitleScene";
import { C, Section, timeline, tween } from "./theme";

const SPECIAL: Record<string, React.FC> = { boot: BootScene, title: TitleScene, phone: PhoneScene, end: EndScene };

const SectionView: React.FC<{ sec: Section }> = ({ sec }) => {
  const Special = SPECIAL[sec.id];
  return Special ? <Special /> : <FeatureScene sec={sec} />;
};

const FadeOut: React.FC = () => {
  const f = useCurrentFrame();
  const d = timeline.durationInFrames;
  return <AbsoluteFill style={{ background: "#000", opacity: tween(f, [d - 14, d], [0, 1]) }} />;
};

/** JARVIS Mark X feature tour — every section, its timing and its sound come from timeline.json. */
export const JarvisAd: React.FC = () => (
  <AbsoluteFill style={{ background: C.void }}>
    <Backdrop />
    <Series>
      {timeline.sections.map((sec) => (
        <Series.Sequence key={sec.id} name={sec.id} durationInFrames={sec.durationInFrames}>
          <SectionView sec={sec} />
        </Series.Sequence>
      ))}
    </Series>
    <HudOverlay />
    <FadeOut />
    <Audio src={staticFile("jarvis-ad/mix.mp3")} />
  </AbsoluteFill>
);

/** One section on its own timeline, for previewing in Studio. */
export const JarvisAdScene: React.FC<{ id: string }> = ({ id }) => {
  const sec = timeline.sections.find((s) => s.id === id)!;
  return (
    <AbsoluteFill style={{ background: C.void }}>
      <Backdrop />
      <SectionView sec={sec} />
    </AbsoluteFill>
  );
};
