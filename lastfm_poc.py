"""
Last.fm API Proof of Concept
"""

import os
import sys
import csv
import time
import requests
from dotenv import load_dotenv

load_dotenv()  # reads a .env file in the current directory, if present, and
                # loads any KEY=value lines from it into the environment

BASE_URL = "https://ws.audioscrobbler.com/2.0/"
USER_AGENT = "LastFmPocScript/1.0 (contact: you@example.com)"
PAGE_LIMIT = 200  # max allowed by the API
REQUEST_DELAY = 0.25  # be polite, avoid hammering the API


def call_lastfm(method, api_key, **params):
    """Make a request to the Last.fm API and return the parsed JSON response."""
    query = {
        "method": method,
        "api_key": api_key,
        "format": "json",
        **params,
    }
    response = requests.get(
        BASE_URL,
        params=query,
        headers={"User-Agent": USER_AGENT},
        timeout=15,
    )
    response.raise_for_status()
    data = response.json()
    if "error" in data:
        raise RuntimeError(f"Last.fm API error {data['error']}: {data.get('message')}")
    return data


def get_all_scrobbles(api_key, username):
    """
    Page through user.getRecentTracks to pull the user's complete
    listening history. Returns a list of dicts: artist, track, album,
    date, timestamp.
    """
    all_tracks = []
    page = 1
    total_pages = None

    while total_pages is None or page <= total_pages:
        data = call_lastfm(
            "user.getrecenttracks",
            api_key,
            user=username,
            limit=PAGE_LIMIT,
            page=page,
        )
        recent = data["recenttracks"]

        if total_pages is None:
            total_pages = int(recent["@attr"]["totalPages"])
            total_tracks = int(recent["@attr"]["total"])
            print(f"Found {total_tracks:,} scrobbles across {total_pages:,} pages for '{username}'...")

        tracks = recent.get("track", [])
        for t in tracks:
            # Skip the currently-playing track; it has no "date" field yet
            if t.get("@attr", {}).get("nowplaying") == "true":
                continue
            all_tracks.append({
                "artist": t["artist"]["#text"],
                "track": t["name"],
                "album": t["album"]["#text"],
                "date": t["date"]["#text"],
                "timestamp": t["date"]["uts"],
            })

        print(f"  Page {page}/{total_pages} fetched ({len(all_tracks):,} tracks so far)")
        page += 1
        time.sleep(REQUEST_DELAY)

    return all_tracks


def get_track_durations(api_key, tracks):
    """
    Look up the real duration (in seconds) of every unique artist/track
    pair in the scrobble history, via track.getInfo. Returns a dict
    mapping (artist, track) -> duration_seconds (0 if unknown/unavailable).
    Each unique song is only looked up once, no matter how many times
    it was played.
    """
    unique_pairs = sorted({(t["artist"], t["track"]) for t in tracks})
    print(f"\nLooking up durations for {len(unique_pairs):,} unique tracks...")

    durations = {}
    for i, (artist, track) in enumerate(unique_pairs, start=1):
        try:
            data = call_lastfm("track.getinfo", api_key, artist=artist, track=track)
            duration_ms = int(data.get("track", {}).get("duration", 0) or 0)
            durations[(artist, track)] = duration_ms // 1000  # ms -> seconds
        except Exception:
            # Some tracks won't be found or won't have duration data;
            # treat those as unknown (0) rather than failing the whole run.
            durations[(artist, track)] = 0

        if i % 25 == 0 or i == len(unique_pairs):
            print(f"  {i:,}/{len(unique_pairs):,} tracks looked up")
        time.sleep(REQUEST_DELAY)

    return durations


def format_duration(total_seconds):
    """Turn a number of seconds into a human-readable 'Xd Xh Xm Xs' string."""
    total_seconds = int(total_seconds)
    days, rem = divmod(total_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    if minutes or hours or days:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    return " ".join(parts)


def save_to_csv(tracks, filename="scrobbles.csv"):
    fieldnames = ["artist", "track", "album", "date", "timestamp", "duration_seconds"]
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(tracks)
    print(f"\nSaved {len(tracks):,} scrobbles to {filename}")


def print_summary(tracks):
    from collections import Counter

    artist_counts = Counter(t["artist"] for t in tracks)
    print("\nTop 10 most-played artists (in this history):")
    for i, (artist, count) in enumerate(artist_counts.most_common(10), start=1):
        print(f"  {i}. {artist} ({count:,} plays)")

    total_seconds = sum(t["duration_seconds"] for t in tracks)
    known_seconds = sum(t["duration_seconds"] for t in tracks if t["duration_seconds"] > 0)
    unknown_count = sum(1 for t in tracks if t["duration_seconds"] == 0)

    print(f"\nTotal listening time: {format_duration(total_seconds)}")
    if unknown_count:
        print(f"  (Note: {unknown_count:,} of {len(tracks):,} plays had no duration data on "
              f"Last.fm and were counted as 0s; known-duration total was "
              f"{format_duration(known_seconds)})")

    artist_seconds = Counter()
    for t in tracks:
        artist_seconds[t["artist"]] += t["duration_seconds"]
    print("\nTop 10 artists by listening time:")
    for i, (artist, seconds) in enumerate(artist_seconds.most_common(10), start=1):
        print(f"  {i}. {artist} ({format_duration(seconds)})")


def main():
    api_key = os.environ.get("LASTFM_API_KEY")
    if not api_key:
        print("Error: LASTFM_API_KEY environment variable is not set.")
        print("  Add a line to your .env file: LASTFM_API_KEY=your_key_here")
        sys.exit(1)

    # Username: use a command-line argument if given, otherwise fall back
    # to LASTFM_USERNAME from the environment/.env file.
    if len(sys.argv) == 2:
        username = sys.argv[1]
    elif len(sys.argv) == 1:
        username = os.environ.get("LASTFM_USERNAME")
        if not username:
            print("Error: no username given and LASTFM_USERNAME is not set.")
            print("  Either run: python lastfm_poc.py <LASTFM_USERNAME>")
            print("  or add a line to your .env file: LASTFM_USERNAME=your_username")
            sys.exit(1)
    else:
        print("Usage: python lastfm_poc.py [LASTFM_USERNAME]")
        print("  (username is optional if LASTFM_USERNAME is set in .env)")
        sys.exit(1)

    try:
        tracks = get_all_scrobbles(api_key, username)

        durations = get_track_durations(api_key, tracks)
        for t in tracks:
            t["duration_seconds"] = durations[(t["artist"], t["track"])]

        save_to_csv(tracks)
        print_summary(tracks)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()