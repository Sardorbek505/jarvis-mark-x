import "./index.css";
import { Composition, Folder } from "remotion";
import { HelloWorld } from "./HelloWorld";
import { Logo } from "./HelloWorld/Logo";
import { JarvisAd, JarvisAdScene } from "./JarvisAd/JarvisAd";
import { timeline } from "./JarvisAd/theme";

// Each <Composition> is an entry in the sidebar!

export const RemotionRoot: React.FC = () => {
  return (
    <>
      {/* JARVIS Mark X ad: npx remotion render JarvisAd out/JarvisAd.mp4 */}
      <Composition
        id="JarvisAd"
        component={JarvisAd}
        durationInFrames={timeline.durationInFrames}
        fps={timeline.fps}
        width={1920}
        height={1080}
      />
      {/* 9:16 cut for Reels / Shorts / TikTok: npx remotion render JarvisAdVertical out/JarvisAdVertical.mp4 */}
      <Composition
        id="JarvisAdVertical"
        component={JarvisAd}
        durationInFrames={timeline.durationInFrames}
        fps={timeline.fps}
        width={1080}
        height={1920}
      />
      <Folder name="JarvisAd-Scenes">
        {timeline.sections.map((s) => (
          <Composition
            key={s.id}
            id={`JarvisAd-${s.id}`}
            component={JarvisAdScene}
            durationInFrames={s.durationInFrames}
            fps={timeline.fps}
            width={1920}
            height={1080}
            defaultProps={{ id: s.id }}
          />
        ))}
      </Folder>

      <Composition
        // You can take the "id" to render a video:
        // npx remotion render HelloWorld
        id="HelloWorld"
        component={HelloWorld}
        durationInFrames={150}
        fps={30}
        width={1920}
        height={1080}
        // You can override these props for each render:
        // https://www.remotion.dev/docs/parametrized-rendering
        defaultProps={{
          titleText: "Welcome to Remotion",
          titleColor: "#000000",
          logoColor1: "#91EAE4",
          logoColor2: "#86A8E7",
        }}
      />

      {/* Mount any React component to make it show up in the sidebar and work on it individually! */}
      <Composition
        id="OnlyLogo"
        component={Logo}
        durationInFrames={150}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={{
          logoColor1: "#91dAE2",
          logoColor2: "#86A8E7",
        }}
      />
    </>
  );
};
