import { loadFont } from "@remotion/fonts";
import { Easing, interpolate, staticFile } from "remotion";
import timelineJson from "./timeline.json";

// Timeline written by scripts/jarvis_ad/build.py (voice-over lengths snapped to a 100 BPM grid).
export type SceneId = "boot" | "title" | "voice" | "vision" | "memory" | "control" | "end";
export type SceneInfo = {
  id: SceneId;
  from: number;
  durationInFrames: number;
  beats: number;
  vo: { text: string; from: number; durationInFrames: number };
};
export const timeline = timelineJson as unknown as {
  fps: number;
  beatFrames: number;
  durationInFrames: number;
  scenes: SceneInfo[];
  voiceLevel: number[];
};
export const scene = (id: SceneId) => timeline.scenes.find((s) => s.id === id)!;

// Palette taken from the product art (assets/*.jpg): deep void, arc-reactor cyan, teal status.
export const C = {
  void: "#02060d",
  panel: "rgba(8, 20, 34, 0.62)",
  line: "rgba(190, 240, 255, 0.16)",
  core: "#2ec8ff",
  coreDeep: "#0a6fa8",
  hot: "#d9f7ff",
  glow: "rgba(46, 200, 255, 0.55)",
  teal: "#3ff0c8",
  amber: "#ffb547",
  ink: "#eaf8ff",
  muted: "rgba(206, 236, 250, 0.62)",
  dim: "rgba(206, 236, 250, 0.32)",
};

// Motion language — one entrance curve, one exit curve, one move curve; base unit 12 frames.
export const ENTER = Easing.bezier(0.16, 1, 0.3, 1);
export const EXIT = Easing.bezier(0.7, 0, 0.84, 0);
export const MOVE = Easing.bezier(0.65, 0, 0.35, 1);
export const DRIFT = Easing.bezier(0.33, 0, 0.67, 1);
export const UNIT = 12;
export const STAGGER = 3;

export const tween = (
  frame: number,
  [a, b]: [number, number],
  [from, to]: [number, number],
  easing: (t: number) => number = ENTER,
) =>
  interpolate(frame, [a, b], [from, to], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing,
  });

export const DISPLAY = "Tektur";
export const MONO = "JetBrains Mono";

loadFont({ family: DISPLAY, url: staticFile("jarvis-ad/fonts/Tektur-Regular.ttf"), weight: "400" });
loadFont({ family: DISPLAY, url: staticFile("jarvis-ad/fonts/Tektur-Medium.ttf"), weight: "500" });
loadFont({ family: MONO, url: staticFile("jarvis-ad/fonts/JetBrainsMono-500.woff2"), weight: "500" });

/** Voice loudness (0–1) at an absolute frame — drives the audio-reactive bits. */
export const voiceAt = (absFrame: number) => {
  const v = timeline.voiceLevel;
  const i = Math.max(0, Math.min(v.length - 1, Math.round(absFrame)));
  // light smoothing so glows breathe instead of flicker
  const a = v[Math.max(0, i - 1)] ?? 0;
  const b = v[Math.min(v.length - 1, i + 1)] ?? 0;
  return (a + 2 * v[i] + b) / 4;
};

/**
 * Approximate word timings for a voice-over line: words share the line's
 * duration by length, with extra room after commas and full stops (Kokoro
 * pauses there). Good enough for karaoke-style highlighting.
 */
export const wordTimings = (text: string, durationInFrames: number) => {
  const words = text.split(" ");
  const weight = (w: string) => w.replace(/[^a-z0-9]/gi, "").length + 2 + (/[.!?]$/.test(w) ? 6 : /,$/.test(w) ? 3 : 0);
  const total = words.reduce((s, w) => s + weight(w), 0);
  let acc = 0;
  return words.map((w) => {
    const start = (acc / total) * durationInFrames;
    acc += weight(w);
    return { word: w, start, end: (acc / total) * durationInFrames };
  });
};
