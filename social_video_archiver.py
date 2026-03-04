#!/usr/bin/env python3
"""
Social Video Archiver
Batch download, rename, and optionally upload social media videos from any platform.
Supports TikTok, Instagram, YouTube, Twitter/X, Facebook, and any yt-dlp compatible site.
"""

import os
import csv
import re
import subprocess
import sys
import argparse
import mimetypes
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# Platform detection & creator extraction
# ---------------------------------------------------------------------------

PLATFORM_PATTERNS = {
    "tiktok": re.compile(r"tiktok\.com", re.IGNORECASE),
    "instagram": re.compile(r"instagram\.com", re.IGNORECASE),
    "youtube": re.compile(r"(youtube\.com|youtu\.be)", re.IGNORECASE),
    "twitter": re.compile(r"(twitter\.com|x\.com)", re.IGNORECASE),
    "facebook": re.compile(r"(facebook\.com|fb\.watch)", re.IGNORECASE),
    "reddit": re.compile(r"reddit\.com", re.IGNORECASE),
    "vimeo": re.compile(r"vimeo\.com", re.IGNORECASE),
    "twitch": re.compile(r"twitch\.tv", re.IGNORECASE),
}

CREATOR_PATTERNS = [
    # TikTok: tiktok.com/@creator/...
    (re.compile(r"tiktok\.com/@([^/?#]+)", re.IGNORECASE), 1),
    # Instagram: instagram.com/creator/ or instagram.com/reel/CODE
    (re.compile(r"instagram\.com/(?!reel/|p/|stories/|explore/)([^/?#]+)", re.IGNORECASE), 1),
    # YouTube: youtube.com/@creator/...
    (re.compile(r"youtube\.com/@([^/?#]+)", re.IGNORECASE), 1),
    # Twitter / X: twitter.com/creator/... or x.com/creator/...
    (re.compile(r"(?:twitter|x)\.com/(?!i/|search|explore|hashtag)([^/?#]+)", re.IGNORECASE), 1),
]


def detect_platform(url: str) -> str:
    """Return a short platform name based on the URL, or 'other'."""
    for name, pattern in PLATFORM_PATTERNS.items():
        if pattern.search(url):
            return name
    return "other"


def extract_creator_from_url(url: str) -> str | None:
    """Try to pull a creator/username from the URL using known patterns."""
    for pattern, group in CREATOR_PATTERNS:
        m = pattern.search(url)
        if m:
            return m.group(group)
    return None


# ---------------------------------------------------------------------------
# yt-dlp helpers
# ---------------------------------------------------------------------------

def run_ytdlp(args: list[str]) -> subprocess.CompletedProcess:
    """Run yt-dlp through the Python module entry-point."""
    cmd = [sys.executable, "-m", "yt_dlp"] + args
    return subprocess.run(cmd, capture_output=True, text=True)


def fetch_metadata(url: str) -> dict:
    """Use yt-dlp --print to grab uploader, id, and title without downloading."""
    result = run_ytdlp([
        "--print", "%(uploader)s",
        "--print", "%(id)s",
        "--print", "%(title)s",
        "--skip-download",
        "--no-warnings",
        url,
    ])
    lines = result.stdout.strip().split("\n")
    return {
        "uploader": lines[0] if len(lines) >= 1 and lines[0] else None,
        "id": lines[1] if len(lines) >= 2 and lines[1] else None,
        "title": lines[2] if len(lines) >= 3 and lines[2] else None,
    }


def download_video(url: str, output_dir: str) -> str | None:
    """Download a video into *output_dir* and return the file path (or None)."""
    os.makedirs(output_dir, exist_ok=True)
    temp_template = os.path.join(output_dir, "tmp_%(id)s.%(ext)s")

    result = run_ytdlp(["-o", temp_template, "--no-warnings", url])
    if result.returncode != 0:
        print(f"  yt-dlp error: {result.stderr.strip()}")
        return None

    # Find the file that was written (yt-dlp may choose any extension)
    for f in sorted(os.listdir(output_dir)):
        if f.startswith("tmp_") and not f.endswith(".part"):
            return os.path.join(output_dir, f)
    return None


# ---------------------------------------------------------------------------
# Filename helpers
# ---------------------------------------------------------------------------

def sanitize(name: str) -> str:
    """Strip characters that are illegal on Windows/Drive filenames."""
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name).strip()


def build_filename(template: str, *, creator: str, platform: str,
                   date: str, video_id: str, title: str, ext: str) -> str:
    """Expand a naming template and return a sanitized filename with extension."""
    name = template.format(
        creator=sanitize(creator),
        platform=sanitize(platform),
        date=date,
        id=sanitize(video_id),
        title=sanitize(title),
    )
    # Collapse multiple underscores / dashes from empty tokens
    name = re.sub(r"[_\-]{2,}", "_", name).strip("_- ")
    return f"{name}.{ext}"


# ---------------------------------------------------------------------------
# URL input readers
# ---------------------------------------------------------------------------

