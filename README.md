# dgh: YouTube gaming upload automation

Free, hands-off uploader for a gaming channel (GTA and other games).
GitHub Actions runs `upload.py` on a schedule, uploads the next video in `queue/`
to YouTube, then records it in `uploaded/`. No paid services are involved.

## How it works

1. Put a video in `queue/` (for example `gta5_police_chase.mp4`).
2. Optionally add `gta5_police_chase.json` next to it with a title, description
   and tags (see `queue/example.json.sample`). Without it, the title comes from
   the file name and the description from `config.json`.
3. Every day at 12:00 UTC the workflow uploads one video. You can also run it by
   hand from the **Actions** tab, choosing how many videos to upload or a dry run.
4. After a successful upload the video is removed from the queue and a record
   with the YouTube link is saved in `uploaded/`.

## One-time setup (free, about 10 minutes)

YouTube does not allow uploads without OAuth credentials, so there is one setup
step. It costs nothing and needs no billing details.

1. Go to https://console.cloud.google.com and create a project.
2. **APIs & Services > Library**: enable **YouTube Data API v3**.
3. **OAuth consent screen**: choose External, fill in the app name and your email,
   add your own Google account as a test user, then **publish the app**. Leaving
   it in Testing mode makes the token expire after 7 days.
4. **Credentials > Create credentials > OAuth client ID > Desktop app**. Download
   the JSON and save it as `client_secret.json` next to `get_token.py` (it is
   git-ignored, never commit it).
5. On your computer run:
   ```bash
   pip install google-auth-oauthlib
   python get_token.py
   ```
   Sign in with the Google account that owns the channel.
6. In the GitHub repo open **Settings > Secrets and variables > Actions** and add
   the three values printed by the script: `YT_CLIENT_ID`, `YT_CLIENT_SECRET`,
   `YT_REFRESH_TOKEN`.

## Things to know

- **Private by default.** YouTube forces videos uploaded through an unaudited API
  project to be private. Uploads still work; you switch each video to Public in
  YouTube Studio, or apply for a YouTube API compliance audit to lift this.
  `config.json` keeps `privacyStatus` at `private` for that reason.
- **Quota.** The free quota allows about 6 uploads a day. Keep `MAX_UPLOADS` low.
- **File size.** GitHub rejects single files over 100 MB, so this setup suits
  short clips and Shorts. Longer videos need a different source for the files.
- **Your own content only.** Upload footage you recorded yourself. Check
  Rockstar's and each publisher's gameplay-video policy, and avoid copyrighted
  music, because Content ID claims can block or demonetise videos.

## Files

| File | Purpose |
| --- | --- |
| `upload.py` | Uploads queued videos |
| `get_token.py` | Run once locally to get the refresh token |
| `config.json` | Default privacy, category, tags, description template |
| `.github/workflows/upload.yml` | The schedule |
| `queue/` | Videos waiting to upload |
| `uploaded/` | Records of finished uploads |
