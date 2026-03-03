# TikTok Drive Uploader

Download TikTok videos from a CSV file, rename them with the creator's name, and upload directly to Google Drive.

## What It Does

1. Reads TikTok URLs from a CSV file
2. Downloads each video using `yt-dlp`
3. Renames to `TTS---CreatorName_YYYYMMDD_HHMMSS.mp4` format
4. Uploads to a specified Google Drive folder
5. Generates a CSV log with results and Drive links

## Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Google Drive API Credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project (or select existing)
3. Enable the **Google Drive API** (APIs & Services > Library)
4. Create OAuth 2.0 credentials:
   - APIs & Services > Credentials > Create Credentials > OAuth client ID
   - Application type: **Desktop app**
   - Download the JSON file
   - Rename to `credentials.json` and place in this directory

### 3. Get Your Drive Folder ID

Open your target folder in Google Drive. The folder ID is in the URL:
```
https://drive.google.com/drive/folders/YOUR_FOLDER_ID_HERE
```

### 4. Prepare Your CSV

Create a CSV with TikTok URLs. The script auto-detects columns named `url`, `link`, or `tiktok`:

```csv
url,notes
https://www.tiktok.com/@creator/video/123456,First video
https://www.tiktok.com/@another/video/789012,Second video
```

See `tiktok_links_template.csv` for a template.

## Usage

```bash
python tiktok_drive_uploader.py your_links.csv YOUR_DRIVE_FOLDER_ID
```

### Options

```bash
# Specify the URL column name
python tiktok_drive_uploader.py links.csv FOLDER_ID --url-column "tiktok_url"

# Custom download directory
python tiktok_drive_uploader.py links.csv FOLDER_ID --download-dir ./tmp

# Custom filename prefix (default: TTS)
python tiktok_drive_uploader.py links.csv FOLDER_ID --prefix "CONTENT"

# Custom credentials path
python tiktok_drive_uploader.py links.csv FOLDER_ID --credentials ~/creds.json
```

## Output

- Videos uploaded as: `TTS---CreatorName_YYYYMMDD_HHMMSS.mp4`
- Log file generated: `upload_log_YYYYMMDD_HHMMSS.csv` with URLs, filenames, Drive IDs, and links

## Troubleshooting

**"credentials.json not found"** - Complete the Google Drive API setup (step 2 above).

**"Download failed"** - Ensure `yt-dlp` is installed (`pip install yt-dlp`). Some videos may be private or region-locked.

**"Upload failed"** - Verify your folder ID is correct and you have write access.

## License

MIT
