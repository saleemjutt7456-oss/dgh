"""Free helpers: Gemini (script + natural voice, free tier) and Pexels (free stock footage).

Everything is optional and degrades gracefully; see generate.py for the fallbacks.
Keys come from environment variables (GitHub secrets): GEMINI_API_KEY, PEXELS_API_KEY.
"""
import base64
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import wave

GEMINI = "https://generativelanguage.googleapis.com/v1beta"


def _request(url, payload=None, headers=None, timeout=90):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET",
                                 headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _gemini(path, payload=None):
    return _request(f"{GEMINI}/{path}", payload, {"x-goog-api-key": os.environ["GEMINI_API_KEY"]})


def list_models(kind, fallback):
    """Ask the API which models this key can use, so we never hard-code a retired name.

    kind: "text" -> flash models for scripts, "tts" -> speech models.
    """
    try:
        names = []
        for m in _gemini("models?pageSize=200").get("models", []):
            name = m["name"].removeprefix("models/")
            if "generateContent" not in m.get("supportedGenerationMethods", []):
                continue
            low = name.lower()
            is_tts = "tts" in low
            if kind == "tts" and not is_tts:
                continue
            if kind == "text" and (is_tts or any(x in low for x in ("image", "live", "audio", "embed", "robotics", "computer"))):
                continue
            if kind == "text" and "flash" not in low:
                continue
            names.append(name)
        # newest first, "lite" variants last (cheaper but weaker)
        names.sort(key=lambda n: ("lite" in n, [-int(x) for x in re.findall(r"\d+", n)]))
        print(f"  Gemini {kind} models available: {', '.join(names[:5]) or 'none'}")
        return names or fallback
    except Exception as err:
        print(f"  could not list Gemini models ({err}); using defaults")
        return fallback


def write_script(topic, headlines, config):
    """Gemini writes title, description, tags and scenes. Returns dict or None."""
    if not os.environ.get("GEMINI_API_KEY"):
        return None
    facts = "\n".join(f"- {h['title']} ({h['source']})" for h in headlines)
    scenes = config.get("scenes", 6)
    prompt = f"""You write scripts for viral vertical YouTube Shorts about gaming news (35-45 seconds).
Topic: {topic['name']}
Use ONLY facts from these headlines. Never invent details, dates, quotes or numbers.
{facts}

Style: fast, punchy, conversational, like a popular gaming YouTuber talking to a friend.
Scene 1 is a HOOK: a surprising, curious first sentence that makes people stay.
The last scene gives a one-line takeaway and asks viewers to follow for daily gaming news.

Return JSON with:
  "title": curiosity-driven but honest title, max 80 characters, no hashtags, no ALL CAPS
  "description": 2-3 sentence summary
  "tags": 8 short search tags
  "scenes": exactly {scenes} objects, each with
     "narration": ONE short spoken sentence, max 16 words, natural spoken English
     "search_query": 2-3 English words to find matching stock footage
                     (e.g. "neon city night", "gamer headset", "sports car", "video game controller")
     "image_prompt": one-line cinematic illustration idea (backup visual)"""
    payload = {"contents": [{"parts": [{"text": prompt}]}],
               "generationConfig": {"responseMimeType": "application/json", "temperature": 0.9}}
    for model in list_models("text", config.get("geminiModels", [])):
        try:
            data = _gemini(f"models/{model}:generateContent", payload)
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            result = json.loads(re.sub(r"^```(?:json)?|```$", "", text.strip()).strip())
            if result.get("scenes") and result.get("title"):
                print(f"  script by {model}")
                return result
        except Exception as err:
            print(f"  Gemini ({model}) failed: {err}")
    return None


def gemini_speech(text, out_path, config):
    """Natural-sounding speech from Gemini's free TTS. Writes a wav file; raises on failure."""
    voice = config.get("geminiVoice", "Puck")
    styled = ("Say this like an energetic, friendly gaming YouTuber, natural pacing, "
              "with real emotion and small pauses: " + text)
    payload = {"contents": [{"parts": [{"text": styled}]}],
               "generationConfig": {"responseModalities": ["AUDIO"],
                                    "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}}}
    last = None
    for model in list_models("tts", config.get("geminiTtsModels", [])):
        for attempt in range(3):
            try:
                data = _gemini(f"models/{model}:generateContent", payload)
                part = data["candidates"][0]["content"]["parts"][0]["inlineData"]
                rate = int((re.search(r"rate=(\d+)", part.get("mimeType", "")) or [0, 24000])[1])
                with wave.open(str(out_path), "wb") as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(rate)
                    w.writeframes(base64.b64decode(part["data"]))
                return
            except urllib.error.HTTPError as err:
                last = err
                if err.code == 429 and attempt < 2:
                    time.sleep(25)
                    continue
                break
            except Exception as err:
                last = err
                break
    raise RuntimeError(f"Gemini speech failed: {last}")


def pexels_clip(query, dest, used, min_h=1280):
    """Download a vertical stock video matching query. Returns path or None."""
    key = os.environ.get("PEXELS_API_KEY")
    if not key:
        return None
    try:
        url = "https://api.pexels.com/videos/search?" + urllib.parse.urlencode(
            {"query": query, "orientation": "portrait", "size": "medium", "per_page": 20})
        videos = _request(url, headers={"Authorization": key, "User-Agent": "Mozilla/5.0"}).get("videos", [])
        for video in videos:
            if video["id"] in used or video.get("duration", 0) < 4:
                continue
            files = [f for f in video["video_files"]
                     if f.get("file_type") == "video/mp4" and f.get("height", 0) >= min_h and f.get("width", 0) <= 1100]
            if not files:
                continue
            best = min(files, key=lambda f: f["height"])  # smallest file that is still sharp enough
            req = urllib.request.Request(best["link"], headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as fh:
                fh.write(resp.read())
            used.add(video["id"])
            return dest
    except Exception as err:
        print(f"  Pexels '{query}' failed: {err}")
    return None
