#!/usr/bin/env python3
"""Find a trending gaming topic and turn it into a fast, vertical news Short.

Per video:
  1. Fresh headlines per topic from Google News RSS (free, no key); least-recent topic wins.
  2. Gemini (free key) writes a punchy hook-first script from those headlines only.
  3. Visuals: real stock footage per scene from Pixabay or Pexels (free key), cropped to vertical.
     Fallback: an animated gradient.
  4. Voice: Gemini speech (most natural free voice), else Microsoft neural voice, else gTTS.
  5. Retention tricks: hook first, one short sentence per scene (a cut every ~4s), big
     word-by-word captions, fades. Thumbnail from the first scene.
  6. mp4 + thumbnail + sidecar .json go to queue/ for upload.py.

Usage:
  python generate.py                 # make 1 video
  python generate.py --count 2
  python generate.py --demo          # offline test: fake headlines, test footage, silent audio
"""
import argparse
import asyncio
import os
import time
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


UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36",
      "Accept": "application/rss+xml, application/xml, text/xml, */*"}
SITE_FEEDS = ["https://www.gamespot.com/feeds/news/", "https://www.pcgamer.com/rss/"]


def http_get(url, tries=3):
    last = None
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as resp:
                return resp.read()
        except Exception as err:
            last = err
            time.sleep(4 * (attempt + 1))
    raise last


def parse_feed(xml_bytes):
    """Namespace-agnostic RSS/Atom parser -> list of headline dicts, newest first."""
    root = ET.fromstring(xml_bytes)
    items, seen = [], set()

    def child(el, name):
        for c in el:
            if c.tag.split("}")[-1].lower() == name:
                return c
        return None

    for el in root.iter():
        if el.tag.split("}")[-1] not in ("item", "entry"):
            continue
        node = child(el, "title")
        raw = ((node.text if node is not None else "") or "").strip()
        title, sep, source = raw.rpartition(" - ")
        if not sep:
            title, source = raw, ""
        src = child(el, "source")
        if src is not None and (src.text or "").strip():
            source = src.text.strip()
        k = key(title)
        if not title or k in seen:
            continue
        seen.add(k)
        link = child(el, "link")
        link = ((link.text or link.get("href") or "") if link is not None else "").strip()
        when = next((n for n in (child(el, "pubdate"), child(el, "published"), child(el, "updated"))
                     if n is not None), None)
        try:
            published = parsedate_to_datetime(when.text) if when is not None else None
        except Exception:
            published = None
        if published is None:
            try:
                published = datetime.fromisoformat(when.text.replace("Z", "+00:00"))
            except Exception:
                published = datetime.now(timezone.utc)
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        items.append({"title": title, "source": source or "news", "link": link,
                      "published": published, "key": k})
    items.sort(key=lambda i: i["published"], reverse=True)
    return items


def fetch_headlines(topic, days=2):
    """Google News, then Bing News, then gaming-site feeds filtered by topic keywords."""
    query = topic["query"]
    keywords = [k.lower() for k in topic.get("keywords", [topic["name"]])]
    sources = [
        ("Google News", "https://news.google.com/rss/search?" + urllib.parse.urlencode(
            {"q": f"{query} when:{days}d", "hl": "en-US", "gl": "US", "ceid": "US:en"}), False),
        ("Bing News", "https://www.bing.com/news/search?" + urllib.parse.urlencode(
            {"q": query.replace('"', ""), "format": "rss"}), False),
    ] + [(url.split("/")[2], url, True) for url in SITE_FEEDS]
    found, errors = [], []
    for label, url, filter_by_keyword in sources:
        try:
            items = parse_feed(http_get(url, tries=2 if label == "Google News" else 1))
        except Exception as err:
            errors.append(f"{label}: {err}")
            continue
        if filter_by_keyword:
            items = [i for i in items if any(k in i["title"].lower() for k in keywords)]
        found += items
        if len(found) >= 8:
            break
    if not found and errors:
        raise RuntimeError("; ".join(errors))
    seen, unique = set(), []
    for i in sorted(found, key=lambda i: i["published"], reverse=True):
        if i["key"] not in seen:
            seen.add(i["key"])
            unique.append(i)
    return unique


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
            items = demo_headlines(topic["name"]) if demo else fetch_headlines(topic)
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
        raise RuntimeError(f"{cmd[0]} failed: {result.stderr[-500:]}")


def silent_audio(text, out):
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
         "-t", f"{max(3, len(text.split()) * 0.4):.1f}", str(out)])


def edge_speech(text, out, config):
    import edge_tts
    voice = config.get("edgeVoice", "en-US-AndrewMultilingualNeural")
    asyncio.run(edge_tts.Communicate(text, voice, rate="+6%").save(str(out)))
    if Path(out).stat().st_size < 1000:
        raise RuntimeError("empty edge-tts audio")


def gtts_speech(text, out, config):
    from gtts import gTTS
    gTTS(text, lang="en").save(str(out))


