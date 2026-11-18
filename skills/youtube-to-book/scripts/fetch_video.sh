#!/usr/bin/env bash
# Download one permitted public source without reading browser profiles.
set -euo pipefail
url=${1:?usage: fetch_video.sh URL EMPTY_DIRECTORY}
dest=${2:?usage: fetch_video.sh URL EMPTY_DIRECTORY}
case "$url" in https://*|http://*) ;; *) echo 'Expected an HTTP(S) URL' >&2; exit 2 ;; esac
mkdir -p "$dest"
if [[ -n "$(ls -A "$dest")" ]]; then
  echo 'Choose an empty directory to keep each recording and its metadata together.' >&2
  exit 2
fi
quality=${QUALITY:-480}
[[ "$quality" =~ ^[0-9]+$ ]] || { echo 'QUALITY must be a height in pixels' >&2; exit 2; }
common=(--ignore-config --no-playlist --retries 2 --fragment-retries 2 --socket-timeout 20)
yt-dlp "${common[@]}" --dump-single-json -- "$url" | python3 -c '
import json, sys
item = json.load(sys.stdin)
keys = ("id", "title", "channel", "uploader", "duration", "language")
json.dump({k: item.get(k) for k in keys}, sys.stdout, ensure_ascii=False, indent=2)
' > "$dest/meta.json"
yt-dlp "${common[@]}" -f bestaudio/best --extract-audio --audio-format m4a \
  -o "$dest/audio.%(ext)s" -- "$url"
if [[ "${SKIP_VIDEO:-0}" != 1 ]]; then
  yt-dlp "${common[@]}" -f "bestvideo[height<=$quality]/best[height<=?$quality]" \
    --recode-video mp4 -o "$dest/video.%(ext)s" -- "$url"
  [[ -s "$dest/video.mp4" ]]
fi
[[ -s "$dest/audio.m4a" && -s "$dest/meta.json" ]]
