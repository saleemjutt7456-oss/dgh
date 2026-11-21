#!/usr/bin/env python3
"""Find a trending gaming topic and turn it into an animated vertical news video (Short).

Per video:
  1. Pull fresh headlines per topic from Google News RSS (free, no key) and pick the
     topic covered least recently.
  2. Script: Gemini (free tier, GEMINI_API_KEY) writes title, description, tags and
     scenes from those headlines only. Without a key, plain headline scenes are used.
  3. Visuals: one AI illustration per scene (Pollinations, free, no key), animated with a
     slow zoom/pan and a fading caption. Falls back to a gradient if an image fails.
  4. Narration (edge-tts, falling back to gTTS), stitched with ffmpeg.
  5. A thumbnail is saved and the mp4 + sidecar .json go to queue/ for upload.py.

Usage:
  python generate.py                 # make 1 video
  python generate.py --count 2
  python generate.py --demo          # offline test: fake headlines, silent audio, no AI
"""
import argparse
import asyncio
import hashlib
import json
import random
import re
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

import ai

ROOT = Path(__file__).resolve().parent
QUEUE = ROOT / "queue"
STATE = ROOT / "data" / "used.json"
W, H = 720, 1280          # output size (vertical)
SRC_W, SRC_H = 1080, 1920  # source image size, larger than output so zooming stays sharp
FPS = 24
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
PALETTES = [
    ((12, 18, 48), (88, 28, 135)),
    ((8, 40, 60), (14, 116, 144)),
    ((40, 10, 20), (190, 40, 60)),
    ((10, 40, 25), (22, 120, 70)),
    ((30, 20, 5), (200, 120, 20)),
]
STYLE_DEFAULT = ("cinematic digital illustration, dramatic lighting, vibrant colors, "
                 "detailed, vertical composition, no text, no watermark")


# --------------------------------------------------------------------------- data
def load_config():
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"used": [], "topics": {}}


def save_state(state):
    STATE.parent.mkdir(exist_ok=True)
    state["used"] = state["used"][-2000:]
    STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def key(title):
    return hashlib.sha1(re.sub(r"\W+", "", title.lower()).encode()).hexdigest()[:16]


def fetch_headlines(query, days=2):
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": f"{query} when:{days}d", "hl": "en-US", "gl": "US", "ceid": "US:en"})
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        root = ET.fromstring(resp.read())
    items, seen = [], set()
    for item in root.iter("item"):
        raw = (item.findtext("title") or "").strip()
        title, _, source = raw.rpartition(" - ")
        title = title or raw
        k = key(title)
        if not title or k in seen:
            continue
        seen.add(k)
        try:
            published = parsedate_to_datetime(item.findtext("pubDate"))
        except Exception:
            published = datetime.now(timezone.utc)
        items.append({"title": title, "source": source or "news", "link": item.findtext("link") or "",
                      "published": published, "key": k})
    items.sort(key=lambda i: i["published"], reverse=True)
    return items


def demo_headlines(name):
    now = datetime.now(timezone.utc)
    return [{"title": f"{name}: demo headline number {n}", "source": "Demo News",
             "link": "https://example.com", "published": now,
             "key": key(f"demo {name} {n} {random.random()}")} for n in range(1, 5)]


def pick_topic(config, state, demo):
    last = state["topics"]
    order = sorted(config["topics"], key=lambda t: last.get(t["name"], ""))  # least recent first
    used = set(state["used"])
    need = config.get("headlinesPerVideo", 4)
    for topic in order:
        try:
            items = demo_headlines(topic["name"]) if demo else fetch_headlines(topic["query"])
        except Exception as err:
            print(f"  could not fetch {topic['name']}: {err}")
            continue
        fresh = [i for i in items if i["key"] not in used][:need]
        if len(fresh) >= 3:
            return topic, fresh
        print(f"  {topic['name']}: only {len(fresh)} fresh headlines, skipping")
    return None, []


# --------------------------------------------------------------------------- images
def font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default(size)


def gradient(palette, size):
    top, bottom = palette
    w, h = size
    img = Image.new("RGB", (1, h))
    px = img.load()
    for y in range(h):
        t = y / (h - 1)
        px[0, y] = tuple(int(top[c] + (bottom[c] - top[c]) * t) for c in range(3))
    return img.resize((w, h))


def cover(img, size):
    """Scale and crop to fill size exactly."""
    w, h = size
    scale = max(w / img.width, h / img.height)
    img = img.resize((int(img.width * scale) + 1, int(img.height * scale) + 1), Image.LANCZOS)
    left, top = (img.width - w) // 2, (img.height - h) // 2
    return img.crop((left, top, left + w, top + h))


