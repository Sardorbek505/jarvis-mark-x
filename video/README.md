# Remotion video

<p align="center">
  <a href="https://github.com/remotion-dev/logo">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="https://github.com/remotion-dev/logo/raw/main/animated-logo-banner-dark.apng">
      <img alt="Animated Remotion Logo" src="https://github.com/remotion-dev/logo/raw/main/animated-logo-banner-light.gif">
    </picture>
  </a>
</p>

Welcome to your Remotion project!

## Commands

**Install Dependencies**

```console
npm i
```

**Start Preview**

```console
npm run dev
```

**Render video**

```console
npx remotion render
```

**Render a specific composition** (`HelloWorld`, `OnlyLogo` — see `src/Root.tsx`)

```console
npx remotion render HelloWorld out/HelloWorld.mp4
```

Output goes to `out/` (git-ignored).

> Offline / restricted-network machines: Remotion downloads its own
> `chrome-headless-shell` on first render. If that is blocked, point it at an
> existing headless shell (a full Chrome binary will not work):
> `npx remotion render HelloWorld --browser-executable=/path/to/headless_shell`

**JARVIS Mark X ad** (`JarvisAd` 16:9 and `JarvisAdVertical` 9:16, ~75 s, a tour of all 15 skills)

```console
npx remotion render JarvisAd out/JarvisAd.mp4
npx remotion render JarvisAdVertical out/JarvisAdVertical.mp4
```

Everything is driven by `scripts/jarvis_ad/storyboard.json`: per feature the
user's phrase, the real tool call + mock result (shown as the app's own result
card), Jarvis's reply and the length in beats (120 BPM). On-screen text is in
its `ui` block.

Russian voice = the app's own Fish Audio «русский Джарвис». Generate it where
the Fish key and network are (e.g. your PC, it reads the key the app saved):

```console
python scripts/jarvis_ad/fish_vo.py            # -> scripts/jarvis_ad/vo/ru/<id>.mp3, commit them
```

Until those files exist the mix has no voice and the audio-reactive bits run
on a placeholder envelope. Then rebuild audio + timeline (sections stretch by
whole beats to fit each line), re-record the HUD to the new timing, render:

```console
pip install -r scripts/jarvis_ad/requirements.txt
python scripts/jarvis_ad/build.py              # -> timeline.json + public/jarvis-ad/mix.mp3
python scripts/capture/capture_pc.py
```

Sound effects: drop files into `scripts/jarvis_ad/sfx/<role>/` (git-ignored —
licensed packs are not redistributed). Roles: ambience, boot, slam, glitch,
whoosh, slide, type, think, card, tap, riser, lock, ignite, aura, cta;
`card-<feature id>/` gives one feature its own card sound. Variants in a folder
rotate. Roles without files fall back to the Mixkit set in `build.py`.

Every product shot is the real app, captured with fictional demo data (no
personal data) on a throwaway copy of the sources:

```console
python scripts/capture/capture_pc.py     # PC app pages + live HUD session -> public/jarvis-ad/pc/
python scripts/capture/capture_phone.py  # Telegram Mini App tabs (393×852 @3x) -> public/jarvis-ad/phone/
```

`capture_pc.py` runs the PyQt6 app offscreen on a virtual clock and records the
HUD frame by frame in sync with the timeline (`src/JarvisAd/timeline.json`),
so re-run it after changing the storyboard. It needs the repo's
`requirements-dev.txt` plus `libegl1` and `libportaudio2`;
`capture_phone.py` needs Playwright and Chromium.

Credits and licenses: `scripts/jarvis_ad/CREDITS.md`.

**Upgrade Remotion**

```console
npx remotion upgrade
```

## Docs

Get started with Remotion by reading the [fundamentals page](https://www.remotion.dev/docs/the-fundamentals).

## Help

We provide help on our [Discord server](https://discord.gg/6VzzNDwUwV).

## Issues

Found an issue with Remotion? [File an issue here](https://github.com/remotion-dev/remotion/issues/new).

## License

Note that for some entities a company license is needed. [Read the terms here](https://github.com/remotion-dev/remotion/blob/main/LICENSE.md).
