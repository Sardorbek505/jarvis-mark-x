"""Сборка ролика: клипы → видео-ассеты, сцены → HyperFrames-композиция (1080×1920, 30 fps).

    python promo/build.py            # ассеты + index.html в promo/build/hf
    python promo/build.py --render   # + рендер (npx hyperframes) и звук (mix.py) → promo/build/jarvis_reels.mp4

Дизайн: тёмная палитра самого Джарвиса (ui.C), бирюзовый акцент — один; Tektur —
фирменный шрифт программы (design/fonts), Montserrat 300/800 — текст. Движение — по
правилам Эмиля Ковальски: ease-out cubic-bezier(.23,1,.32,1) на входах, без ease-in на
появлениях, старт с scale .95, а не с нуля. Сцена: проблема → решение → настоящий экран.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

PROMO = Path(__file__).resolve().parent
ROOT = PROMO.parent
sys.path.insert(0, str(PROMO))

from scenes import INTRO_LINES, INTRO_SEC  # noqa: E402
from timeline import BUILD, VO_AT, plan, total  # noqa: E402

HF = BUILD / "hf"
CLIPS = BUILD / "clips"
A = HF / "assets"
HF_VERSION = "0.8.112"

# Сцена (окно 1280×800 в логических px) — куда смотрит камера: [(сек, масштаб, cx, cy)].
FIT = 1000 / 1280
ORB_FOCUS = (497, 352)
CAM = {
    "study": [(0, FIT, 640, 400), (1.4, FIT, 640, 400), (2.6, 1.32, 760, 470), (6.5, 1.32, 760, 300)],
    "calls": [(0, FIT, 640, 400), (1.2, FIT, 640, 400), (2.4, 1.35, 1010, 300), (6.0, 1.35, 1010, 560)],
    "commands": [(0, FIT, 640, 400), (1.2, FIT, 640, 400), (2.4, 1.35, 1000, 260), (6.5, 1.35, 1000, 520)],
    "safety": [(0, FIT, 640, 400), (1.2, FIT, 640, 400), (2.4, 1.4, 760, 300), (7.0, 1.4, 760, 520)],
    "setup": [(0, FIT, 640, 400), (1.2, FIT, 640, 400), (2.4, 1.35, 800, 260), (5.5, 1.35, 800, 520)],
}
CARD_AT = 2.0          # в клипе карточка результата появляется на 2,0 с (capture.cycle)
STATE_COLORS = [("Ждёт", "#30d0be"), ("Слушает", "#46e880"), ("Думает", "#b6e240"), ("Говорит", "#ff8a34")]


def sh(*cmd):
    subprocess.run([str(c) for c in cmd], check=True)


def encode():
    """PNG-последовательности → видео. HUD — H.264 (2560×1600), капсула — VP9 с альфой."""
    A.mkdir(parents=True, exist_ok=True)
    for d in sorted(CLIPS.iterdir()):
        if not d.is_dir():
            continue
        alpha = d.name.startswith("i_")
        out = A / (d.name + (".webm" if alpha else ".mp4"))
        newest = max(p.stat().st_mtime for p in d.glob("*.png"))
        if out.exists() and out.stat().st_mtime > newest:
            continue
        if alpha:
            sh("ffmpeg", "-loglevel", "error", "-y", "-framerate", 30, "-i", d / "%05d.png", "-c:v", "libvpx-vp9",
               "-pix_fmt", "yuva420p", "-crf", 22, "-b:v", 0, "-g", 15, "-row-mt", 1, out)
        else:
            sh("ffmpeg", "-loglevel", "error", "-y", "-framerate", 30, "-i", d / "%05d.png", "-c:v", "libx264",
               "-crf", 14, "-preset", "medium", "-pix_fmt", "yuv420p", "-g", 15, out)
        print("encoded", out.name, flush=True)


def card_box(clip: str) -> tuple[int, int, int, int] | None:
    """Где на кадре карточка результата (логические px): ищем её рамку справа от шара."""
    frames = sorted((CLIPS / clip).glob("*.png"))
    if not frames:
        return None
    im = np.asarray(Image.open(frames[-1]).convert("RGB")).astype(int)
    k = im.shape[1] / 1280
    x0 = int(824 * k)
    col = im[:, x0 - 2:x0 + 3].max(axis=1).max(axis=1)     # левая рамка карточки
    ys = np.where(col > 70)[0]
    ys = ys[(ys > 60 * k) & (ys < 700 * k)]
    if not len(ys):
        return None
    top, bot = ys.min() / k, ys.max() / k
    return 822, int(top) - 2, 1266, int(bot) + 3


def stills():
    """Кадры экранов для «десятков окон» (hook) — последние кадры настоящих экранов."""
    out = []
    for name in ("c_commands", "c_study", "c_calls", "c_safety", "c_setup", "c_music", "c_weather", "c_memory"):
        frames = sorted((CLIPS / name).glob("*.png"))
        if frames:
            Image.open(frames[-1]).convert("RGB").resize((1280, 800), Image.LANCZOS).save(
                A / f"still_{name}.jpg", quality=86)
            out.append(f"assets/still_{name}.jpg")
    return out


def fonts():
    fdir = A / "fonts"
    fdir.mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "design" / "fonts" / "Tektur-Medium.ttf", fdir)
    src = Path("/tmp/claude-0/fonts/package/files")
    if not src.exists():
        tmp = BUILD / "fontsource"
        tmp.mkdir(exist_ok=True)
        sh("bash", "-c", f"cd {tmp} && npm pack @fontsource/montserrat@5.3.0 --silent && tar xzf *.tgz")
        src = tmp / "package" / "files"
    for w in (300, 500, 600, 800):
        for sub in ("cyrillic", "latin"):
            shutil.copy(src / f"montserrat-{sub}-{w}-normal.woff2", fdir)
    gsap = BUILD / "node_modules" / "gsap" / "dist" / "gsap.min.js"
    if not gsap.exists():
        sh("npm", "i", "--prefix", BUILD, f"hyperframes@{HF_VERSION}", "gsap@3.14.2", "--silent")
    shutil.copy(gsap, A / "gsap.min.js")


FONT_CSS = "\n".join(
    f"@font-face{{font-family:M;font-weight:{w};src:url(assets/fonts/montserrat-{s}-{w}-normal.woff2) format('woff2');"
    f"unicode-range:{r}}}"
    for w in (300, 500, 600, 800)
    for s, r in (("cyrillic", "U+0301,U+0400-045F,U+0490-0491,U+04B0-04B1,U+2116"),
                 ("latin", "U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+2000-206F,U+20AC,"
                           "U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD")))
) + "\n@font-face{font-family:T;src:url(assets/fonts/Tektur-Medium.ttf)}"

CSS = """
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:1080px;height:1920px;overflow:hidden;background:#030609}
#root{position:relative;width:100%;height:100%;font-family:M,sans-serif;color:#e6f3fa;overflow:hidden}
.bg{position:absolute;inset:0;background:radial-gradient(120% 70% at 50% 46%,#0b1a1f 0%,#05090d 55%,#030609 100%)}
.glow{position:absolute;left:-200px;top:420px;width:1480px;height:1300px;border-radius:50%;
  background:radial-gradient(closest-side,var(--g,#30d0be) 0%,transparent 100%);opacity:0}
.sc{position:absolute;inset:0;opacity:0}
.problem{position:absolute;left:80px;top:300px;width:920px;font-weight:300;font-size:46px;line-height:1.18;
  color:#8fb0c4;letter-spacing:-0.01em}
.head{position:absolute;left:80px;top:366px;width:930px;font-weight:800;font-size:86px;line-height:1.03;
  letter-spacing:-0.035em;color:#f4fbff}
.frame{position:absolute;left:40px;top:640px;width:1000px;height:840px;border-radius:40px;overflow:hidden;
  background:#030609;border:1px solid #1b2631;box-shadow:0 40px 90px -20px rgba(0,0,0,.85),0 0 0 1px rgba(63,208,189,.05)}
.cam{position:absolute;left:0;top:0;width:1280px;height:800px;transform-origin:0 0}
.cam video,.ccam video{position:absolute;left:0;top:0;width:1280px;height:800px}
.cardwrap{position:absolute;left:70px;width:940px;border-radius:28px;overflow:hidden;opacity:0;
  box-shadow:0 30px 80px -10px rgba(0,0,0,.9),0 0 0 1px rgba(255,255,255,.04)}
.ccam{position:absolute;left:0;top:0;width:1280px;height:800px;transform-origin:0 0}
.isl{position:absolute;left:60px;top:8px;width:960px;height:680px}
.isl.big{top:760px}
.chips{position:absolute;left:70px;top:1386px;width:940px;display:flex;gap:14px;justify-content:center}
.chip{display:flex;align-items:center;gap:12px;padding:16px 24px;border-radius:40px;background:rgba(5,9,13,.86);
  border:1px solid #243240;font-weight:600;font-size:30px;color:#8fb0c4;opacity:.55}
.chip i{width:16px;height:16px;border-radius:50%;display:block}
.caps{position:absolute;left:60px;top:1550px;width:960px;height:220px}
.cap{position:absolute;left:0;top:0;width:960px;text-align:center;font-weight:600;font-size:52px;line-height:1.2;
  color:#fff5df;opacity:0;letter-spacing:-0.01em}
.mark{position:absolute;left:0;width:1080px;text-align:center;font-family:T,sans-serif;color:#f4fbff}
.tag{position:absolute;left:80px;width:920px;text-align:center;font-weight:300;font-size:46px;color:#8fb0c4;line-height:1.25}
.wall{position:absolute;left:-180px;top:180px;width:1440px;height:1600px;opacity:0}
.wall img{position:absolute;width:640px;height:400px;border-radius:22px;border:1px solid #1b2631;object-fit:cover}
.big1{position:absolute;left:80px;width:920px;font-weight:300;font-size:92px;line-height:1.04;letter-spacing:-0.035em;color:#8fb0c4}
.big2{position:absolute;left:80px;width:920px;font-weight:800;font-size:112px;line-height:1.0;letter-spacing:-0.04em;color:#f4fbff}
.intro{position:absolute;inset:0;width:1080px;height:1920px}
"""


def esc(t: str) -> str:
    return html.escape(t, quote=True)


def cam_xy(s: float, cx: float, cy: float, w=1000, h=840) -> dict:
    return {"scale": round(s, 4), "x": round(w / 2 - cx * s, 1), "y": round(h / 2 - cy * s, 1)}


def caption_groups(text: str, start: float, sec: float) -> list[tuple[float, float, str]]:
    """Реплика → группы по 2–5 слов, разрез по знакам препинания; время — пропорционально символам."""
    words = text.split()
    groups, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) >= 5 or (len(cur) >= 2 and re.search(r"[.,:;?!»—]$", w)) or (len(cur) >= 3 and len(" ".join(cur)) > 26):
            groups.append(" ".join(cur))
            cur = []
    if cur:
        if groups and len(cur) == 1:
            groups[-1] += " " + cur[0]
        else:
            groups.append(" ".join(cur))
    total_chars = sum(len(g) + 2 for g in groups)
    out, t = [], start
    for g in groups:
        d = sec * (len(g) + 2) / total_chars
        out.append((t, d, g))
        t += d
    return out


def html_doc() -> str:
    P = plan()
    T = total()
    dom, js = [], []
    caps = []

    def vid(vid_id: str, src: str, start: float, dur: float, media_start: float = 0.0, cls: str = "", extra=""):
        return (f'<video id="{vid_id}" class="{cls}" src="{src}" data-start="{start:.3f}" data-duration="{dur:.3f}"'
                f' data-media-start="{media_start:.3f}" data-track-index="1" muted playsinline{extra}></video>')

    # интро — родная сцена «два хлопка»
    dom.append(f'<div class="sc" id="s_intro">{vid("v_intro", "assets/intro.mp4", 0, INTRO_SEC, cls="intro")}</div>')
    js.append(f"tl.set('#s_intro',{{opacity:1}},0);tl.to('#s_intro',{{opacity:0,duration:.4,ease:'power2.in'}},{INTRO_SEC - 0.4});")
    for at, text in INTRO_LINES:
        from timeline import durations
        caps += caption_groups(text, at, durations()[f"intro{INTRO_LINES.index((at, text))}"]["sec"])

    still_list = stills()
    for i, s in enumerate(P):
        S, L, k, sid = s["start"], s["sec"], s["kind"], s["id"]
        sel = f"#s_{sid}"
        parts = []
        caps += caption_groups(s["vo"], S + VO_AT, s["vo_sec"])
        js.append(f"tl.set('{sel}',{{opacity:1}},{S});")
        js.append(f"tl.to('{sel}',{{opacity:0,y:-60,duration:.36,ease:'power2.in'}},{S + L - 0.36});")
        if "problem" in s:
            parts.append(f'<div class="problem" id="p_{sid}">{esc(s["problem"])}</div>')
            parts.append(f'<div class="head" id="h_{sid}">{esc(s["head"])}</div>')
            if k != "hook":
                js.append(f"tl.fromTo('#p_{sid}',{{opacity:0,y:22}},{{opacity:1,y:0,duration:.6,ease:EO}},{S + 0.12});")
                js.append(f"tl.fromTo('#h_{sid}',{{opacity:0,y:34,filter:'blur(6px)'}},"
                          f"{{opacity:1,y:0,filter:'blur(0px)',duration:.75,ease:EO}},{S + 0.5});")
        if k in ("orb", "states", "multi", "page"):
            clip = f"c_{sid}"
            parts.append(f'<div class="frame" id="f_{sid}"><div class="cam" id="cam_{sid}">'
                         f'{vid("v_" + sid, f"assets/{clip}.mp4", S, L)}</div></div>')
            js.append(f"tl.fromTo('#f_{sid}',{{opacity:0,y:70,scale:.95}},{{opacity:1,y:0,scale:1,duration:.85,ease:EO}},{S + 0.08});")
            if k == "page":
                keys = CAM[sid]
            elif k == "multi":                       # шар и карточка вместе: три команды подряд
                keys = [(0, FIT, 640, 400), (0.7, FIT, 640, 400), (1.8, 1.0, 780, 400)]
            else:
                keys = [(0, FIT, 640, 400), (0.7, FIT, 640, 400), (2.0, 1.55, *ORB_FOCUS)]
            first = cam_xy(*keys[0][1:])
            js.append(f"tl.set('#cam_{sid}',{json.dumps(first)},{S});")
            for (t0, *_a), (t1, *b) in zip(keys, keys[1:]):
                if b != list(_a):
                    js.append(f"tl.to('#cam_{sid}',{{...{json.dumps(cam_xy(*b))},duration:{t1 - t0:.2f},"
                              f"ease:'power2.inOut'}},{S + t0});")
        # карточка результата — тот же клип, крупно, «выезжает» из окна
        card_clip = {"orb": f"c_{sid}", "page": "c_calendar" if sid == "study" else None}.get(k)
        if card_clip:
            box = card_box(card_clip)
            if box:
                x0, y0, x1, y1 = box
                sc = 940 / (x1 - x0)
                h = (y1 - y0) * sc
                top = min(1480 - h * 0.62, 1500 - h)
                card_at = 3.6 if k == "page" else CARD_AT + 0.15
                media = 1.8 if k == "page" else 0.0
                start = S + (card_at - CARD_AT - 0.15 if k == "page" else 0)
                parts.append(f'<div class="cardwrap" id="cw_{sid}" style="top:{top:.0f}px;height:{h:.0f}px">'
                             f'<div class="ccam" style="transform:translate({-x0 * sc:.1f}px,{-y0 * sc:.1f}px) scale({sc:.4f})">'
                             f'{vid("vc_" + sid, f"assets/{card_clip}.mp4", start, S + L - start, media)}</div></div>')
                js.append(f"tl.fromTo('#cw_{sid}',{{opacity:0,y:50,scale:.95}},{{opacity:1,y:0,scale:1,duration:.7,ease:EO}},"
                          f"{S + card_at});")
        if s.get("island") or k == "island":
            big = " big" if k == "island" else ""
            parts.append(vid("vi_" + sid, f"assets/i_{sid}.webm", S, L, cls="isl" + big))
            if big:
                js.append(f"tl.fromTo('#vi_{sid}',{{scale:.95,opacity:0}},{{scale:1.06,opacity:1,duration:.8,ease:EO}},{S + 0.1});")
        if k == "states":
            chips = "".join(f'<div class="chip" id="ch_{j}"><i style="background:{c}"></i>{n}</div>'
                            for j, (n, c) in enumerate(STATE_COLORS))
            parts.append(f'<div class="chips">{chips}</div>')
            vo, text = s["vo_sec"], s["vo"]
            times = [S + 0.2] + [S + VO_AT + vo * text.find(w) / len(text) for w in ("слушаю", "думаю", "отвечаю")]
            for j, (t, (n, c)) in enumerate(zip(times, STATE_COLORS)):
                js.append(f"tl.to('#ch_{j}',{{opacity:1,color:'#f4fbff',borderColor:'{c}',duration:.25,ease:EO}},{t:.2f});")
                if j < 3:
                    js.append(f"tl.to('#ch_{j}',{{opacity:.55,color:'#8fb0c4',borderColor:'#243240',duration:.25}},{times[j + 1]:.2f});")
        if k == "hook":
            imgs = "".join(f'<img src="{src}" style="left:{(j % 2) * 700}px;top:{(j // 2) * 450}px">'
                           for j, src in enumerate(still_list[:8]))
            parts.append(f'<div class="wall" id="w_{sid}">{imgs}</div>')
            parts[0] = parts[0].replace('class="problem"', 'class="big1" style="top:700px"')
            parts[1] = parts[1].replace('class="head"', 'class="big2" style="top:930px"')
            js.append(f"tl.fromTo('#w_{sid}',{{opacity:0,y:120,rotation:-8}},{{opacity:.42,y:-60,rotation:-8,duration:{L * 0.55:.2f},ease:'power1.out'}},{S});")
            js.append(f"tl.to('#w_{sid}',{{opacity:.08,filter:'blur(10px)',duration:.6,ease:EO}},{S + 1.7});")
            js.append(f"tl.fromTo('#p_{sid}',{{opacity:0,y:30}},{{opacity:1,y:0,duration:.7,ease:EO}},{S + 0.25});")
            js.append(f"tl.fromTo('#h_{sid}',{{opacity:0,y:40,scale:.96}},{{opacity:1,y:0,scale:1,duration:.8,ease:EO}},{S + 1.9});")
        if k == "title":
            parts.append('<div class="mark" id="m_title" style="top:760px;font-size:118px;letter-spacing:.08em">Д.Ж.А.Р.В.И.С.</div>')
            parts.append('<div class="mark" id="m_title2" style="top:910px;font-size:38px;letter-spacing:.5em;color:#3fd0bd">MARK X</div>')
            parts.append('<div class="tag" id="t_title" style="top:1010px">Голосовой ИИ-ассистент для вашего компьютера</div>')
            js.append(f"tl.fromTo('#m_title',{{opacity:0,letterSpacing:'.3em',filter:'blur(12px)'}},"
                      f"{{opacity:1,letterSpacing:'.08em',filter:'blur(0px)',duration:1.1,ease:EO}},{S + 0.05});")
            js.append(f"tl.fromTo('#m_title2',{{opacity:0}},{{opacity:1,duration:.6,ease:EO}},{S + 0.6});")
            js.append(f"tl.fromTo('#t_title',{{opacity:0,y:24}},{{opacity:1,y:0,duration:.7,ease:EO}},{S + 0.9});")
        if k == "outro":
            parts.append(vid("v_outro", "assets/intro.mp4", S, L, max(0.0, INTRO_SEC - 0.4 - L), cls="intro"))
            parts.append('<div class="mark" id="m_out" style="top:1180px;font-size:104px;letter-spacing:.08em">Д.Ж.А.Р.В.И.С.</div>')
            parts.append('<div class="mark" id="m_out2" style="top:1320px;font-size:34px;letter-spacing:.5em;color:#3fd0bd">MARK X</div>')
            js.append(f"tl.fromTo('#m_out',{{opacity:0,y:30}},{{opacity:1,y:0,duration:.9,ease:EO}},{S + L * 0.45:.2f});")
            js.append(f"tl.fromTo('#m_out2',{{opacity:0}},{{opacity:1,duration:.6,ease:EO}},{S + L * 0.45 + 0.4:.2f});")
        dom.append(f'<div class="sc" id="s_{sid}">' + "".join(parts) + "</div>")

    for j, (t, d, g) in enumerate(caps):
        dom.append(f'<div class="cap" id="cap{j}">{esc(g)}</div>')
        js.append(f"tl.fromTo('#cap{j}',{{opacity:0,y:10}},{{opacity:1,y:0,duration:.18,ease:'power2.out'}},{t:.3f});")
        js.append(f"tl.to('#cap{j}',{{opacity:0,duration:.12}},{t + d - 0.06:.3f});")
    caps_html = '<div class="caps">' + "".join(x for x in dom if 'class="cap"' in x) + "</div>"
    dom = [x for x in dom if 'class="cap"' not in x]

    return f"""<!doctype html>
<html lang="ru" data-resolution="portrait"><head><meta charset="UTF-8"><meta name="viewport" content="width=1080, height=1920">
<script src="assets/gsap.min.js"></script>
<style>{FONT_CSS}{CSS}</style></head>
<body><div id="root" data-composition-id="main" data-start="0" data-duration="{T:.3f}" data-width="1080" data-height="1920">
<div class="bg"></div>
{''.join(dom)}
{caps_html}
</div>
<script>
const EO = "cubic-bezier(0.23,1,0.32,1)";
const tl = gsap.timeline({{paused:true}});
{chr(10).join(js)}
tl.set({{}},{{}},{T:.3f});
window.__timelines["main"] = tl;
</script></body></html>"""


def intro_video():
    out = A / "intro.mp4"
    src = CLIPS / "intro"
    if out.exists() and out.stat().st_mtime > max(p.stat().st_mtime for p in src.glob("*.png")):
        return
    sh("ffmpeg", "-loglevel", "error", "-y", "-framerate", 30, "-i", src / "%05d.png", "-c:v", "libx264", "-crf", 14,
       "-pix_fmt", "yuv420p", "-g", 15, out)


def project():
    HF.mkdir(parents=True, exist_ok=True)
    (HF / "hyperframes.json").write_text(json.dumps({
        "$schema": "https://hyperframes.heygen.com/schema/hyperframes.json",
        "paths": {"blocks": "compositions", "components": "compositions/components", "assets": "assets"},
        "media": {"autoProxy": True}}, indent=2))
    (HF / "meta.json").write_text(json.dumps({"id": "jarvis-reels", "name": "jarvis-reels"}))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--render", action="store_true")
    ap.add_argument("--quality", default="delivery")
    a = ap.parse_args()
    project()
    A.mkdir(parents=True, exist_ok=True)
    fonts()
    encode()
    intro_video()
    (HF / "index.html").write_text(html_doc(), "utf-8")
    print("composition:", HF / "index.html", "duration", total())
    if a.render:
        env = {"HYPERFRAMES_NO_TELEMETRY": "1"}
        import os
        silent = BUILD / "video_silent.mp4"
        subprocess.run(["npx", "--prefix", str(BUILD), "hyperframes", "render", "--fps", "30", "--quality", a.quality,
                        "--output", str(silent)], cwd=HF, check=True, env={**os.environ, **env})
        sh(sys.executable, PROMO / "mix.py")


if __name__ == "__main__":
    main()