def read_urls(filepath: str) -> list[str]:
    """Read URLs from a plain-text file (one per line) or a CSV file.

    Auto-detect: if the filename ends with .csv we parse as CSV (looking for
    a column named url/link/video, else first column).  Otherwise each
    non-blank line starting with http is treated as a URL.
    """
    urls: list[str] = []

    if filepath.lower().endswith(".csv"):
        urls = _read_csv(filepath)
    else:
        urls = _read_text(filepath)

    return urls


def _read_text(filepath: str) -> list[str]:
    urls: list[str] = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and line.lower().startswith("http"):
                urls.append(line)
    return urls


def _read_csv(filepath: str) -> list[str]:
    urls: list[str] = []
    with open(filepath, "r", encoding="utf-8") as f:
        sample = f.read(2048)
        f.seek(0)

        try:
            has_header = csv.Sniffer().has_header(sample)
        except csv.Error:
            has_header = False

        reader = csv.reader(f)

        if has_header:
            headers = next(reader)
            url_idx = 0
            for i, h in enumerate(headers):
                if any(kw in h.lower() for kw in ("url", "link", "video")):
                    url_idx = i
                    break
        else:
            url_idx = 0

        for row in reader:
            if row and len(row) > url_idx:
                cell = row[url_idx].strip()
                if cell.lower().startswith("http"):
                    urls.append(cell)
    return urls


# ---------------------------------------------------------------------------
# Google Drive (conditionally imported)
# ---------------------------------------------------------------------------

def _import_drive_deps():
    """Import Google client libraries at runtime so they are only needed when
    the user actually wants to upload to Drive."""
    # pylint: disable=import-outside-toplevel
    try:
        from google.oauth2.credentials import Credentials  # noqa: F811
        from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: F811
        from google.auth.transport.requests import Request  # noqa: F811
        from googleapiclient.discovery import build  # noqa: F811
        from googleapiclient.http import MediaFileUpload  # noqa: F811
        import pickle  # noqa: F811
    except ImportError:
        print(
            "\nGoogle Drive dependencies are not installed.\n"
            "Install them with:\n\n"
            "  pip install google-auth google-auth-oauthlib google-api-python-client\n"
        )
        sys.exit(1)
    return Credentials, InstalledAppFlow, Request, build, MediaFileUpload, pickle


DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def get_drive_service(credentials_path: str = "credentials.json",
                      token_path: str = "token.pickle"):
    """Authenticate and return a Google Drive API service object."""
    Credentials, InstalledAppFlow, Request, build, _, pickle = _import_drive_deps()

    creds = None
    if os.path.exists(token_path):
        with open(token_path, "rb") as token:
            creds = pickle.load(token)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(credentials_path):
                print(
                    "\n" + "=" * 60 +
                    "\nSETUP REQUIRED: Google Drive API credentials\n" +
                    "=" * 60 +
                    f"\n\nExpected file at: {credentials_path}\n"
                    "See README.md for setup instructions.\n"
                )
                sys.exit(1)
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, DRIVE_SCOPES)
            creds = flow.run_local_server(port=0)

        with open(token_path, "wb") as token:
            pickle.dump(creds, token)

    return build("drive", "v3", credentials=creds)


