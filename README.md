# dgh: automatic gaming news videos for YouTube

Free and fully automatic. Twice a day GitHub Actions runs this pipeline:

1. `generate.py` reads fresh headlines from Google News (no API key) for the topics in
   `config.json` (GTA 6, GTA Online, Fortnite, Minecraft, Call of Duty, PS5), picks the
   topic covered least recently, and builds a vertical 1080x1920 Short: title slide,
   one narrated slide per headline, outro. Narration uses free Microsoft Edge voices
   (falls back to Google TTS).
2. `upload.py` uploads it to YouTube with title, tags and a description that lists the
   sources, then records it in `uploaded/`.
3. Used headlines are saved in `data/used.json` so nothing repeats.

Schedule: 06:00 and 14:00 UTC (11:00 and 19:00 Pakistan time) = 2 videos a day.
Run it by hand any time from the **Actions** tab. Edit topics, voice and channel name
in `config.json`. You can also drop your own recorded clips in `queue/`; they upload too.

Test locally without internet: `python generate.py --demo`.

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
- **Quota.** Each upload costs 1,600 of the free 10,000 daily units, so 2 a day is fine.
- **Original value.** The videos are news summaries with source links. YouTube can
  limit channels that post mass-produced, repetitive content, so check the first
  videos, vary the topics, and consider adding your own commentary or gameplay.
- **Gameplay clips** you add yourself should be your own footage; avoid copyrighted music.

## Files

| File | Purpose |
| --- | --- |
| `generate.py` | Finds topics and builds the videos |
| `upload.py` | Uploads queued videos |
| `get_token.py` | Run once locally to get the refresh token |
| `config.json` | Default privacy, category, tags, description template |
| `.github/workflows/upload.yml` | The schedule |
| `queue/` | Videos waiting to upload |
| `uploaded/` | Records of finished uploads |
