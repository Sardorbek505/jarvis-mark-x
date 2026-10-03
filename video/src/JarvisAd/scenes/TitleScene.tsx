import React from "react";
import { AbsoluteFill, interpolate, random, useCurrentFrame } from "remotion";
import { ORB, OrbView } from "../devices";
import { Center, SceneShell, riseClip } from "../primitives";
import { C, DISPLAY, ENTER, EXIT, MONO, UNIT, scene, tween, useVertical } from "../theme";

const HIT = 6; // the bass hit lands 0.2 s into the scene (scripts/jarvis_ad/build.py)
const WORD = "JARVIS";

/** The name slams in over the live HUD orb — first of the two "impact" moments. */
export const TitleScene: React.FC = () => {
  const f = useCurrentFrame();
  const flash = tween(f, [HIT - 1, HIT + 1], [0, 1]) * tween(f, [HIT + 1, HIT + 18], [1, 0], EXIT);
  const amp = 9 * tween(f, [HIT, HIT + 16], [1, 0]);
  const shake = `${(random(`tx${f}`) - 0.5) * 2 * amp}px ${(random(`ty${f}`) - 0.5) * 2 * amp}px`;
  const pill = f - 18;
  const v = useVertical();

  return (
    <SceneShell>
      <AbsoluteFill style={{ translate: shake }}>
        {/* the real HUD orb, close up and dimmed behind the name */}
        <Center>
          <OrbView
            absFrom={scene("title").from}
            size={v ? 1080 : 1100}
            center={ORB.alone}
            scale={tween(f, [0, 108], [1.7, 1.85])}
            style={{
              opacity: tween(f, [0, 8], [0, 0.6]),
              filter: `brightness(${0.75 + flash * 1.2}) blur(${tween(f, [0, 20], [6, 1.5])}px)`,
              maskImage: "radial-gradient(circle, #000 45%, transparent 70%)",
            }}
          />
        </Center>
        <AbsoluteFill
          style={{
            opacity: flash * 0.7,
            background: "radial-gradient(circle at 50% 50%, rgba(214,255,248,0.9), rgba(48,208,190,0.35) 22%, transparent 55%)",
          }}
        />

        <Center style={{ flexDirection: "column", gap: 34 }}>
          <div style={{ position: "relative", display: "flex" }}>
            {WORD.split("").map((ch, i) => {
              const t = f - 1 - i * 2;
              return (
                <div key={i} style={{ padding: "0 0.02em", ...riseClip(t) }}>
                  <div
                    style={{
                      fontFamily: DISPLAY,
                      fontWeight: 500,
                      fontSize: v ? 160 : 240,
                      lineHeight: 1,
                      letterSpacing: "0.12em",
                      color: C.ink,
                      textShadow: `0 0 50px ${C.glow}, 0 0 120px rgba(48,208,190,0.25)`,
                      translate: `0 ${tween(t, [0, UNIT], [105, 0])}%`,
                      filter: `blur(${tween(t, [0, UNIT], [12, 0])}px)`,
                    }}
                  >
                    {ch}
                  </div>
                </div>
              );
            })}
            {/* sheen: a hot copy of the word, revealed by a moving mask band */}
            <div
              style={{
                position: "absolute",
                inset: 0,
                display: "flex",
                fontFamily: DISPLAY,
                fontWeight: 500,
                fontSize: v ? 160 : 240,
                lineHeight: 1,
                letterSpacing: "0.12em",
                color: "#ffffff",
                padding: "0 0.02em",
                maskImage: "linear-gradient(100deg, transparent 40%, #000 50%, transparent 60%)",
                maskSize: "300% 100%",
                maskPosition: `${tween(f, [26, 60], [100, 0])}% 0`,
                opacity: tween(f, [24, 28], [0, 1]) * tween(f, [56, 62], [1, 0]),
              }}
            >
              {WORD.split("").map((ch, i) => (
                <span key={i} style={{ padding: "0 0.02em" }}>
                  {ch}
                </span>
              ))}
            </div>
          </div>

          <div
            style={{
              padding: "16px 46px 18px",
              borderRadius: 999,
              border: `1px solid rgba(255,255,255,0.2)`,
              background: "linear-gradient(180deg, rgba(40,52,64,0.95), rgba(8,14,22,0.98))",
              boxShadow: `inset 0 2px rgba(255,255,255,0.18), 0 0 50px rgba(48,208,190,0.25), 0 24px 48px rgba(0,0,0,0.4)`,
              fontFamily: MONO,
              fontSize: 40,
              letterSpacing: "0.5em",
              color: C.hot,
              opacity: tween(pill, [0, 6], [0, 1]),
              // squash-and-stretch landing (NullMotion "kinetic headline" pill)
              scale: `${interpolate(pill, [0, 8, 16], [0.9, 1.07, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: ENTER })} ${interpolate(pill, [0, 8, 16], [1.08, 0.94, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: ENTER })}`,
            }}
          >
            MARK X
          </div>
        </Center>
      </AbsoluteFill>
    </SceneShell>
  );
};