def upload_to_drive(service, file_path: str, filename: str, folder_id: str):
    """Upload a local file to Google Drive and return (file_id, web_link)."""
    _, _, _, _, MediaFileUpload, _ = _import_drive_deps()

    mime, _ = mimetypes.guess_type(file_path)
    if mime is None:
        mime = "application/octet-stream"

    file_metadata = {"name": filename, "parents": [folder_id]}
    media = MediaFileUpload(file_path, mimetype=mime, resumable=True)

    result = service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id, webViewLink",
    ).execute()

    return result.get("id"), result.get("webViewLink")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Social Video Archiver -- batch download, rename, "
                    "and optionally upload social media videos.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  # Local only\n"
            "  python social_video_archiver.py urls.txt\n\n"
            "  # Custom output dir + naming template\n"
            '  python social_video_archiver.py urls.txt --output-dir ./videos '
            '--template "{platform}_{creator}_{date}"\n\n'
            "  # Upload to Google Drive\n"
            "  python social_video_archiver.py urls.csv --drive-folder FOLDER_ID\n"
        ),
    )

    parser.add_argument(
        "input_file",
        help="Path to a text file (one URL per line) or CSV with URLs",
    )
    parser.add_argument(
        "--drive-folder",
        default=None,
        help="Google Drive folder ID to upload to (omit for local-only mode)",
    )
    parser.add_argument(
        "--output-dir",
        default="./archived",
        help="Local directory for downloaded videos (default: ./archived)",
    )
    parser.add_argument(
        "--template",
        default="{creator}_{platform}_{date}_{id}",
        help='Naming template.  Tokens: {creator}, {platform}, {date}, {id}, {title}  '
             '(default: "{creator}_{platform}_{date}_{id}")',
    )
    parser.add_argument(
        "--credentials",
        default="credentials.json",
        help="Path to Google OAuth credentials.json (default: credentials.json)",
    )
    parser.add_argument(
        "--download-dir",
        default=None,
        help="Temporary download directory (default: <output-dir>/.tmp)",
    )

    args = parser.parse_args()

    # Resolve directories
    output_dir = os.path.abspath(args.output_dir)
    download_dir = args.download_dir or os.path.join(output_dir, ".tmp")
    local_only = args.drive_folder is None
    mode_label = "LOCAL-ONLY" if local_only else "DRIVE UPLOAD"

    print("\n" + "=" * 60)
    print("Social Video Archiver")
    print(f"Mode: {mode_label}")
    print("=" * 60 + "\n")

    # --- Read URLs ---------------------------------------------------------
    if not os.path.exists(args.input_file):
        print(f"Input file '{args.input_file}' not found!")
        sys.exit(1)

    urls = read_urls(args.input_file)
    print(f"Found {len(urls)} URL(s) in {args.input_file}\n")
    if not urls:
        print("No URLs found -- nothing to do.")
        return

    # --- Optional Drive auth -----------------------------------------------
    drive_service = None
    if not local_only:
        print("Authenticating with Google Drive...")
        try:
            drive_service = get_drive_service(args.credentials)
            print("  Connected to Google Drive\n")
        except Exception as e:
            print(f"  Failed to connect: {e}")
            sys.exit(1)

    os.makedirs(output_dir, exist_ok=True)

    # --- Process each URL --------------------------------------------------
    results: list[dict] = []
    date_str = datetime.now().strftime("%Y%m%d")

    for i, url in enumerate(urls, 1):
        print(f"[{i}/{len(urls)}] {url[:80]}")
        platform = detect_platform(url)
        creator = extract_creator_from_url(url)
        video_id = ""
        title = ""

        # Fetch metadata from yt-dlp
        try:
            meta = fetch_metadata(url)
            if not creator and meta.get("uploader"):
                creator = meta["uploader"]
            video_id = meta.get("id") or ""
            title = meta.get("title") or ""
        except Exception:
            pass

        creator = creator or "unknown"

        # Download
        video_path = download_video(url, download_dir)
        if not video_path:
            print("  FAILED to download\n")
            results.append({
                "url": url, "creator": creator, "platform": platform,
                "filename": "", "status": "download_failed",
                "drive_id": "", "link": "", "error": "yt-dlp download failed",
            })
            continue

        ext = Path(video_path).suffix.lstrip(".")

        new_filename = build_filename(
            args.template,
            creator=creator,
            platform=platform,
            date=date_str,
            video_id=video_id,
            title=title,
            ext=ext,
        )

        final_path = os.path.join(output_dir, new_filename)
        # Avoid overwriting existing files
        if os.path.exists(final_path):
            stem = Path(new_filename).stem
            ts = datetime.now().strftime("%H%M%S")
            new_filename = f"{stem}_{ts}.{ext}"
            final_path = os.path.join(output_dir, new_filename)

        os.rename(video_path, final_path)
        print(f"  Platform : {platform}")
        print(f"  Creator  : {creator}")
        print(f"  Saved as : {new_filename}")

        drive_id = ""
        drive_link = ""
        status = "downloaded"

        # Upload to Drive if requested
        if drive_service:
            try:
                drive_id, drive_link = upload_to_drive(
                    drive_service, final_path, new_filename, args.drive_folder,
                )
                status = "uploaded"
                print(f"  Drive link: {drive_link}")
            except Exception as e:
                status = "upload_failed"
                print(f"  Upload FAILED: {e}")
                results.append({
                    "url": url, "creator": creator, "platform": platform,
                    "filename": new_filename, "status": status,
                    "drive_id": "", "link": "", "error": str(e),
                })
                print()
                continue

        results.append({
            "url": url, "creator": creator, "platform": platform,
            "filename": new_filename, "status": status,
            "drive_id": drive_id, "link": drive_link, "error": "",
        })
        print()

    # --- Cleanup temp dir --------------------------------------------------
    try:
        if os.path.isdir(download_dir) and not os.listdir(download_dir):
            os.rmdir(download_dir)
    except OSError:
        pass

    # --- Summary -----------------------------------------------------------
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)

    ok = [r for r in results if r["status"] in ("downloaded", "uploaded")]
    fail = [r for r in results if r["status"] not in ("downloaded", "uploaded")]

    print(f"  Succeeded : {len(ok)}")
    print(f"  Failed    : {len(fail)}")

    if ok:
        print("\nProcessed files:")
        for r in ok:
            tag = "[uploaded]" if r["status"] == "uploaded" else "[local]"
            print(f"  {tag} {r['filename']}")

    if fail:
        print("\nFailed URLs:")
        for r in fail:
            print(f"  {r['url'][:70]}  ({r['status']})")

    # --- Write log ---------------------------------------------------------
    log_name = f"archive_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    log_path = os.path.join(output_dir, log_name)
    fieldnames = ["url", "creator", "platform", "filename", "status",
                  "drive_id", "link", "error"]
    with open(log_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    print(f"\nLog saved to: {log_path}")
    print()


if __name__ == "__main__":
    main()
