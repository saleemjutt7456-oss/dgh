"""Free AI helpers: script writing (Gemini free tier) and image generation (Pollinations).

Both are optional. Without GEMINI_API_KEY the generator falls back to plain headline
slides; if image generation fails a scene falls back to a gradient background.
"""
import io
import json
import os
import re
import time
import urllib.parse
import urllib.request

from PIL import Image

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def _post_json(url, payload, key, timeout=90):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json", "x-goog-api-key": key})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def write_script(topic, headlines, config):
    """Ask Gemini for a title, description, tags and scenes. Returns dict or None."""
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return None
    facts = "\n".join(f"- {h['title']} ({h['source']})" for h in headlines)
    scenes = config.get("scenes", 5)
    prompt = f"""You write scripts for short vertical YouTube gaming news videos (about 45 seconds).
Topic: {topic['name']}
Use ONLY facts from these headlines. Do not invent details, dates, quotes or numbers.
{facts}

Return JSON with:
  "title": catchy but honest title, max 80 characters, no hashtags
  "description": 2-3 sentence summary
  "tags": 8 short search tags
  "scenes": exactly {scenes} objects, each with
     "caption": on-screen text, max 55 characters
     "narration": 1-2 spoken sentences, energetic, easy to follow
     "image_prompt": a vivid cinematic illustration of the scene, in English; no text, no logos,
                     no real people's faces, no copyrighted characters by name
The first scene is a hook, the last scene asks viewers to follow for daily gaming news."""
    payload = {"contents": [{"parts": [{"text": prompt}]}],
               "generationConfig": {"responseMimeType": "application/json", "temperature": 0.8}}
    for model in config.get("geminiModels", ["gemini-2.5-flash", "gemini-2.0-flash"]):
        try:
            data = _post_json(GEMINI_URL.format(model=model), payload, key)
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            result = json.loads(re.sub(r"^```(?:json)?|```$", "", text.strip()).strip())
            if result.get("scenes") and result.get("title"):
                return result
        except Exception as err:
            print(f"  Gemini ({model}) failed: {err}")
    return None


def make_image(prompt, size, style, seed, retries=3):
    """Download an AI image from Pollinations (free, no key). Returns PIL image or None."""
    full = f"{prompt}, {style}"
    url = ("https://image.pollinations.ai/prompt/" + urllib.parse.quote(full)
           + "?" + urllib.parse.urlencode({"width": size[0], "height": size[1],
                                           "seed": seed, "nologo": "true", "model": "flux"}))
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                img = Image.open(io.BytesIO(resp.read()))
                img.load()
                return img.convert("RGB")
        except Exception as err:
            print(f"  image attempt {attempt + 1} failed: {err}")
            time.sleep(5 * (attempt + 1))
    return None
