#!/usr/bin/env bash
# Inspect requirements for only the selected stages. Installs nothing.
set -u
if [[ $# == 0 ]]; then echo 'usage: preflight.sh fetch|frames|asr-ru|asr-whisper|cover|epub [...]' >&2; exit 2; fi
rc=0
need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing executable: $1" >&2; rc=1; }; }
file() { [[ -s "$1" ]] || { echo "Missing file: $1" >&2; rc=1; }; }
base_dir="$(cd "$(dirname "$0")" && pwd)"
need python3
for stage do
  case "$stage" in
    fetch) need yt-dlp; need ffmpeg; need ffprobe ;;
    frames) need ffmpeg; need ffprobe ;;
    epub) need pandoc ;;
    cover) bash "$base_dir/make_cover.sh" --check || rc=1 ;;
    asr-whisper)
      need ffmpeg; need "${WHISPER_BIN:-whisper-cli}"; file "${WHISPER_MODEL:-set WHISPER_MODEL}"
      if [[ -n "${VAD_MODEL:-}" ]]; then file "$VAD_MODEL"; fi ;;
    asr-ru)
      need ffmpeg; need ffprobe
      "${GIGAAM_PY:-python3}" -c 'import torch, numpy, gigaam' || rc=1
      cache_dir="${GIGAAM_CACHE:-$HOME/.cache/gigaam}"
      file "$cache_dir/v3_e2e_rnnt.ckpt"; file "$cache_dir/v3_e2e_rnnt_tokenizer.model" ;;
    *) echo "Unknown stage: $stage" >&2; exit 2 ;;
  esac
done
if [[ "$rc" == 0 ]]; then echo 'Selected requirements found.'; fi
exit "$rc"
