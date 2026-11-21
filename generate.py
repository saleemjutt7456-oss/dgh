#!/usr/bin/env python3
"""Find a trending gaming topic and turn it into a vertical news video (Short).

Pipeline per video:
  1. Pull fresh headlines for each topic in config.json from Google News RSS
     (free, no API key).
  2. Pick the topic that was covered least recently and has enough new headlines.
  3. Render one slide per headline (Pillow), narrate it (edge-tts, falling back
     to gTTS), and stitch everything into an mp4 with ffmpeg.
  4. Write the mp4 and a sidecar .json (title, description, tags) into queue/,
     where upload.py picks it up.

Used headlines are remembered in data/used.json so nothing repeats.

Usage:
  python generate.py                 # make VIDEOS videos (default 1)
  python generate.py --count 2
  python generate.py --demo          # offline test: fake headlines, silent audio
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
import textwrap
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
QUEUE = ROOT / "queue"
STATE = ROOT / "data" / "used.json"
W, H = 1080, 1920
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
PALETTES = [
    ((12, 18, 48), (88, 28, 135)),
    ((8, 40, 60), (14, 116, 144)),
    ((40, 10, 20), (190, 40, 60)),
    ((10, 40, 25), (22, 120, 70)),
    ((30, 20, 5), (200, 120, 20)),
]


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
        link = item.findtext("link") or ""
        k = key(title)
        if not title or k in seen:
            continue
        seen.add(k)
        try:
            published = parsedate_to_datetime(item.findtext("pubDate"))
        except Exception:
            published = datetime.now(timezone.utc)
        items.append({"title": title, "source": source or "news", "link": link,
                      "published": published, "key": k})
    items.sort(key=lambda i: i["published"], reverse=True)
    return items


def demo_headlines(name):
    now = datetime.now(timezone.utc)
    return [{"title": f"{name}: demo headline number {n}", "source": "Demo News",
             "link": "https://example.com", "published": now,
             "key": key(f"demo {name} {n} {random.random()}")} for n in range(1, 5)]


def pick_topic(config, state, demo):
    topics = config["topics"]
    last = state["topics"]
    order = sorted(topics, key=lambda t: last.get(t["name"], ""))  # least recent first
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


# --------------------------------------------------------------------------- slides
def font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default(size)


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


def make_slide(path, palette, kicker, body, footer, body_size=76):
    top, bottom = palette
    img = Image.new("RGB", (W, H))
    px = img.load()
    for y in range(H):
        t = y / (H - 1)
        row = tuple(int(top[c] + (bottom[c] - top[c]) * t) for c in range(3))
        for x in range(W):
            px[x, y] = row
    d = ImageDraw.Draw(img)

    d.text((W // 2, 260), kicker.upper(), font=font(FONT_BOLD, 54), fill=(255, 214, 10), anchor="mm")
    d.rectangle((W // 2 - 90, 320, W // 2 + 90, 328), fill=(255, 214, 10))

    fnt = font(FONT_BOLD, body_size)
    lines = wrap(d, body, fnt, W - 160)
    while len(lines) > 9 and body_size > 44:
        body_size -= 6
        fnt = font(FONT_BOLD, body_size)
        lines = wrap(d, body, fnt, W - 160)
    line_h = int(body_size * 1.3)
    y = H // 2 - (len(lines) * line_h) // 2
    for line in lines:
        d.text((W // 2, y), line, font=fnt, fill="white", anchor="mm")
        y += line_h

    d.text((W // 2, H - 200), footer, font=font(FONT_REG, 44), fill=(210, 210, 230), anchor="mm")
    img.save(path)


# --------------------------------------------------------------------------- audio / video
def run(cmd):
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def narrate(text, out, voice, demo):
    if demo:
        seconds = max(2, len(text.split()) * 0.4)
        run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
             "-t", f"{seconds:.1f}", "-c:a", "libmp3lame", str(out)])
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


def render_clip(png, mp3, out):
    run(["ffmpeg", "-y", "-loop", "1", "-i", str(png), "-i", str(mp3),
         "-c:v", "libx264", "-tune", "stillimage", "-preset", "veryfast", "-crf", "28",
         "-pix_fmt", "yuv420p", "-r", "24", "-vf", f"scale={W}:{H}",
         "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
         "-af", "apad=pad_dur=0.5", "-shortest", str(out)])


def concat(clips, out, workdir):
    listing = workdir / "clips.txt"
    listing.write_text("".join(f"file '{c}'\n" for c in clips), encoding="utf-8")
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
         "-c", "copy", "-movflags", "+faststart", str(out)])


# --------------------------------------------------------------------------- video
def build_video(topic, headlines, config, demo):
    name = topic["name"]
    palette = random.choice(PALETTES)
    voice = config.get("voice", "en-US-GuyNeural")
    today = datetime.now(timezone.utc).strftime("%d %b %Y")
    n = len(headlines)

    slides = [(name, f"{name} news. Top {n} updates right now.",
               f"{name} news. Top {n} updates right now.", today)]
    for i, h in enumerate(headlines, 1):
        spoken = f"Number {i}. {h['title']}. Reported by {h['source']}."
        slides.append((f"{name}  |  {i} of {n}", h["title"], spoken, f"Source: {h['source']}"))
    slides.append(("Follow for more", "New gaming news every day. Sources are in the description.",
                   "That is all for now. Follow for new gaming news every day. Sources are in the description.",
                   config.get("channelName", "")))

    QUEUE.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    out = QUEUE / f"{stamp}-{slug}-news.mp4"

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        clips = []
        for idx, (kicker, body, spoken, footer) in enumerate(slides):
            png, mp3, clip = tmp / f"{idx}.png", tmp / f"{idx}.mp3", tmp / f"{idx}.mp4"
            make_slide(png, palette, kicker, body, footer)
            narrate(spoken, mp3, voice, demo)
            render_clip(png, mp3, clip)
            clips.append(clip)
        concat(clips, out, tmp)

    top = headlines[0]["title"]
    title = f"{name} News: {top}"
    if len(title) > 88:
        title = title[:85].rstrip() + "..."
    title += " #Shorts"
    sources = "\n".join(f"- {h['title']} ({h['source']}): {h['link']}" for h in headlines)
    tags = list(dict.fromkeys(topic.get("tags", []) + [name, "gaming news", "shorts", "gaming"]))
    hashtag = re.sub(r"\W+", "", name)
    description = (f"{name} news roundup for {today}.\n\nSources:\n{sources}\n\n"
                   "Headlines and links come from Google News. This video is an automated summary "
                   "and is not affiliated with any game publisher.\n\n"
                   f"#{hashtag} #gamingnews #shorts")
    out.with_suffix(".json").write_text(json.dumps(
        {"title": title, "description": description, "tags": tags}, indent=2), encoding="utf-8")
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
        if not args.demo:
            state["used"] += [h["key"] for h in headlines]
            state["topics"][topic["name"]] = datetime.now(timezone.utc).isoformat()
            save_state(state)
        else:
            state["topics"][topic["name"]] = datetime.now(timezone.utc).isoformat()
        made += 1
    print(f"Created {made} video(s).")
    return 0 if made else 1


if __name__ == "__main__":
    sys.exit(main())
