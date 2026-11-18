#!/usr/bin/env bash
# Rasterize a locally generated SVG using an isolated Chromium profile.
set -eu
browser=${CHROME:-}
if [[ -z "$browser" ]]; then
  for candidate in chromium chromium-browser google-chrome chrome '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'; do
    if command -v "$candidate" >/dev/null; then browser="$(command -v "$candidate")"; break; fi
  done
fi
command -v "$browser" >/dev/null 2>&1 || { echo 'Set CHROME to a Chrome/Chromium executable' >&2; exit 1; }
[[ "${1:-}" != --check ]] || exit 0
svg=${1:?usage: make_cover.sh COVER.svg [OUTPUT.png]}
out=${2:-$(dirname "$svg")/cover.png}
[[ -f "$svg" && ! -e "$out" ]] || { echo 'Check the SVG and choose a new output file' >&2; exit 1; }
tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT
uri="$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve().as_uri())' "$svg")"
"$browser" --headless --disable-gpu --hide-scrollbars --allow-file-access-from-files \
  --no-first-run --disable-background-networking --user-data-dir="$tmp_dir/profile" \
  --screenshot="$tmp_dir/cover.png" --window-size=1600,2560 --force-device-scale-factor=1 "$uri" >/dev/null || {
  [[ -s "$tmp_dir/cover.png" ]] || { echo "Chromium failed before rendering" >&2; exit 1; }
}
python3 - "$tmp_dir/cover.png" "$out" <<'PY'
from pathlib import Path
from shutil import copyfileobj
from struct import unpack
import sys
with Path(sys.argv[1]).open('rb') as rgb:
    header = rgb.read(24)
    if header[:8] != b'\x89PNG\r\n\x1a\n' or unpack('>II', header[16:24]) != (1600, 2560):
        raise SystemExit('Unexpected cover size or format')
    rgb.seek(0)
    with Path(sys.argv[2]).open('xb') as out:
        copyfileobj(rgb, out)
PY
