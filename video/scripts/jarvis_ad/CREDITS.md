# JarvisAd — credits and licenses

| What | Source | License |
|---|---|---|
| Voice-over (RU) | [Fish Audio](https://fish.audio) voice `680d74fbef69419f87cfc70f092a1451` («русский Джарвис», the JARVIS app's default voice), model `s2.1-pro-free`, via `fish_vo.py` | Fish Audio terms; check the voice model's own terms before publishing an ad |
| Music | Original cue, synthesized by `build.py` | this repo |
| Sound effects (main) | Packs supplied by the owner, placed in `sfx/<role>/` locally — not committed, not redistributed | the packs' own licenses |
| Sound effects (fallback) | [Mixkit](https://mixkit.co/free-sound-effects/), fetched from [video-shotcraft](https://github.com/louiseliu/hyperFrames-video-shotcraft) @ `1df77f1` (URLs per file in its `assets/audio/ATTRIBUTION.md`) | Mixkit Sound Effects Free License |
| Product footage | The real PC app and Telegram Mini App of this repo, captured by `scripts/capture/` with fictional demo data | this repo |
| Tektur font | `design/fonts/` | SIL OFL 1.1 (`public/jarvis-ad/fonts/Tektur-OFL.txt`) |
| JetBrains Mono font | `@fontsource/jetbrains-mono` | SIL OFL 1.1 (`public/jarvis-ad/fonts/JetBrainsMono-OFL.txt`) |
| Capture-time fonts (not shipped) | Noto Sans, JetBrains Mono, Inter from [google/fonts](https://github.com/google/fonts) — stand-ins for Segoe UI / Consolas / iOS system font | SIL OFL 1.1 |

Fallback sound effects (Mixkit), used only for roles with no owner file:

- `scifi/tech-hum-futuristic` — https://assets.mixkit.co/active_storage/sfx/2133/2133-preview.mp3
- `data/power-up-electronic` — https://assets.mixkit.co/active_storage/sfx/2602/2602-preview.mp3
- `impact/bass-hit-futuristic` — https://assets.mixkit.co/active_storage/sfx/2303/2303-preview.mp3
- `data/whoosh-electric` — https://assets.mixkit.co/active_storage/sfx/2596/2596-preview.mp3
- `data/data-scan` — https://assets.mixkit.co/active_storage/sfx/2847/2847-preview.mp3
- `light/shimmer-sparkle-sweep` — https://assets.mixkit.co/active_storage/sfx/2633/2633-preview.mp3
- `scifi/hitech-bleep` — https://assets.mixkit.co/active_storage/sfx/2521/2521-preview.mp3
- `mech/lock-digital` — https://assets.mixkit.co/active_storage/sfx/2859/2859-preview.mp3
- `impact/impact-cine-big` — https://assets.mixkit.co/active_storage/sfx/788/788-preview.mp3
- `light/light-aura` — https://assets.mixkit.co/active_storage/sfx/2581/2581-preview.mp3

Motion references studied (patterns re-implemented, no code copied):
[NullMotion](https://github.com/blixvip/NullMotion) (HUD depth layering, kinetic headline, blur reveal),
[remotion-dev/skills](https://github.com/remotion-dev/skills) (Remotion best practices),
[iart-ai/motion-design-skills](https://github.com/iart-ai/motion-design-skills) (motion language, hierarchy, restraint),
[video-shotcraft](https://github.com/louiseliu/hyperFrames-video-shotcraft) (beat-locked cuts, sound-design phrasing),
[heygen-com/hyperframes](https://github.com/heygen-com/hyperframes) (motion-graphics workflow).
