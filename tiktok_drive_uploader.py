#!/usr/bin/env python3
"""
TikTok Video Downloader & Google Drive Uploader
Downloads TikTok videos from CSV, renames to TTS---[CreatorName], uploads to Drive.
"""

import os
import csv
import re
import subprocess
import sys
import argparse
from pathlib import Path
from datetime import datetime

# Google Drive imports
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import pickle

SCOPES = ['https://www.googleapis.com/auth/drive.file']


def get_drive_service(credentials_path="credentials.json", token_path="token.pickle"):
    """Authenticate and return Google Drive service."""
    creds = None

    if os.path.exists(token_path):
        with open(token_path, 'rb') as token:
            creds = pickle.load(token)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(credentials_path):
                print("\n" + "=" * 60)
                print("SETUP REQUIRED: Google Drive API Credentials")
                print("=" * 60)
                print(f"\nExpected credentials file at: {credentials_path}")
                print("See README.md for setup instructions.")
                sys.exit(1)

            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(token_path, 'wb') as token:
            pickle.dump(creds, token)

    return build('drive', 'v3', credentials=creds)


def extract_creator_from_url(url):
    """Extract TikTok creator username from URL."""
    match = re.search(r'tiktok\.com/@([^/]+)', url)
    return match.group(1) if match else None


def run_ytdlp(args):
    """Run yt-dlp through Python module."""
    cmd = [sys.executable, '-m', 'yt_dlp'] + args
    return subprocess.run(cmd, capture_output=True, text=True)


def download_tiktok_video(url, output_dir):
    """Download TikTok video using yt-dlp, return path and creator."""
    os.makedirs(output_dir, exist_ok=True)

    creator = extract_creator_from_url(url)
    temp_template = os.path.join(output_dir, "temp_%(id)s.%(ext)s")

    try:
        result = run_ytdlp(['--print', 'uploader', '--print', 'id', '--skip-download', url])
        lines = result.stdout.strip().split('\n')
        if len(lines) >= 2:
            uploader = lines[0]
            if not creator:
                creator = uploader

        result = run_ytdlp(['-o', temp_template, '--no-warnings', url])
        if result.returncode != 0:
            print(f"  yt-dlp error: {result.stderr}")
            return None, creator

        for f in os.listdir(output_dir):
            if f.startswith("temp_") and not f.endswith('.part'):
                return os.path.join(output_dir, f), creator

    except Exception as e:
        print(f"  Error downloading: {e}")
        return None, creator

    return None, creator


def sanitize_filename(name):
    """Remove invalid characters from filename."""
    return re.sub(r'[<>:"/\\|?*]', '', name).strip()


def upload_to_drive(service, file_path, filename, folder_id):
    """Upload file to Google Drive folder."""
    file_metadata = {
        'name': filename,
        'parents': [folder_id]
    }

    media = MediaFileUpload(
        file_path,
        mimetype='video/mp4',
        resumable=True
    )

    file = service.files().create(
        body=file_metadata,
        media_body=media,
        fields='id, webViewLink'
    ).execute()

    return file.get('id'), file.get('webViewLink')


def read_urls_from_csv(csv_path, url_column=None):
    """Read TikTok URLs from CSV file."""
    urls = []

    with open(csv_path, 'r', encoding='utf-8') as f:
        sample = f.read(1024)
        f.seek(0)
        has_header = csv.Sniffer().has_header(sample)

        reader = csv.reader(f)

        if has_header:
            headers = next(reader)
            if url_column and url_column in headers:
                url_idx = headers.index(url_column)
            else:
                url_idx = 0
                for i, h in enumerate(headers):
                    if any(x in h.lower() for x in ['url', 'link', 'tiktok']):
                        url_idx = i
                        break
        else:
            url_idx = 0

        for row in reader:
            if row and len(row) > url_idx:
                url = row[url_idx].strip()
                if 'tiktok.com' in url:
                    urls.append(url)

    return urls


def main():
    parser = argparse.ArgumentParser(
        description='Download TikTok videos from CSV and upload to Google Drive'
    )
    parser.add_argument('csv_file', help='Path to CSV file with TikTok URLs')
    parser.add_argument('folder_id', help='Google Drive folder ID to upload to')
    parser.add_argument('--url-column', default='url',
                        help='CSV column name containing URLs (default: auto-detect)')
    parser.add_argument('--download-dir', default='./downloads',
                        help='Temporary download directory (default: ./downloads)')
    parser.add_argument('--credentials', default='credentials.json',
                        help='Path to Google OAuth credentials.json')
    parser.add_argument('--prefix', default='TTS',
                        help='Filename prefix (default: TTS)')
    args = parser.parse_args()

    print("\n" + "=" * 60)
    print("TikTok Video Downloader & Google Drive Uploader")
    print("=" * 60 + "\n")

    if not os.path.exists(args.csv_file):
        print(f"CSV file '{args.csv_file}' not found!")
        sys.exit(1)

    print(f"Reading URLs from {args.csv_file}...")
    urls = read_urls_from_csv(args.csv_file, args.url_column)
    print(f"   Found {len(urls)} TikTok URLs\n")

    if not urls:
        print("No TikTok URLs found in CSV!")
        return

    print("Authenticating with Google Drive...")
    try:
        drive_service = get_drive_service(args.credentials)
        print("   Connected to Google Drive\n")
    except Exception as e:
        print(f"   Failed to connect: {e}\n")
        return

    results = []

    for i, url in enumerate(urls, 1):
        print(f"[{i}/{len(urls)}] Processing: {url[:60]}...")

        video_path, creator = download_tiktok_video(url, args.download_dir)

        if not video_path:
            print(f"   Failed to download")
            results.append({'url': url, 'status': 'download_failed'})
            continue

        if not creator:
            creator = "Unknown"

        creator_clean = sanitize_filename(creator)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        new_filename = f"{args.prefix}---{creator_clean}_{timestamp}.mp4"

        print(f"   Creator: @{creator}")
        print(f"   Filename: {new_filename}")

        try:
            file_id, link = upload_to_drive(drive_service, video_path, new_filename, args.folder_id)
            print(f"   Uploaded to Drive")
            results.append({
                'url': url,
                'creator': creator,
                'filename': new_filename,
                'drive_id': file_id,
                'link': link,
                'status': 'success'
            })
        except Exception as e:
            print(f"   Upload failed: {e}")
            results.append({'url': url, 'status': 'upload_failed', 'error': str(e)})

        try:
            os.remove(video_path)
        except Exception:
            pass

        print()

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    successful = [r for r in results if r.get('status') == 'success']
    failed = [r for r in results if r.get('status') != 'success']

    print(f"Successfully processed: {len(successful)}")
    print(f"Failed: {len(failed)}")

    if successful:
        print("\nUploaded files:")
        for r in successful:
            print(f"  - {r['filename']}")

    if failed:
        print("\nFailed URLs:")
        for r in failed:
            print(f"  - {r['url'][:50]}... ({r['status']})")

    # Save log
    log_file = f"upload_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    with open(log_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['url', 'creator', 'filename', 'drive_id', 'link', 'status', 'error'])
        writer.writeheader()
        writer.writerows(results)
    print(f"\nResults saved to: {log_file}")

    try:
        os.rmdir(args.download_dir)
    except Exception:
        pass


if __name__ == "__main__":
    main()
