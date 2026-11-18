#!/usr/bin/env bash
# Produce local timestamped text for the base and companion skills.
set -eu
audio=${1:?usage: transcribe.sh AUDIO OUTPUT_DIRECTORY [LANGUAGE]}
dest=${2:?usage: transcribe.sh AUDIO OUTPUT_DIRECTORY [LANGUAGE]}
lang_code=${3:-ru}
lang_code="$(printf '%s' "${lang_code%%-*}" | tr '[:upper:]' '[:lower:]')"
[[ "$lang_code" =~ ^[a-z]{2,3}$ && -s "$audio" ]] || { echo 'Check audio and language code' >&2; exit 2; }
engine=${ASR_ENGINE:-auto}
if [[ "$engine" == auto ]]; then
  if [[ "$lang_code" == ru ]]; then engine=gigaam; else engine=whisper; fi
fi
case "$engine" in
  gigaam)
    [[ "$lang_code" == ru ]] || { echo 'GigaAM requires Russian audio' >&2; exit 2; }
    script_dir="$(cd "$(dirname "$0")" && pwd)"
    mkdir -p "$dest/gigaam"
    "${GIGAAM_PY:-python3}" "$script_dir/gigaam_longform.py" "$audio" \
      --out-prefix "$dest/gigaam/primary" --device "${GIGAAM_DEVICE:-auto}" --overlap-seconds 0.75
    [[ -s "$dest/gigaam/primary.txt" && -s "$dest/gigaam/primary.segments.json" ]] ;;
  whisper)
    : "${WHISPER_MODEL:?Point WHISPER_MODEL to a local whisper.cpp model}"
    [[ -s "$WHISPER_MODEL" ]] || { echo 'Missing whisper.cpp model' >&2; exit 1; }
    mkdir -p "$dest"
    out="$dest/whisper"
    [[ ! -e "$out" ]] || { echo 'Choose a new output directory' >&2; exit 1; }
    tmp_dir="$(mktemp -d)"
    trap 'rm -rf "$tmp_dir"' EXIT
    ffmpeg -nostdin -loglevel error -i "$audio" -ar 16000 -ac 1 -c:a pcm_s16le "$tmp_dir/input.wav"
    args=(-m "$WHISPER_MODEL" -f "$tmp_dir/input.wav" -l "$lang_code" -otxt -osrt -oj -of "$tmp_dir/primary")
    if [[ -n "${VAD_MODEL:-}" ]]; then args+=(--vad --vad-model "$VAD_MODEL"); fi
    "${WHISPER_BIN:-whisper-cli}" "${args[@]}"
    for ext in txt srt json; do [[ -s "$tmp_dir/primary.$ext" ]] || { echo "Missing $ext transcript" >&2; exit 1; }; done
    mkdir "$out"
    cp "$tmp_dir"/primary.{txt,srt,json} "$out/" ;;
  *) echo "Unknown ASR_ENGINE: $engine" >&2; exit 2 ;;
esac
