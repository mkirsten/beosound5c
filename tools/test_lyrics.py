#!/usr/bin/env python3
"""
Fetch time-synced lyrics via the Musixmatch desktop API.

Usage:
    python3 test_lyrics.py "Track Name" "Artist"
    python3 test_lyrics.py "Bohemian Rhapsody" "Queen"
    python3 test_lyrics.py "Never Gonna Give You Up" "Rick Astley" 213
"""

import json
import sys
import urllib.error
import urllib.parse
import urllib.request

MUSIXMATCH_API = "https://apic-desktop.musixmatch.com/ws/1.1"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def get_musixmatch_token():
    """Get a fresh Musixmatch usertoken."""
    url = f"{MUSIXMATCH_API}/token.get?app_id=web-desktop-app-v1.0"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
        body = data.get("message", {}).get("body", {})
        token = body.get("user_token")
        if not token:
            status = data.get("message", {}).get("header", {}).get("status_code")
            raise RuntimeError(f"Musixmatch token.get failed (status {status})")
        return token


def fetch_synced_lyrics(track_name, artist_name, duration_s=0):
    """Fetch time-synced lyrics from Musixmatch. Returns dict or None."""
    token = get_musixmatch_token()
    params = {
        "q_track": track_name,
        "q_artist": artist_name,
        "usertoken": token,
        "app_id": "web-desktop-app-v1.0",
        "subtitle_format": "mxm",
    }
    if duration_s > 0:
        params["f_subtitle_length"] = duration_s

    url = f"{MUSIXMATCH_API}/matcher.subtitle.get?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"  Musixmatch subtitle request failed: HTTP {e.code}")
        return None

    status = data["message"]["header"]["status_code"]
    if status != 200:
        return fetch_plain_lyrics(track_name, artist_name, token)

    subtitle = data["message"]["body"]["subtitle"]
    body = subtitle.get("subtitle_body", "")
    if not body:
        return fetch_plain_lyrics(track_name, artist_name, token)

    try:
        lines = json.loads(body)
    except json.JSONDecodeError:
        return None

    return {
        "sync_type": "LINE_SYNCED",
        "lines": [
            {
                "start_ms": int(
                    l["time"]["minutes"] * 60000
                    + l["time"]["seconds"] * 1000
                    + l["time"].get("hundredths", 0) * 10
                ),
                "text": l.get("text", ""),
            }
            for l in lines
        ],
    }


def fetch_plain_lyrics(track_name, artist_name, token=None):
    """Fetch plain (unsynced) lyrics from Musixmatch."""
    if not token:
        token = get_musixmatch_token()
    params = urllib.parse.urlencode({
        "q_track": track_name,
        "q_artist": artist_name,
        "usertoken": token,
        "app_id": "web-desktop-app-v1.0",
    })
    url = f"{MUSIXMATCH_API}/matcher.lyrics.get?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception:
        return None

    if data["message"]["header"]["status_code"] != 200:
        return None

    body = data["message"]["body"]["lyrics"].get("lyrics_body", "")
    if not body:
        return None

    return {
        "sync_type": "UNSYNCED",
        "lines": [{"start_ms": 0, "text": line} for line in body.split("\n") if line.strip()],
    }


def format_time(ms):
    s = ms // 1000
    return f"{s // 60}:{s % 60:02d}"


def main():
    if len(sys.argv) < 3:
        print("Usage: python3 test_lyrics.py \"Track Name\" \"Artist\" [duration_seconds]")
        sys.exit(1)

    track_name = sys.argv[1]
    artist_name = sys.argv[2]
    duration_s = int(sys.argv[3]) if len(sys.argv) > 3 else 0

    print(f"Track:  {track_name}")
    print(f"Artist: {artist_name}")
    if duration_s:
        print(f"Duration: {format_time(duration_s * 1000)}")
    print()

    result = fetch_synced_lyrics(track_name, artist_name, duration_s)
    if not result:
        print("No lyrics found.")
        sys.exit(1)

    print(f"Sync type: {result['sync_type']}")
    print(f"Lines:     {len(result['lines'])}")
    print("-" * 60)
    for line in result["lines"]:
        text = line["text"]
        if not text:
            print()
            continue
        if result["sync_type"] == "LINE_SYNCED":
            print(f"  [{format_time(line['start_ms'])}]  {text}")
        else:
            print(f"  {text}")
    print("-" * 60)


if __name__ == "__main__":
    main()
