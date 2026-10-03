import React from "react";
import { AbsoluteFill, interpolate, random, useCurrentFrame } from "remotion";
import { ORB, OrbView } from "../devices";
import { Center, CoreRings, riseClip } from "../primitives";
import { C, DISPLAY, ENTER, EXIT, MONO, MOVE, UNIT, pop, section, timeline, tween, voiceAt, ui, useVertical } from "../theme";

const IGNITE = timeline.beatFrames; // one beat after the cut — the boom and impact land here (build.py)
const WORD = "JARVIS";

/** The one "wow": the core ignites, settles into a lockup, and the call to action lands. */
export const EndScene: React.FC = () => {
  const f = useCurrentFrame();
  const s = section("end");
  const lit = f >= IGNITE;
  const flash = tween(f, [IGNITE - 1, IGNITE + 1], [0, 1]) * tween(f, [IGNITE + 1, IGNITE + 24], [1, 0], EXIT);
  const amp = 11 * tween(f, [IGNITE, IGNITE + 18], [1, 0]);
  const shake = `${(random(`ex${f}`) - 0.5) * 2 * amp}px ${(random(`ey${f}`) - 0.5) * 2 * amp}px`;
  const settle = tween(f, [IGNITE + 26, IGNITE + 52], [0, 1], MOVE); // core rises to make room for the lockup
  const energy = lit ? 0.35 + 0.4 * flash + 0.5 * voiceAt(s.from + f) : 0.08;
  const logo = f - (IGNITE + 38);
  const v = useVertical();
  const rise = v ? -330 : -240; // where the core settles above the lockup

  return (
    <AbsoluteFill style={{ translate: shake }}>
      {/* god rays behind the core */}
      <AbsoluteFill
        style={{
          opacity: lit ? 0.18 + flash * 0.4 : 0,
          background: "repeating-conic-gradient(from 0deg at 50% 50%, rgba(48,208,190,0.5) 0deg 2deg, transparent 2deg 12deg)",
          rotate: `${f * 0.15}deg`,
          scale: "1.6",
          maskImage: "radial-gradient(circle at 50% 50%, #000 0%, transparent 45%)",
          translate: `0 ${rise * settle}px`,
        }}
      />

      <Center style={{ translate: `0 ${rise * settle}px`, scale: String(1 - (v ? 0.5 : 0.54) * settle) }}>
        <div style={{ position: "relative", width: 900, height: 900 }}>
          <Center>
            {/* the real HUD orb as the core */}
            <OrbView
              absFrom={section("end").from}
              size={640}
              center={ORB.alone}
              scale={0.95}
              style={{
                borderRadius: "50%",
                maskImage: "radial-gradient(circle, #000 55%, transparent 71%)",
                opacity: lit ? 1 : tween(f, [0, IGNITE], [0.05, 0.3]),
                scale: String(lit ? 0.72 + 0.28 * pop(f, IGNITE, 6, 200) : 0.72),
                filter: `brightness(${lit ? 1 + flash * 1.8 : 0.5})`,
              }}
            />
          </Center>
          <Center>
            <CoreRings size={900} draw={tween(f, [0, IGNITE + 20], [0, 1], MOVE)} energy={energy} spin={f * 0.8} />
          </Center>
          {/* shockwave */}
          <Center>
            <div
              style={{
                width: 200,
                height: 200,
                borderRadius: "50%",
                border: `3px solid ${C.hot}`,
                boxShadow: `0 0 40px ${C.core}`,
                scale: String(tween(f, [IGNITE, IGNITE + 30], [0.5, 9], ENTER)),
                opacity: lit ? tween(f, [IGNITE, IGNITE + 30], [0.9, 0]) : 0,
              }}
            />
          </Center>
        </div>
      </Center>

      <AbsoluteFill
        style={{
          opacity: flash,
          background: "radial-gradient(circle at 50% 50%, rgba(230,255,250,0.95), rgba(48,208,190,0.4) 30%, transparent 70%)",
        }}
      />

      {/* lockup */}
      <AbsoluteFill style={{ alignItems: "center", paddingTop: v ? 960 : 560 }}>
        <div style={{ display: "flex", flexDirection: v ? "column" : "row", alignItems: "center", gap: v ? 18 : 36 }}>
          <div style={{ display: "flex" }}>
            {WORD.split("").map((ch, i) => {
              const t = logo - i * 2;
              return (
                <div key={i} style={{ ...riseClip(t) }}>
                  <div
                    style={{
                      fontFamily: DISPLAY,
                      fontWeight: 500,
                      fontSize: v ? 130 : 150,
                      lineHeight: 1.05,
                      letterSpacing: "0.14em",
                      color: C.ink,
                      textShadow: `0 0 40px ${C.glow}`,
                      translate: `0 ${tween(t, [0, UNIT], [105, 0])}%`,
                      filter: `blur(${tween(t, [0, UNIT], [10, 0])}px)`,
                    }}
                  >
                    {ch}
                  </div>
                </div>
              );
            })}
          </div>
          <div
            style={{
              padding: "10px 30px 12px",
              borderRadius: 999,
              border: "1px solid rgba(255,255,255,0.2)",
              background: "linear-gradient(180deg, rgba(40,52,64,0.95), rgba(8,14,22,0.98))",
              boxShadow: "inset 0 2px rgba(255,255,255,0.18), 0 0 40px rgba(48,208,190,0.25)",
              fontFamily: MONO,
              fontSize: 34,
              letterSpacing: "0.4em",
              color: C.hot,
              opacity: tween(logo - 14, [0, 6], [0, 1]),
              scale: `${interpolate(logo - 14, [0, 8, 16], [0.9, 1.07, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: ENTER })} ${interpolate(logo - 14, [0, 8, 16], [1.08, 0.94, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: ENTER })}`,
            }}
          >
            MARK X
          </div>
        </div>

        <div
          style={{
            marginTop: 26,
            fontFamily: DISPLAY,
            fontSize: v ? 40 : 44,
            maxWidth: v ? 860 : undefined,
            textAlign: "center",
            color: C.muted,
            opacity: tween(logo - 26, [0, UNIT], [0, 1]),
            translate: `0 ${tween(logo - 26, [0, UNIT], [16, 0])}px`,
          }}
        >
          {ui("tagline")}
        </div>

        <div
          style={{
            position: "relative",
            overflow: "hidden",
            marginTop: 44,
            display: "flex",
            alignItems: "center",
            gap: v ? 14 : 24,
            flexDirection: v ? "column" : "row",
            padding: "14px 16px 14px 16px",
            borderRadius: 999,
            border: `1px solid ${C.core}66`,
            background: "rgba(6,18,30,0.8)",
            boxShadow: `0 0 50px rgba(48,208,190,0.2)`,
            opacity: tween(logo - 40, [0, 4], [0, 1]),
            scale: String(Math.max(0, pop(f, IGNITE + 78, 7))),
          }}
        >
          <div
            style={{
              padding: "12px 28px",
              borderRadius: 999,
              background: `linear-gradient(180deg, ${C.hot}, ${C.core})`,
              color: C.void,
              fontFamily: MONO,
              fontSize: 26,
              letterSpacing: "0.18em",
              boxShadow: `0 0 30px ${C.glow}`,
            }}
          >
            {ui("cta")}
          </div>
          <div style={{ fontFamily: MONO, fontSize: 28, letterSpacing: "0.04em", color: C.ink, paddingRight: 18 }}>
            github.com/Sardorbek505/jarvis-mark-x
          </div>
          <div
            style={{
              position: "absolute",
              top: 0,
              bottom: 0,
              width: "18%",
              left: `${tween(logo - 70, [0, 26], [-25, 120], MOVE)}%`,
              background: "linear-gradient(105deg, transparent, rgba(220,248,255,0.28) 50%, transparent)",
            }}
          />
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
