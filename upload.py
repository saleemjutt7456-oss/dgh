#!/usr/bin/env python3
"""Upload queued videos from queue/ to YouTube.

For every video in queue/ the script looks for an optional sidecar file with the
same name and a .json extension (queue/clip1.mp4 -> queue/clip1.json) holding
title, description, tags, privacyStatus, categoryId. Anything missing is filled
in from config.json and from the file name.

After a successful upload the video file is deleted from the queue and a record
is written to uploaded/<name>.json.

Environment variables:
  YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN   OAuth credentials (required)
  MAX_UPLOADS   videos to upload per run (default 1)
  DRY_RUN       set to 1 to print what would happen without uploading
"""
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
QUEUE = ROOT / "queue"
DONE = ROOT / "uploaded"
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
TOKEN_URI = "https://oauth2.googleapis.com/token"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]  # also covers thumbnails.set
RETRY_STATUS = {500, 502, 503, 504}


def load_config():
    path = ROOT / "config.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def title_from_name(stem):
    text = re.sub(r"[_\-]+", " ", stem).strip()
    return text[:1].upper() + text[1:] if text else "Gameplay"


def build_metadata(video, config):
    sidecar = video.with_suffix(".json")
    custom = json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.exists() else {}

    title = (custom.get("title") or title_from_name(video.stem))[:100]
    tags = list(dict.fromkeys(custom.get("tags", []) + config.get("defaultTags", [])))
    hashtags = " ".join("#" + re.sub(r"\W+", "", t) for t in tags[:3] if t)

    template = config.get("descriptionTemplate", "{title}\n\n{hashtags}\n")
    description = custom.get("description") or template.format(title=title, hashtags=hashtags)

    # YouTube limits: 500 characters of tags in total, 5000 in the description.
    kept, total = [], 0
    for tag in tags:
        if total + len(tag) + 1 > 500:
            break
        kept.append(tag)
        total += len(tag) + 1

    return {
        "title": title,
        "description": description[:5000],
        "tags": kept,
        "categoryId": str(custom.get("categoryId", config.get("categoryId", "20"))),
        "privacyStatus": custom.get("privacyStatus", config.get("privacyStatus", "private")),
        "madeForKids": bool(custom.get("madeForKids", config.get("madeForKids", False))),
    }


def get_service():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    missing = [k for k in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN") if not os.environ.get(k)]
    if missing:
        sys.exit("Missing environment variables: " + ", ".join(missing))

    creds = Credentials(
        token=None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"],
        token_uri=TOKEN_URI,
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"],
        scopes=SCOPES,
    )
    creds.refresh(Request())
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def upload(service, video, meta):
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    body = {
        "snippet": {
            "title": meta["title"],
            "description": meta["description"],
            "tags": meta["tags"],
            "categoryId": meta["categoryId"],
        },
        "status": {
            "privacyStatus": meta["privacyStatus"],
            "selfDeclaredMadeForKids": meta["madeForKids"],
        },
    }
    media = MediaFileUpload(str(video), chunksize=8 * 1024 * 1024, resumable=True)
    request = service.videos().insert(part="snippet,status", body=body, media_body=media)

    response, attempt = None, 0
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                print(f"  {int(status.progress() * 100)}%")
        except HttpError as err:
            if err.resp.status in RETRY_STATUS and attempt < 8:
                attempt += 1
                wait = min(60, 2 ** attempt + random.random())
                print(f"  server error {err.resp.status}, retry {attempt} in {wait:.0f}s")
                time.sleep(wait)
            else:
                raise
    return response["id"]


def main():
    QUEUE.mkdir(exist_ok=True)
    DONE.mkdir(exist_ok=True)
    config = load_config()
    limit = int(os.environ.get("MAX_UPLOADS", "1"))
    dry = os.environ.get("DRY_RUN") == "1"

    videos = sorted(p for p in QUEUE.iterdir() if p.suffix.lower() in VIDEO_EXT)
    if not videos:
        print("Queue is empty, nothing to upload.")
        return

    service = None if dry else get_service()
    uploaded = 0
    for video in videos[:limit]:
        meta = build_metadata(video, config)
        print(f"{video.name} -> '{meta['title']}' ({meta['privacyStatus']})")
        if dry:
            print(json.dumps(meta, indent=2))
            continue
        try:
            video_id = upload(service, video, meta)
        except Exception as err:  # keep the file in the queue so the next run retries
            print(f"  FAILED: {err}")
            if "quotaExceeded" in str(err):
                break
            continue
        thumb = video.with_suffix(".jpg")
        thumb_set = False
        if thumb.exists():
            try:
                from googleapiclient.http import MediaFileUpload
                service.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(thumb))).execute()
                thumb_set = True
            except Exception as err:  # needs a phone-verified channel; not fatal
                print(f"  thumbnail not set: {err}")
        record = dict(meta, videoId=video_id, url=f"https://youtu.be/{video_id}",
                      thumbnailSet=thumb_set, uploadedAt=datetime.now(timezone.utc).isoformat())
        (DONE / f"{video.stem}.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
        video.unlink()
        video.with_suffix(".json").unlink(missing_ok=True)
        thumb.unlink(missing_ok=True)
        uploaded += 1
        print(f"  done: {record['url']}")
    print(f"Uploaded {uploaded} video(s).")


if __name__ == "__main__":
    main()
