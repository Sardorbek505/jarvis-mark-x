import React from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";
import { Center, Headline, Label, SceneShell, Shot } from "../primitives";
import { C, scene, voiceAt } from "../theme";

/** Live waveform laid over the product's own voice-stream art, driven by the voice-over. */
const LiveWave: React.FC<{ absFrame: number }> = ({ absFrame }) => {
  const f = useCurrentFrame();
  const level = voiceAt(absFrame);
  const path = (k: number, amp: number, freq: number, speed: number) => {
    const pts: string[] = [];
    for (let x = 0; x <= 1400; x += 10) {
      const u = x / 1400;
      const taper = Math.sin(Math.PI * u) ** 1.5;
      const y =
        Math.sin(u * freq * Math.PI * 2 + f * speed + k) * amp * taper +
        Math.sin(u * freq * 3.1 * Math.PI + f * speed * 1.7) * amp * 0.3 * taper;
      pts.push(`${x},${200 + y}`);
    }
    return `M${pts.join(" L")}`;
  };
  const amp = 14 + 130 * level;
  return (
    <svg width={1400} height={400} viewBox="0 0 1400 400" style={{ overflow: "visible", filter: `drop-shadow(0 0 12px ${C.glow})` }}>
      <path d={path(0, amp, 3, 0.22)} stroke={C.hot} strokeWidth={3} fill="none" opacity={0.9} />
      <path d={path(1.3, amp * 0.7, 4.2, -0.17)} stroke={C.core} strokeWidth={2} fill="none" opacity={0.7} />
      <path d={path(2.1, amp * 0.45, 5.5, 0.3)} stroke={C.teal} strokeWidth={1.5} fill="none" opacity={0.6} />
    </svg>
  );
};

export const VoiceScene: React.FC = () => {
  const f = useCurrentFrame();
  const s = scene("voice");
  return (
    <SceneShell>
      <AbsoluteFill>
        <Shot src="voice.jpg" from={1.02} to={1.1} />
      </AbsoluteFill>
      <AbsoluteFill
        style={{
          background:
            "linear-gradient(180deg, rgba(2,6,13,0.9) 0%, rgba(2,6,13,0.35) 34%, rgba(2,6,13,0.1) 55%, rgba(2,6,13,0.75) 100%)",
        }}
      />
      <Center style={{ top: 4 }}>
        <LiveWave absFrame={s.from + f} />
      </Center>
      <AbsoluteFill style={{ alignItems: "center", paddingTop: 150 }}>
        <Label delay={4}>01 — VOICE</Label>
        <div style={{ height: 22 }} />
        <Headline lines={["Speak. It answers."]} sub="Real-time two-way voice · interrupt any time" delay={8} size={100} align="center" />
      </AbsoluteFill>
    </SceneShell>
  );
};
