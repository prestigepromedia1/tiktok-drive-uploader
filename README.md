# Social Video Archiver

Batch download, rename, and optionally upload social media videos from any platform.

Works with **TikTok, Instagram Reels, YouTube Shorts, Twitter/X, Facebook, Reddit, Vimeo, Twitch**, and every other site supported by [yt-dlp](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md).

## Quick Start

```bash
pip install yt-dlp
```

Create a text file with one URL per line (or use a CSV), then run:

```bash
python social_video_archiver.py urls.txt
```

Videos are downloaded, renamed, and saved to `./archived/`.

## Usage

### Local only (default)

```bash
python social_video_archiver.py urls.txt
```

### Local with custom output directory and naming template

```bash
python social_video_archiver.py urls.txt --output-dir ./videos --template "{platform}_{creator}_{date}"
```

### Upload to Google Drive

```bash
python social_video_archiver.py urls.csv --drive-folder YOUR_FOLDER_ID
```

### Upload to Drive with custom credentials path

```bash
python social_video_archiver.py urls.csv --drive-folder YOUR_FOLDER_ID --credentials creds.json
```

## Input Formats

**Plain text** -- one URL per line:

```
https://www.tiktok.com/@creator/video/7123456789
https://www.instagram.com/reel/ABC123/
https://youtube.com/shorts/dQw4w9WgXcQ
```

**CSV** -- the script auto-detects columns named `url`, `link`, or `video`:

```csv
url,notes
https://www.tiktok.com/@creator/video/7123456789,save this one
https://x.com/user/status/123456789,important thread
```

## Naming Templates

Control how downloaded files are named with `--template`. Available tokens:

| Token        | Description                         | Example          |
|--------------|-------------------------------------|------------------|
| `{creator}`  | Username / uploader                 | `dancequeen`     |
| `{platform}` | Detected platform                   | `tiktok`         |
| `{date}`     | Download date (YYYYMMDD)            | `20260303`       |
| `{id}`       | Video ID from the platform          | `7123456789`     |
| `{title}`    | Video title (from yt-dlp metadata)  | `my_cool_video`  |

**Default template:** `{creator}_{platform}_{date}_{id}`

Examples:

```bash
--template "{platform}_{creator}_{date}"        # tiktok_dancequeen_20260303.mp4
--template "{creator}---{id}"                    # dancequeen---7123456789.mp4
--template "{date}_{platform}_{title}"           # 20260303_instagram_my_cool_video.mp4
```

## Google Drive Setup (optional)

Only required if you use `--drive-folder`.

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project (or select an existing one)
3. Enable the **Google Drive API** (APIs & Services > Library)
4. Create OAuth 2.0 credentials:
   - APIs & Services > Credentials > Create Credentials > OAuth client ID
   - Application type: **Desktop app**
   - Download the JSON file and save as `credentials.json` in this directory
5. Install the Drive dependencies:
   ```bash
   pip install google-auth google-auth-oauthlib google-api-python-client
   ```
6. Get your target folder ID from the Drive URL:
   ```
   https://drive.google.com/drive/folders/YOUR_FOLDER_ID_HERE
   ```

## Output

- Videos saved to `./archived/` (or your `--output-dir`)
- A CSV log is generated in the output directory with columns: `url`, `creator`, `platform`, `filename`, `status`, `drive_id`, `link`, `error`

## CLI Reference

```
usage: social_video_archiver.py [-h] [--drive-folder FOLDER_ID]
                                [--output-dir DIR] [--template TEMPLATE]
                                [--credentials FILE] [--download-dir DIR]
                                input_file

positional arguments:
  input_file            Text file (one URL per line) or CSV with URLs

optional arguments:
  --drive-folder ID     Google Drive folder ID (omit for local-only mode)
  --output-dir DIR      Local output directory (default: ./archived)
  --template TPL        Naming template (default: {creator}_{platform}_{date}_{id})
  --credentials FILE    Path to Google OAuth credentials.json
  --download-dir DIR    Temporary download directory (default: <output-dir>/.tmp)
```

## License

MIT
