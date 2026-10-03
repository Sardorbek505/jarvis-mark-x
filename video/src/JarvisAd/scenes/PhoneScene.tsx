import React from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";
import { AppWindow, Beam, HudVideo, Phone, Tap } from "../devices";
import { C, DISPLAY, MONO, pop, section, tween, ui, useVertical } from "../theme";
import { Reply } from "./FeatureScene";

// Mini App tabs (393-wide CSS px of public/jarvis-ad/phone/*.png): tab bar y = 818.
const TAB = { dashboard: [98, 818], pc: [360, 818] } as const;
const STUDY_MODE: [number, number] = [98, 322]; // «Режим учёбы» on the PC remote
const CHIPS = ["Чат", "Сводка", "Дела", "Учёба", "Привычки", "ПК-пульт"];

/**
 * The Telegram Mini App: same Jarvis in your pocket. Three taps on the beat
 * (build.py phone_taps: 42 %, 60 %, 78 % of the section) walk chat → summary →
 * PC remote → run a command on the PC.
 */
export const PhoneScene: React.FC = () => {
  const f = useCurrentFrame();
  const v = useVertical();
  const sec = section("phone");
  const d = sec.durationInFrames;
  const taps = [0.42, 0.6, 0.78].map((p) => Math.round(d * p));
  const pw = v ? 470 : 400;
  const px = v ? (1080 - pw) / 2 : 1140;
  const py = v ? 470 : 150;
  const title = ui("phoneHead");
  const out = tween(f, [d - 6, d], [0, 1]);

  return (
    <AbsoluteFill style={{ opacity: tween(f, [0, 5], [0, 1]) * (1 - out), filter: `blur(${out * 8}px)` }}>
      {/* headline, letters bounce in */}
      <div
        style={{
          position: "absolute",
          left: v ? 60 : 96,
          top: v ? 250 : 120,
          width: v ? 960 : 1040,
          display: "flex",
          flexWrap: "wrap",
          columnGap: v ? 20 : 24,
          justifyContent: v ? "center" : "flex-start",
        }}
      >
        {/* letters bounce one by one, but each word stays on one line */}
        {title.split(" ").map((word, w, words) => {
          const offset = words.slice(0, w).reduce((n, x) => n + x.length + 1, 0);
          return (
            <span key={w} style={{ display: "inline-block", whiteSpace: "nowrap" }}>
              {word.split("").map((ch, j) => {
                const b = pop(f, 2 + (offset + j) * 1.2, 8);
                return (
                  <span
                    key={j}
                    style={{
                      display: "inline-block",
                      fontFamily: DISPLAY,
                      fontWeight: 500,
                      fontSize: v ? 70 : 84,
                      lineHeight: 1.1,
                      color: C.ink,
                      textShadow: `0 0 40px ${C.glow}`,
                      scale: String(Math.max(0, b)),
                      translate: `0 ${(1 - b) * 60}px`,
                    }}
                  >
                    {ch}
                  </span>
                );
              })}
            </span>
          );
        })}
      </div>

      {!v ? (
        <>
          <AppWindow x={96} y={330} w={860} rotY={10} delay={6}>
            <HudVideo absFrom={sec.from} />
          </AppWindow>
          <Beam a={[px + 10, 520]} b={[930, 560]} delay={12} pulses={[taps[2] + 2]} />
        </>
      ) : null}

      {/* Mini App tabs: two columns beside the phone, popping in one by one, gently floating */}
      {CHIPS.map((c, i) => {
        const b = pop(f, 10 + i * 4, 7);
        const left = i % 2 === 0;
        const row = Math.floor(i / 2);
        const phoneH = pw * 2.17;
        const gap = v ? 18 : 40;
        const cx = left ? px - gap : px + pw + gap;
        const cy = py + phoneH * (0.25 + row * 0.25) + Math.sin(f / 14 + i) * 8;
        return (
          <div
            key={c}
            style={{
              position: "absolute",
              left: cx,
              top: cy,
              translate: left ? "-100% -50%" : "0 -50%",
              scale: String(Math.max(0, b)),
              transformOrigin: left ? "100% 50%" : "0% 50%",
              padding: "10px 20px",
              borderRadius: 999,
              border: `1px solid ${C.core}66`,
              background: "rgba(8,22,24,0.85)",
              boxShadow: `0 0 24px rgba(48,208,190,0.25)`,
              fontFamily: MONO,
              fontSize: v ? 24 : 20,
              letterSpacing: "0.1em",
              color: C.ink,
              whiteSpace: "nowrap",
            }}
          >
            {c}
          </div>
        );
      })}

      <div style={{ position: "absolute", inset: 0, scale: String(0.9 + 0.1 * Math.min(1, pop(f, 0, 10, 140))) }}>
        <Phone
          x={px}
          y={py}
          w={pw}
          rotY={v ? 0 : -8}
          delay={0}
          screens={[
            { src: "chat", from: 0 },
            { src: "dashboard", from: taps[0] + 6 },
            { src: "pc", from: taps[1] + 6 },
          ]}
        >
          <Tap at={taps[0]} x={TAB.dashboard[0]} y={TAB.dashboard[1]} phoneW={pw} />
          <Tap at={taps[1]} x={TAB.pc[0]} y={TAB.pc[1]} phoneW={pw} />
          <Tap at={taps[2]} x={STUDY_MODE[0]} y={STUDY_MODE[1]} phoneW={pw} />
        </Phone>
      </div>

      <Reply
        sec={sec}
        f={f}
        m={{ phrase: 0, think: 0, card: 0, speak: sec.marks.speak }}
        L={v ? { x: 60, y: 1560, w: 960, size: 40 } : { x: 96, y: 920, w: 900, size: 38 }}
        align={v ? "center" : "left"}
      />
    </AbsoluteFill>
  );
};
