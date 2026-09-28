import React from "react";
import { interpolate, useCurrentFrame } from "remotion";
import { Center, CoreRings, SceneShell } from "../primitives";
import { C, DISPLAY, DRIFT, MONO, MOVE, scene, tween, voiceAt } from "../theme";

const LOG = [
  "> BOOT SEQUENCE 10.0",
  "> VOICE CORE ......... OK",
  "> VISION CORE ........ OK",
  "> MEMORY GRID ........ OK",
  "> REMOTE LINK ........ OK",
  "> ALL SYSTEMS ONLINE",
];

/** Rings stroke on while the system log types itself; the core breathes with the first line of voice. */
export const BootScene: React.FC = () => {
  const f = useCurrentFrame();
  const s = scene("boot");
  const energy = tween(f, [10, 70], [0.1, 0.35], MOVE) + 0.65 * voiceAt(s.from + f);
  const sync = Math.round(tween(f, [10, 84], [0, 100], MOVE));

  return (
    <SceneShell>
      <Center style={{ scale: String(tween(f, [0, s.durationInFrames], [0.96, 1.03], DRIFT)) }}>
        <CoreRings size={600} draw={tween(f, [6, 84], [0, 1], MOVE)} energy={energy} spin={f * 0.6} />
      </Center>

      <div style={{ position: "absolute", left: 132, top: 330, fontFamily: MONO, fontSize: 24, lineHeight: 1.9 }}>
        {LOG.map((line, i) => {
          const t = f - 8 - i * 9;
          const chars = Math.floor(interpolate(t, [0, 10], [0, line.length], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }));
          const last = i === LOG.length - 1;
          return (
            <div key={line} style={{ color: last ? C.teal : C.muted, opacity: t < 0 ? 0 : 1, whiteSpace: "pre" }}>
              {line.slice(0, chars)}
              {t >= 0 && chars < line.length ? <span style={{ color: C.core }}>▌</span> : null}
            </div>
          );
        })}
      </div>

      <div style={{ position: "absolute", right: 132, top: 330, textAlign: "right", opacity: tween(f, [8, 20], [0, 1]) }}>
        <div style={{ fontFamily: MONO, fontSize: 20, letterSpacing: "0.3em", color: C.dim }}>NEURAL SYNC</div>
        <div
          style={{
            fontFamily: DISPLAY,
            fontWeight: 500,
            fontSize: 110,
            lineHeight: 1.1,
            color: sync === 100 ? C.hot : C.ink,
            textShadow: `0 0 ${sync === 100 ? 40 : 18}px ${C.glow}`,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {String(sync).padStart(3, "0")}
          <span style={{ fontSize: 50, color: C.core }}>%</span>
        </div>
      </div>
    </SceneShell>
  );
};
