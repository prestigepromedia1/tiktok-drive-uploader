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
import time
import random
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

def _detect_browser() -> str | None:
    """Find a browser to pull cookies from (best anti-block measure).

    Chrome locks its cookie DB while running, so we prefer Edge/Firefox first
    (which don't have this issue), then try Chrome only if others fail.
    """
    # Edge and Firefox don't lock their cookie DB while running
    for browser in ("edge", "firefox", "brave", "chrome"):
        try:
            result = subprocess.run(
                [sys.executable, "-m", "yt_dlp",
                 "--cookies-from-browser", browser,
                 "--skip-download", "--no-warnings",
                 "https://www.example.com"],
                capture_output=True, text=True, timeout=15,
            )
            stderr = result.stderr.lower()
            # Skip if it can't copy the cookie DB (browser is running + locked)
            if "could not copy" in stderr or "could not find" in stderr:
                continue
            if result.returncode == 0:
                return browser
        except Exception:
            continue
    return None


# Detected once at startup, reused for every call
_COOKIE_BROWSER: str | None = None
_COOKIE_CHECKED = False
_PROXY: str | None = None

# Alternate TikTok API hostnames -- when the default is blocked, these
# route through different CDN edges that may not be blocked yet.
_TIKTOK_ALT_APIS = [
    "api22-normal-c-alisg.tiktokv.com",
    "api16-normal-c-useast2a.tiktokv.com",
    "api19-normal-c-useast1a.tiktokv.com",
]


def configure_proxy(proxy: str | None):
    """Set a proxy for all yt-dlp calls."""
    global _PROXY
    _PROXY = proxy


def _stealth_args(use_cookies: bool = True) -> list[str]:
    """Return yt-dlp flags that make requests look like a real browser."""
    global _COOKIE_BROWSER, _COOKIE_CHECKED
    args = [
        "--user-agent",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "--referer", "https://www.tiktok.com/",
        "--extractor-retries", "3",
    ]
    if _PROXY:
        args += ["--proxy", _PROXY]
    if not _COOKIE_CHECKED:
        _COOKIE_CHECKED = True
        _COOKIE_BROWSER = _detect_browser()
        if _COOKIE_BROWSER:
            print(f"  Using cookies from {_COOKIE_BROWSER} (anti-block)")
        else:
            print("  No browser cookies available -- using stealth headers only")
        if _PROXY:
            print(f"  Proxy: {_PROXY}")
        print()
    if use_cookies and _COOKIE_BROWSER:
        args += ["--cookies-from-browser", _COOKIE_BROWSER]
    return args


def run_ytdlp(args: list[str], extra_args: list[str] | None = None) -> subprocess.CompletedProcess:
    """Run yt-dlp through the Python module entry-point."""
    cmd = [sys.executable, "-m", "yt_dlp"] + _stealth_args() + (extra_args or []) + args
    return subprocess.run(cmd, capture_output=True, text=True)


def fetch_metadata(url: str) -> dict:
    """Use yt-dlp --print to grab uploader, id, title, and upload_date."""
    result = run_ytdlp([
        "--print", "%(uploader)s",
        "--print", "%(id)s",
        "--print", "%(title)s",
        "--print", "%(upload_date)s",
        "--skip-download",
        "--no-warnings",
        url,
    ])
    lines = result.stdout.strip().split("\n")
    return {
        "uploader": lines[0] if len(lines) >= 1 and lines[0] != "NA" else None,
        "id": lines[1] if len(lines) >= 2 and lines[1] != "NA" else None,
        "title": lines[2] if len(lines) >= 3 and lines[2] != "NA" else None,
        "upload_date": lines[3] if len(lines) >= 4 and lines[3] != "NA" else None,
    }


def _is_blocked_error(stderr: str) -> bool:
    """Check if a yt-dlp error indicates IP/rate blocking."""
    err = stderr.lower()
    return any(s in err for s in (
        "blocked", "unable to extract", "http error 403",
        "http error 429", "http error 503", "rate limit",
    ))


def _try_download(url: str, template: str, extra_args: list[str] | None = None) -> subprocess.CompletedProcess:
    """Single download attempt with optional extra yt-dlp args."""
    return run_ytdlp(["-o", template, "--no-warnings", url], extra_args=extra_args)


def download_video(url: str, output_dir: str, retries: int = 2) -> str | None:
    """Download with multi-strategy fallback:

    1. Standard yt-dlp with stealth headers
    2. Retry with backoff (for transient blocks)
    3. Alternate TikTok API endpoints (for persistent blocks)
    """
    os.makedirs(output_dir, exist_ok=True)
    temp_template = os.path.join(output_dir, "tmp_%(id)s.%(ext)s")

    # --- Strategy 1: Standard download with retries ---
    last_err = ""
    for attempt in range(1, retries + 2):
        result = _try_download(url, temp_template)
        if result.returncode == 0:
            return _find_downloaded(output_dir)
        last_err = result.stderr.strip()
        if attempt <= retries and _is_blocked_error(last_err):
            wait = attempt * 5 + random.randint(2, 8)
            print(f"  Retry {attempt}/{retries} in {wait}s...")
            time.sleep(wait)
        elif not _is_blocked_error(last_err):
            # Non-block error (e.g. video deleted) -- don't waste time retrying
            print(f"  yt-dlp error: {last_err}")
            return None

    # --- Strategy 2: Alternate TikTok API endpoints ---
    is_tiktok = "tiktok" in url.lower()
    if is_tiktok and _is_blocked_error(last_err):
        for i, api_host in enumerate(_TIKTOK_ALT_APIS):
            print(f"  Trying alternate endpoint {i+1}/{len(_TIKTOK_ALT_APIS)}...")
            time.sleep(random.randint(2, 5))
            extra = ["--extractor-args", f"tiktok:api_hostname={api_host}"]
            result = _try_download(url, temp_template, extra_args=extra)
            if result.returncode == 0:
                return _find_downloaded(output_dir)
            if not _is_blocked_error(result.stderr):
                break  # Different error, stop trying alternates

    print(f"  All download strategies exhausted")
    return None