_WHISPER = None


def transcribe(path):
    """Word timings of what is actually spoken, via faster-whisper (free, local).
    Returns [(word, start, end), ...] or None if unavailable."""
    global _WHISPER
    try:
        from faster_whisper import WhisperModel
        if _WHISPER is None:
            _WHISPER = WhisperModel("tiny.en", device="cpu", compute_type="int8")
        segments, _ = _WHISPER.transcribe(str(path), language="en", word_timestamps=True, beam_size=1)
        return [(w.word.strip(), w.start, w.end) for seg in segments for w in seg.words]
    except Exception as err:
        print(f"  speech check unavailable: {err}")
        return None


def trim_audio(src, dst):
    """Cut silence from the start and end of a voice clip."""
    cut = "silenceremove=start_periods=1:start_threshold=-45dB"
    run(["ffmpeg", "-y", "-i", str(src), "-af", f"{cut},areverse,{cut},areverse",
         "-ar", "24000", "-ac", "1", str(dst)])


def words_of(text):
    return re.findall(r"[A-Za-z0-9']+", text.lower())


def narrate_all(scenes, tmp, config, demo):
    """One consistent voice for the whole video: Gemini, else Edge, else gTTS.

    Each clip is trimmed and checked with speech recognition: if the voice says clearly more or
    fewer words than the script (e.g. reads an instruction aloud or repeats itself) that engine
    is rejected and the next one is used. Returns [(audio_path, (speech_start, speech_end))].
    """
    if demo:
        outs = []
        for i, s in enumerate(scenes):
            out = tmp / f"{i}.wav"
            silent_audio(s["narration"], out)
            outs.append((out, None))
        return outs
    engines = []
    if os.environ.get("GEMINI_API_KEY") and config.get("useGeminiVoice", True):
        engines.append(("Gemini voice", lambda t, o, c: ai.gemini_speech(t, o, c), "wav"))
    engines += [("Edge neural voice", edge_speech, "mp3"), ("gTTS", gtts_speech, "mp3")]
    for position, (name, fn, ext) in enumerate(engines):
        last_engine = position == len(engines) - 1
        outs = []
        try:
            for i, s in enumerate(scenes):
                raw, clean = tmp / f"{i}_{name[:3]}_raw.{ext}", tmp / f"{i}_{name[:3]}.wav"
                fn(s["narration"], raw, config)
                trim_audio(raw, clean)
                heard = transcribe(clean)
                span = None
                if heard:
                    want, got = len(words_of(s["narration"])), len(heard)
                    print(f"  scene {i + 1}: script {want} words, heard {got}: "
                          f"{' '.join(w for w, _, _ in heard)[:90]}")
                    if not last_engine and not (0.7 <= got / max(want, 1) <= 1.35):
                        raise RuntimeError(f"voice said {got} words but script has {want}")
                    span = (heard[0][1], heard[-1][2])
                outs.append((clean, span))
                if name == "Gemini voice":
                    time.sleep(config.get("geminiVoiceDelay", 6))  # stay inside free-tier rate limits
            print(f"  voice: {name}")
            return outs
        except Exception as err:
            print(f"  {name} rejected: {err}")
    raise RuntimeError("no voice engine worked")


def duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return float(out.stdout.strip())


def caption_filters(text, secs, tmp, idx, span=None):
    """Big word-by-word captions: 2-3 words at a time, timed to the narration length."""
    words = text.replace("\n", " ").split()
    chunks, cur = [], []
    for w in words:
        if cur and (len(" ".join(cur + [w])) > 13 or len(cur) >= 3):
            chunks.append(cur)
            cur = []
        cur.append(w)
        if w.endswith((".", "!", "?")):
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    weights = [sum(len(w) + 2 for w in c) for c in chunks]
    total = sum(weights) or 1
    start, end = span if span else (0.1, max(secs - 0.45, 1))
    speech = max(end - start, 0.5)
    filters, t0 = [], start
    for n, (chunk, wt) in enumerate(zip(chunks, weights)):
        t1 = t0 + speech * wt / total
        f = tmp / f"cap_{idx}_{n}.txt"
        f.write_text(" ".join(chunk).upper(), encoding="utf-8")
        filters.append(
            f"drawtext=fontfile={FONT_BOLD}:textfile={f}:fontsize=66:fontcolor=white:"
            f"borderw=7:bordercolor=black:x=(w-text_w)/2:y=h*0.60:"
            f"enable='between(t,{t0:.2f},{t1:.2f})'")
        t0 = t1
    return filters


