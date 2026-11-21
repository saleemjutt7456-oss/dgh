# dgh: automatic gaming news videos for YouTube

Free and fully automatic. Twice a day GitHub Actions runs this pipeline:

1. **Ideas:** `generate.py` reads fresh Google News headlines (no key) and picks the topic
   covered least recently.
2. **Script:** Gemini writes a hook-first script (one short sentence per scene, a cut about
   every 4 seconds) using only facts from the headlines.
3. **Visuals:** real stock footage per scene from Pexels (free key), cropped to vertical.
4. **Voice:** Gemini's free speech voice (the most natural free option), falling back to a
   Microsoft neural voice, then Google TTS. One voice per video.
5. **Retention:** big word-by-word captions, fades, honest curiosity titles, thumbnail.
6. **Upload:** `upload.py` uploads, sets the thumbnail (phone-verified channels only) and
   records it in `uploaded/`. Used headlines go in `data/used.json`.

Schedule: 06:00 and 14:00 UTC (11:00 and 19:00 Pakistan time) = 2 videos a day.
Run it by hand from the **Actions** tab. Edit topics, voice and style in `config.json`.
Your own clips dropped in `queue/` upload too. Offline test: `python generate.py --demo`.

## Free API keys (no card)

Add each as a secret in **Settings > Secrets and variables > Actions**. Never paste keys
into chat, code or issues.

- `GEMINI_API_KEY`: https://aistudio.google.com/apikey (script and natural voice)
- `PEXELS_API_KEY`: https://www.pexels.com/api/ (real stock footage; without it videos fall
  back to plain gradient backgrounds)

Check the first run's log in the Actions tab: it prints which script, voice and footage
source were used.

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
| `ai.py` | Gemini script writer and AI image download |
| `upload.py` | Uploads queued videos |
| `get_token.py` | Run once locally to get the refresh token |
| `config.json` | Default privacy, category, tags, description template |
| `.github/workflows/upload.yml` | The schedule |
| `queue/` | Videos waiting to upload |
| `uploaded/` | Records of finished uploads |