def wrap(draw, text, fnt, max_width):
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=fnt) <= max_width:
            line = trial
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def caption_layer(text, footer=""):
    """Transparent overlay (output size) with a caption band near the lower third."""
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    size = 54
    fnt = font(FONT_BOLD, size)
    lines = wrap(d, text, fnt, W - 100)
    while len(lines) > 5 and size > 34:
        size -= 4
        fnt = font(FONT_BOLD, size)
        lines = wrap(d, text, fnt, W - 100)
    line_h = int(size * 1.3)
    block = len(lines) * line_h + 60
    y0 = int(H * 0.66)
    d.rounded_rectangle((30, y0, W - 30, y0 + block), radius=28, fill=(0, 0, 0, 165))
    y = y0 + 30 + line_h // 2
    for line in lines:
        d.text((W // 2, y), line, font=fnt, fill="white", anchor="mm",
               stroke_width=2, stroke_fill=(0, 0, 0, 255))
        y += line_h
    if footer:
        d.text((W // 2, H - 90), footer, font=font(FONT_REG, 30), fill=(235, 235, 245, 230), anchor="mm")
    return layer


def make_thumbnail(bg, title, path):
    img = cover(bg, (1280, 720)).convert("RGB")
    d = ImageDraw.Draw(img, "RGBA")
    d.rectangle((0, 0, 1280, 720), fill=(0, 0, 0, 80))
    size = 88
    fnt = font(FONT_BOLD, size)
    lines = wrap(d, title.upper(), fnt, 1120)
    while len(lines) > 3 and size > 50:
        size -= 6
        fnt = font(FONT_BOLD, size)
        lines = wrap(d, title.upper(), fnt, 1120)
    y = 360 - (len(lines) * int(size * 1.2)) // 2
    for line in lines:
        d.text((640, y), line, font=fnt, fill=(255, 214, 10), anchor="mm",
               stroke_width=6, stroke_fill=(0, 0, 0))
        y += int(size * 1.2)
    img.save(path, quality=88)


# --------------------------------------------------------------------------- audio / video
def run(cmd):
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if result.returncode:
        raise RuntimeError(f"{cmd[0]} failed: {result.stderr[-400:]}")


def narrate(text, out, voice, demo):
    if demo:
        run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
             "-t", f"{max(3, len(text.split()) * 0.4):.1f}", "-c:a", "libmp3lame", str(out)])
        return
    try:
        import edge_tts
        asyncio.run(edge_tts.Communicate(text, voice).save(str(out)))
        if Path(out).stat().st_size > 1000:
            return
    except Exception as err:
        print(f"  edge-tts failed ({err}), falling back to gTTS")
    from gtts import gTTS
    gTTS(text, lang="en").save(str(out))


def duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return float(out.stdout.strip())


def render_clip(bg_png, cap_png, mp3, out, index):
    """Animate the still: slow zoom (in on even scenes, out on odd) plus fading caption."""
    secs = duration(mp3) + 0.5
    frames = int(secs * FPS)
    if index % 2 == 0:
        zoom = f"min(1+0.0007*on,1.25)"
    else:
        zoom = f"max(1.25-0.0007*on,1.0)"
    pan = ["iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"]
    graph = (
        f"[0:v]zoompan=z='{zoom}':x='{pan[0]}':y='{pan[1]}':d={frames}:s={W}x{H}:fps={FPS},"
        f"format=yuv420p[bg];"
        f"[1:v]format=rgba,fade=t=in:st=0.3:d=0.6:alpha=1[cap];"
        f"[bg][cap]overlay=0:0:format=auto,fade=t=in:st=0:d=0.25,"
        f"fade=t=out:st={secs - 0.25:.2f}:d=0.25,format=yuv420p[v]"
    )
    run(["ffmpeg", "-y", "-loop", "1", "-framerate", str(FPS), "-t", f"{secs:.2f}", "-i", str(bg_png),
         "-loop", "1", "-framerate", str(FPS), "-t", f"{secs:.2f}", "-i", str(cap_png),
         "-i", str(mp3), "-filter_complex", graph, "-map", "[v]", "-map", "2:a",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-r", str(FPS),
         "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
         "-af", "apad=pad_dur=0.5", "-t", f"{secs:.2f}", str(out)])


def concat(clips, out, workdir):
    listing = workdir / "clips.txt"
    listing.write_text("".join(f"file '{c}'\n" for c in clips), encoding="utf-8")
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
         "-c", "copy", "-movflags", "+faststart", str(out)])


