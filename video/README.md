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

**JARVIS Mark X ad** (`JarvisAd`, 41 s, 1920×1080)

```console
npx remotion render JarvisAd out/JarvisAd.mp4
```

Voice-over, score, sound effects, scene timing and images are pre-built and
committed (`public/jarvis-ad/`, `src/JarvisAd/timeline.json`). To change the
script or the mix, edit `scripts/jarvis_ad/build.py` and rebuild (Python 3.10+,
downloads the Kokoro TTS model and SFX into `.cache/` on first run):

```console
pip install -r scripts/jarvis_ad/requirements.txt
python scripts/jarvis_ad/build.py
```

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
