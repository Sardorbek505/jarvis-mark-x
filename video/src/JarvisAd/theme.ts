import { loadFont } from "@remotion/fonts";
import { Easing, interpolate, staticFile, useVideoConfig } from "remotion";
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
  lang: "ru" | "en";
  ui: Record<UiKey, string>;
};
export type UiKey =
  | "voiceLabel" | "voiceHead" | "visionLabel" | "visionHead" | "memoryLabel" | "memoryHead"
  | "controlLabel" | "controlHead" | "tagline" | "cta";
/** On-screen text in the voice-over's language (scripts/jarvis_ad/script.json). */
export const ui = (key: UiKey) => timeline.ui[key];
export const scene = (id: SceneId) => timeline.scenes.find((s) => s.id === id)!;

// Palette taken from the product itself: the app's idle teal (ui.py _STATE_RGB "ОЖИДАЕТ"),
// its "speaking" orange, its "listening" green, on the app's near-black.
export const C = {
  void: "#03070a",
  panel: "rgba(10, 18, 22, 0.72)",
  line: "rgba(190, 255, 245, 0.14)",
  core: "#30d0be",
  coreDeep: "#137a70",
  hot: "#d6fff8",
  glow: "rgba(48, 208, 190, 0.5)",
  teal: "#46e880",
  amber: "#ff8a34",
  ink: "#eefcfa",
  muted: "rgba(214, 240, 236, 0.66)",
  dim: "rgba(214, 240, 236, 0.34)",
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

/** True for the 9:16 cut (Reels / Shorts / TikTok). */
export const useVertical = () => {
  const { width, height } = useVideoConfig();
  return height > width;
};

export const DISPLAY = "Tektur";
export const MONO = "JetBrains Mono";

loadFont({ family: DISPLAY, url: staticFile("jarvis-ad/fonts/Tektur-Regular.ttf"), weight: "400" });
loadFont({ family: DISPLAY, url: staticFile("jarvis-ad/fonts/Tektur-Medium.ttf"), weight: "500" });
loadFont({
  family: MONO,
  url: staticFile("jarvis-ad/fonts/JetBrainsMono-500.woff2"),
  weight: "500",
  unicodeRange:
    "U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD",
});
loadFont({
  family: MONO,
  url: staticFile("jarvis-ad/fonts/JetBrainsMono-500-cyrillic.woff2"),
  weight: "500",
  unicodeRange: "U+0301,U+0400-045F,U+0490-0491,U+04B0-04B1,U+2116",
});

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