# --------------------------------------------------------------------------- scenes
def plain_scenes(name, headlines, today, channel):
    n = len(headlines)
    scenes = [{"caption": f"{name} news: top {n} updates",
               "narration": f"{name} news. Top {n} updates right now.",
               "image_prompt": f"{name} video game world, epic wide shot"}]
    for i, h in enumerate(headlines, 1):
        scenes.append({"caption": h["title"],
                       "narration": f"Number {i}. {h['title']}. Reported by {h['source']}.",
                       "image_prompt": f"{name} video game scene inspired by: {h['title']}"})
    scenes.append({"caption": "Follow for daily gaming news",
                   "narration": "That is all for now. Follow for new gaming news every day.",
                   "image_prompt": f"{name} gamer silhouette watching a glowing screen"})
    return scenes


def build_video(topic, headlines, config, demo):
    name = topic["name"]
    today = datetime.now(timezone.utc).strftime("%d %b %Y")
    palette = random.choice(PALETTES)
    voice = config.get("voice", "en-US-GuyNeural")
    style = config.get("imageStyle", STYLE_DEFAULT)
    channel = config.get("channelName", "")

    script = None if demo else ai.write_script(topic, headlines, config)
    if script:
        print("  script written by Gemini")
        scenes = script["scenes"]
    else:
        print("  using plain headline scenes (no AI script)")
        scenes = plain_scenes(name, headlines, today, channel)

    QUEUE.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    out = QUEUE / f"{stamp}-{slug}-news.mp4"
    thumb = out.with_suffix(".jpg")

    ai_images = 0
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        clips, first_bg = [], None
        for idx, scene in enumerate(scenes):
            bg_png, cap_png, mp3, clip = (tmp / f"{idx}_bg.png", tmp / f"{idx}_cap.png",
                                          tmp / f"{idx}.mp3", tmp / f"{idx}.mp4")
            img = None
            if not demo:
                img = ai.make_image(scene.get("image_prompt") or f"{name} video game", (SRC_W, SRC_H),
                                    style, seed=random.randint(1, 10**6))
            if img:
                ai_images += 1
                img = cover(img, (SRC_W, SRC_H))
            else:
                img = gradient(palette, (SRC_W, SRC_H)).filter(ImageFilter.GaussianBlur(2))
            img.save(bg_png)
            if first_bg is None:
                first_bg = img
            caption_layer(scene["caption"][:110], channel if idx == len(scenes) - 1 else "").save(cap_png)
            narrate(scene["narration"], mp3, voice, demo)
            render_clip(bg_png, cap_png, mp3, clip, idx)
            clips.append(clip)
        concat(clips, out, tmp)

        title = (script or {}).get("title") or f"{name} News: {headlines[0]['title']}"
        title = (title if len(title) <= 88 else title[:85].rstrip() + "...") + " #Shorts"
        make_thumbnail(first_bg, (script or {}).get("title") or f"{name} news", thumb)

    print(f"  {ai_images}/{len(scenes)} AI images")
    sources = "\n".join(f"- {h['title']} ({h['source']}): {h['link']}" for h in headlines)
    summary = (script or {}).get("description") or f"{name} news roundup for {today}."
    hashtag = re.sub(r"\W+", "", name)
    description = (f"{summary}\n\nSources:\n{sources}\n\n"
                   "Headlines come from Google News. Visuals are AI-generated illustrations and "
                   "this video is not affiliated with any game publisher.\n\n"
                   f"#{hashtag} #gamingnews #shorts")
    tags = list(dict.fromkeys((script or {}).get("tags", []) + topic.get("tags", [])
                              + [name, "gaming news", "shorts", "gaming"]))
    out.with_suffix(".json").write_text(json.dumps(
        {"title": title, "description": description, "tags": tags, "thumbnail": thumb.name},
        indent=2), encoding="utf-8")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--demo", action="store_true", help="offline test with fake data")
    args = ap.parse_args()

    config = load_config()
    state = load_state()
    made = 0
    for _ in range(args.count):
        topic, headlines = pick_topic(config, state, args.demo)
        if not topic:
            print("No topic with enough fresh headlines right now.")
            break
        print(f"Topic: {topic['name']} ({len(headlines)} headlines)")
        out = build_video(topic, headlines, config, args.demo)
        print(f"  created {out.name}")
        state["topics"][topic["name"]] = datetime.now(timezone.utc).isoformat()
        if not args.demo:
            state["used"] += [h["key"] for h in headlines]
            save_state(state)
        made += 1
    print(f"Created {made} video(s).")
    return 0 if made else 1


if __name__ == "__main__":
    sys.exit(main())