def render_clip(source, is_video, text, audio, out, index, tmp, label="", span=None):
    """One scene: footage (or animated gradient) + captions + fades + narration."""
    secs = duration(audio) + 0.45
    frames = int(secs * FPS)
    if is_video:
        base = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                f"fps={FPS},eq=brightness=-0.06:saturation=1.15")
        inputs = ["-stream_loop", "-1", "-i", str(source)]
    else:
        zoom = "min(1+0.0007*on,1.25)" if index % 2 == 0 else "max(1.25-0.0007*on,1.0)"
        base = (f"[0:v]zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                f"d={frames}:s={W}x{H}:fps={FPS}")
        inputs = ["-loop", "1", "-framerate", str(FPS), "-i", str(source)]
    chain = [base, f"drawbox=x=0:y=ih*0.54:w=iw:h=ih*0.20:color=black@0.35:t=fill"]
    if label:
        chain.append(f"drawtext=fontfile={FONT_BOLD}:text='{label}':fontsize=34:fontcolor=#ffd60a:"
                     f"borderw=4:bordercolor=black:x=(w-text_w)/2:y=h*0.08")
    chain += caption_filters(text, secs, tmp, index, span)
    chain += [f"fade=t=in:st=0:d=0.15", f"fade=t=out:st={secs - 0.15:.2f}:d=0.15", "format=yuv420p[v]"]
    graph = ",".join(chain)
    run(["ffmpeg", "-y", *inputs, "-i", str(audio), "-filter_complex", graph,
         "-map", "[v]", "-map", "1:a", "-t", f"{secs:.2f}",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "25", "-r", str(FPS),
         "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
         "-af", "apad=pad_dur=0.5", str(out)])


def concat(clips, out, workdir):
    listing = workdir / "clips.txt"
    listing.write_text("".join(f"file '{c}'\n" for c in clips), encoding="utf-8")
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
         "-c", "copy", "-movflags", "+faststart", str(out)])


def demo_footage(dest, index):
    """Stand-in for stock footage so the video path can be tested offline."""
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc2=size=720x1280:rate=24,hue=h={index * 50}",
         "-t", "8", "-pix_fmt", "yuv420p", str(dest)])


def frame_of(video, dest):
    run(["ffmpeg", "-y", "-ss", "1", "-i", str(video), "-frames:v", "1", str(dest)])
    return Image.open(dest).convert("RGB")


# --------------------------------------------------------------------------- scenes
def plain_scenes(name, headlines):
    scenes = [{"narration": f"{name} news. Here are the top {len(headlines)} stories right now.",
               "search_query": "video game controller"}]
    for i, h in enumerate(headlines, 1):
        scenes.append({"narration": f"Number {i}. {h['title']}.", "search_query": f"{name} gaming"})
    scenes.append({"narration": "Follow for new gaming news every day.", "search_query": "gamer headset"})
    return scenes


def build_video(topic, headlines, config, demo):
    name = topic["name"]
    today = datetime.now(timezone.utc).strftime("%d %b %Y")
    palette = random.choice(PALETTES)
    channel = config.get("channelName", "")

    script = None if demo else ai.write_script(topic, headlines, config)
    scenes = script["scenes"] if script else plain_scenes(name, headlines)
    print("  script: Gemini" if script else "  script: plain headlines (Gemini unavailable)")

    QUEUE.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    out = QUEUE / f"{stamp}-{slug}-news.mp4"
    thumb = out.with_suffix(".jpg")

    footage_count, used = 0, set()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        voices = narrate_all(scenes, tmp, config, demo)
        clips, first_bg = [], None
        for idx, scene in enumerate(scenes):
            src, is_video = tmp / f"{idx}_src.mp4", True
            if demo:
                demo_footage(src, idx)
            elif not ai.stock_clip(scene.get("search_query") or f"{name} gaming", src, used):
                if not ai.stock_clip(config.get("fallbackQuery", "video game neon"), src, used):
                    is_video = False
            if is_video:
                footage_count += 1
            else:
                src = tmp / f"{idx}_bg.png"
                gradient(palette, (SRC_W, SRC_H)).filter(ImageFilter.GaussianBlur(2)).save(src)
            if first_bg is None:
                first_bg = frame_of(src, tmp / "first.png") if is_video else Image.open(src)
            clip = tmp / f"{idx}.mp4"
            label = f"{name} NEWS" if idx == 0 else ""
            audio, span = voices[idx]
            render_clip(src, is_video, scene["narration"], audio, clip, idx, tmp, label, span)
            clips.append(clip)
        concat(clips, out, tmp)

        title = (script or {}).get("title") or f"{name} News: {headlines[0]['title']}"
        title = (title if len(title) <= 88 else title[:85].rstrip() + "...") + " #Shorts"
        make_thumbnail(first_bg, (script or {}).get("title") or f"{name} news", thumb)

    print(f"  {footage_count}/{len(scenes)} scenes with stock footage")
    sources = "\n".join(f"- {h['title']} ({h['source']}): {h['link']}" for h in headlines)
    summary = (script or {}).get("description") or f"{name} news roundup for {today}."
    hashtag = re.sub(r"\W+", "", name)
    description = (f"{summary}\n\nSources:\n{sources}\n\n"
                   "Headlines come from Google News. Footage is free stock video (Pixabay/Pexels) and this video is "
                   "not affiliated with any game publisher.\n\n"
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
