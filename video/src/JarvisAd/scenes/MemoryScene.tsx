import React from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";
import { Chip, Headline, Label, SceneShell, Shot } from "../primitives";
import { C, EXIT, scene, timeline, tween, wordTimings } from "../theme";

const NODE = { x: 984, y: 506 }; // central node of memory.jpg at 1920×1080

/** Knowledge-graph art with beat-synced pulses; the chips pop as each word is spoken. */
export const MemoryScene: React.FC = () => {
  const f = useCurrentFrame();
  const s = scene("memory");
  const words = wordTimings(s.vo.text, s.vo.durationInFrames);
  const at = (w: string) => s.vo.from + Math.round(words.find((x) => x.word.toLowerCase().startsWith(w))!.start);

  return (
    <SceneShell>
      <AbsoluteFill>
        <Shot src="memory.jpg" from={1} to={1.14} origin="51% 47%" />
      </AbsoluteFill>
      {/* pulse rings from the central node, one per beat */}
      <AbsoluteFill>
        {[0, 1].map((k) => {
          const t = (f + k * (timeline.beatFrames / 2)) % timeline.beatFrames;
          const p = t / timeline.beatFrames;
          return (
            <div
              key={k}
              style={{
                position: "absolute",
                left: NODE.x - 40 - p * 360,
                top: NODE.y - 40 - p * 360,
                width: 80 + p * 720,
                height: 80 + p * 720,
                borderRadius: "50%",
                border: `2px solid ${C.core}`,
                opacity: tween(p, [0, 1], [0.45, 0], EXIT) * tween(f, [6, 20], [0, 1]),
              }}
            />
          );
        })}
      </AbsoluteFill>
      <AbsoluteFill
        style={{ background: "linear-gradient(90deg, rgba(2,6,13,0.94) 0%, rgba(2,6,13,0.75) 32%, transparent 60%)" }}
      />

      <div style={{ position: "absolute", left: 132, top: 280, width: 720 }}>
        <Label delay={4}>03 — MEMORY</Label>
        <div style={{ height: 26 }} />
        <Headline lines={["Remembers", "what matters."]} sub="Obsidian notes & long-term memory" delay={8} size={96} />
        <div style={{ display: "flex", gap: 16, marginTop: 50 }}>
          <Chip delay={at("notes")}>NOTES</Chip>
          <Chip delay={at("plans")}>PLANS</Chip>
          <Chip delay={at("preferences")} color={C.teal}>
            PREFERENCES
          </Chip>
        </div>
      </div>
    </SceneShell>
  );
};
