// Покадровый рендер promo/index.html → MP4 1080x1920 @30fps.
//   node promo/render.cjs                 — полный ролик (promo/jarvis_promo.mp4)
//   node promo/render.cjs --stills 1,5.2  — отдельные кадры в promo/stills/
// Нужны: playwright (Chromium) и ffmpeg (берётся из imageio-ffmpeg или PATH).
const path = require('path');
const fs = require('fs');
const { spawn, execSync } = require('child_process');
const { chromium } = require('playwright');

const FPS = 30;
const DIR = __dirname;

function ffmpegBin() {
  if (process.env.FFMPEG) return process.env.FFMPEG;
  try {
    return execSync('python3 -c "import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())"').toString().trim();
  } catch { return 'ffmpeg'; }
}

(async () => {
  const args = process.argv.slice(2);
  const stillsArg = args.includes('--stills') ? args[args.indexOf('--stills') + 1] : null;
  const exe = fs.existsSync('/opt/pw-browsers/chromium') ? undefined : undefined;
  const browser = await chromium.launch(exe ? { executablePath: exe } : {});
  const page = await browser.newPage({ viewport: { width: 540, height: 960 }, deviceScaleFactor: 2 });
  await page.goto('file://' + path.join(DIR, 'index.html') + '?render');
  await page.evaluate(() => document.fonts.ready);
  const duration = await page.evaluate(() => window.DURATION);
  const stage = await page.$('#stage');

  if (stillsArg) {
    const out = path.join(DIR, 'stills');
    fs.mkdirSync(out, { recursive: true });
    for (const t of stillsArg.split(',').map(Number)) {
      await page.evaluate(t => window.renderFrame(t), t);
      await stage.screenshot({ path: path.join(out, `t${t.toFixed(2)}.png`) });
    }
    await browser.close();
    return;
  }

  const audio = path.join(DIR, 'soundtrack.wav');
  const outFile = path.join(DIR, 'jarvis_promo.mp4');
  const ff = spawn(ffmpegBin(), [
    '-y', '-loglevel', 'error',
    '-f', 'image2pipe', '-framerate', String(FPS), '-i', '-',
    ...(fs.existsSync(audio) ? ['-i', audio, '-c:a', 'aac', '-b:a', '192k', '-shortest'] : []),
    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '18', '-preset', 'medium',
    '-movflags', '+faststart', outFile,
  ], { stdio: ['pipe', 'inherit', 'inherit'] });

  const frames = Math.round(duration * FPS);
  for (let i = 0; i < frames; i++) {
    await page.evaluate(t => window.renderFrame(t), i / FPS);
    const buf = await stage.screenshot({ type: 'jpeg', quality: 95 });
    if (!ff.stdin.write(buf)) await new Promise(r => ff.stdin.once('drain', r));
    if (i % 60 === 0) process.stdout.write(`frame ${i}/${frames}\n`);
  }
  ff.stdin.end();
  await new Promise(r => ff.on('close', r));
  await browser.close();
  console.log('done →', outFile);
})();