def _find_downloaded(output_dir: str) -> str | None:
    """Find the temp file that yt-dlp just wrote."""
    for f in sorted(os.listdir(output_dir)):
        if f.startswith("tmp_") and not f.endswith(".part"):
            return os.path.join(output_dir, f)
    return None


# ---------------------------------------------------------------------------
# Filename helpers
# ---------------------------------------------------------------------------

def sanitize(name: str) -> str:
    """Strip characters that are illegal on Windows/Drive filenames.
    Replace dots and underscores with dashes — no dots or underscores in output."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name)
    name = name.replace(".", "-").replace("_", "-")
    name = name.strip()
    return name


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
    # Replace underscores with dashes, collapse repeated dashes (but preserve intentional --)
    name = name.replace("_", "-")
    # Collapse 3+ dashes down to 2
    name = re.sub(r"-{3,}", "--", name)
    name = name.strip("- ")
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
                      token_path: str = "token.json"):
    """Authenticate and return a Google Drive API service object."""
    Credentials, InstalledAppFlow, Request, build, _, pickle = _import_drive_deps()

    creds = None

    # Prefer JSON token (modern), fall back to legacy pickle
    if os.path.exists(token_path):
        try:
            from google.oauth2.credentials import Credentials as OAuthCreds
            creds = OAuthCreds.from_authorized_user_file(token_path, DRIVE_SCOPES)
        except Exception:
            pass

    if not creds:
        pickle_path = token_path.replace(".json", ".pickle")
        if os.path.exists(pickle_path):
            with open(pickle_path, "rb") as f:
                creds = pickle.load(f)

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

        # Save as JSON (modern format)
        with open(token_path, "w") as f:
            f.write(creds.to_json())

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
        supportsAllDrives=True,
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
        default="TTS--{creator}",
        help='Naming template.  Tokens: {creator}, {platform}, {date}, {id}, {title}  '
             '(default: "TTS--{creator}")',
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
    parser.add_argument(
        "--delay",
        type=float,
        default=2.0,
        help="Seconds to wait between downloads to avoid rate limits (default: 2)",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=2,
        help="Retry failed downloads N times with backoff (default: 2)",
    )
    parser.add_argument(
        "--proxy",
        default=None,
        help="HTTP/SOCKS5 proxy for downloads (e.g. socks5://127.0.0.1:1080)",
    )

    args = parser.parse_args()

    # Configure proxy if provided
    if args.proxy:
        configure_proxy(args.proxy)

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
    fallback_date = datetime.now().strftime("%Y%m%d")

    for i, url in enumerate(urls, 1):
        print(f"[{i}/{len(urls)}] {url[:80]}")
        platform = detect_platform(url)
        creator = extract_creator_from_url(url)
        video_id = ""
        title = ""
        date_str = fallback_date

        # Fetch metadata from yt-dlp
        try:
            meta = fetch_metadata(url)
            if not creator and meta.get("uploader"):
                creator = meta["uploader"]
            video_id = meta.get("id") or ""
            title = meta.get("title") or ""
            # Use post date if available (yt-dlp returns YYYYMMDD)
            if meta.get("upload_date"):
                date_str = meta["upload_date"]
        except Exception:
            pass

        creator = creator or "unknown"

        # Download (with retry + backoff)
        video_path = download_video(url, download_dir, retries=args.retries)
        if not video_path:
            print("  FAILED to download\n")
            results.append({
                "url": url, "creator": creator, "platform": platform,
                "filename": "", "status": "download_failed",
                "drive_id": "", "link": "", "error": "yt-dlp download failed",
            })
            # Still delay before next URL to cool down
            if i < len(urls):
                time.sleep(args.delay)
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
        # Avoid overwriting existing files -- append incrementing counter
        if os.path.exists(final_path):
            stem = Path(new_filename).stem
            counter = 2
            while os.path.exists(os.path.join(output_dir, f"{stem}-{counter}.{ext}")):
                counter += 1
            new_filename = f"{stem}-{counter}.{ext}"
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

        # Pace downloads to avoid rate limits
        if i < len(urls):
            jitter = random.uniform(0, args.delay * 0.5)
            time.sleep(args.delay + jitter)

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

    # --- Write retry file for failed downloads ----------------------------
    download_fails = [r for r in results if r["status"] == "download_failed"]
    if download_fails:
        retry_name = f"retry_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        retry_path = os.path.join(output_dir, retry_name)
        with open(retry_path, "w", encoding="utf-8") as f:
            for r in download_fails:
                f.write(r["url"] + "\n")
        print(f"\n  {len(download_fails)} blocked URL(s) saved to: {retry_path}")
        print("  Retry later from a different network, or with --proxy")

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
