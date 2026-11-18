#!/usr/bin/env python3
"""Offline long-form GigaAM-v3 e2e RNNT with overlap and resumability.

The script refuses to start without local model assets, keeps a run identity
manifest, splits audio at low-energy points, pads segment boundaries with
context, assigns each word to exactly one core interval, and writes raw plus
owned words to a JSONL checkpoint.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

import gigaam
import numpy as np
import torch
from gigaam.preprocess import SAMPLE_RATE, load_audio
from gigaam.utils import AudioDataset
from torch.utils.data import DataLoader


SCRIPT_VERSION = "1.0.0"
MODEL_NAME = "v3_e2e_rnnt"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def choose_device(requested: str) -> str:
    if requested != "auto":
        return requested
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def repository_commit() -> str:
    repository = Path(gigaam.__file__).resolve().parent.parent
    try:
        result = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def segment_waveform(
    audio: torch.Tensor,
    *,
    ideal_seconds: float,
    min_seconds: float,
    max_seconds: float,
    overlap_seconds: float,
) -> list[dict[str, int]]:
    """Split at quiet points and add context without changing core ownership."""
    frame_samples = max(1, int(0.02 * SAMPLE_RATE))
    usable = (audio.numel() // frame_samples) * frame_samples
    if usable == 0:
        raise ValueError("Audio is shorter than one 20 ms analysis frame")
    frames = audio[:usable].numpy().reshape(-1, frame_samples)
    rms = np.sqrt(np.mean(np.square(frames, dtype=np.float32), axis=1) + 1e-12)

    kernel = np.ones(11, dtype=np.float32) / 11
    smooth = np.convolve(rms, kernel, mode="same")
    total_samples = audio.numel()
    min_samples = int(min_seconds * SAMPLE_RATE)
    ideal_samples = int(ideal_seconds * SAMPLE_RATE)
    max_samples = int(max_seconds * SAMPLE_RATE)
    overlap_samples = int(overlap_seconds * SAMPLE_RATE)
    boundaries = [0]

    start = 0
    while total_samples - start > max_samples:
        search_start = start + min_samples
        search_end = min(start + max_samples, total_samples)
        first_frame = max(0, search_start // frame_samples)
        last_frame = min(len(smooth), math.ceil(search_end / frame_samples))
        window = smooth[first_frame:last_frame]

        if window.size == 0:
            cut = min(start + ideal_samples, total_samples)
        else:
            low = float(window.min())
            scale = max(float(np.percentile(window, 90)) - low, 1e-6)
            energy_score = (window - low) / scale
            candidate_samples = (
                np.arange(first_frame, last_frame, dtype=np.float64)
                * frame_samples
            )
            distance_score = (
                np.abs(candidate_samples - (start + ideal_samples))
                / max(max_samples - ideal_samples, SAMPLE_RATE)
            )
            score = energy_score + 0.12 * distance_score
            cut = (first_frame + int(np.argmin(score))) * frame_samples

        cut = max(cut, start + min_samples)
        cut = min(cut, start + max_samples, total_samples)
        boundaries.append(cut)
        start = cut

    if boundaries[-1] != total_samples:
        boundaries.append(total_samples)

    core_segments = list(zip(boundaries[:-1], boundaries[1:]))
    if (
        len(core_segments) >= 2
        and core_segments[-1][1] - core_segments[-1][0] < 5 * SAMPLE_RATE
        and core_segments[-1][1] - core_segments[-2][0] <= max_samples
    ):
        core_segments[-2] = (core_segments[-2][0], core_segments[-1][1])
        core_segments.pop()

    segments: list[dict[str, int]] = []
    for core_start, core_end in core_segments:
        segments.append(
            {
                "core_start": core_start,
                "core_end": core_end,
                "input_start": max(0, core_start - overlap_samples),
                "input_end": min(total_samples, core_end + overlap_samples),
            }
        )
    return segments


def seconds(value: int) -> float:
    return round(value / SAMPLE_RATE, 3)


def segment_boundaries(segment: dict[str, int]) -> dict[str, float]:
    return {
        "start": seconds(segment["core_start"]),
        "end": seconds(segment["core_end"]),
        "input_start": seconds(segment["input_start"]),
        "input_end": seconds(segment["input_end"]),
    }


def load_checkpoint(
    path: Path, expected: list[dict[str, float]]
) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    records: list[dict[str, Any]] = []
    truncated_tail = False
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            if line_number == len(lines):
                truncated_tail = True
                break
            raise RuntimeError(
                f"Invalid checkpoint JSON at line {line_number}: {error}"
            ) from error
        records.append(record)

    if truncated_tail:
        payload = "".join(
            json.dumps(item, ensure_ascii=False) + "\n" for item in records
        )
        atomic_write_text(path, payload)
        print("Ignored and removed one truncated checkpoint tail record", flush=True)

    if len(records) > len(expected):
        raise RuntimeError("Checkpoint has more records than current segmentation")
    for index, record in enumerate(records):
        for key in ("start", "end", "input_start", "input_end"):
            if abs(float(record[key]) - expected[index][key]) > 0.01:
                raise RuntimeError(
                    f"Checkpoint boundary mismatch at segment {index}, key {key}"
                )
    return records


def join_words(tokens: list[str]) -> str:
    text = " ".join(token.strip() for token in tokens if token.strip())
    text = re.sub(r"\s+([,.;:!?%…)\]}»])", r"\1", text)
    text = re.sub(r"([(\[{«])\s+", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def own_words(
    words: list[Any],
    *,
    input_start: float,
    core_start: float,
    core_end: float,
    segment_index: int,
    segment_count: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    raw_words: list[dict[str, Any]] = []
    owned_words: list[dict[str, Any]] = []
    for word in words or []:
        shifted = {
            "text": str(word.text),
            "start": round(float(word.start) + input_start, 3),
            "end": round(float(word.end) + input_start, 3),
        }
        raw_words.append(shifted)
        midpoint = (shifted["start"] + shifted["end"]) / 2
        if segment_index > 0 and midpoint < core_start:
            continue
        if segment_index < segment_count - 1 and midpoint >= core_end:
            continue
        owned_words.append(shifted)
    return raw_words, owned_words


def srt_timestamp(value: float) -> str:
    millis = max(0, round(value * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def make_srt(records: list[dict[str, Any]]) -> str:
    cues: list[tuple[float, float, str]] = []
    for record in records:
        words = record.get("words") or []
        if not words:
            text = str(record.get("text", "")).strip()
            if text:
                cues.append((float(record["start"]), float(record["end"]), text))
            continue
        group: list[dict[str, Any]] = []
        for word in words:
            group.append(word)
            text = join_words([str(item["text"]) for item in group])
            duration = float(group[-1]["end"]) - float(group[0]["start"])
            sentence_end = bool(re.search(r"[.!?…][»”\"]?$", text))
            if len(text) >= 78 or duration >= 5.2 or (sentence_end and len(text) >= 24):
                cues.append((float(group[0]["start"]), float(group[-1]["end"]), text))
                group = []
        if group:
            cues.append(
                (
                    float(group[0]["start"]),
                    float(group[-1]["end"]),
                    join_words([str(item["text"]) for item in group]),
                )
            )

    blocks = []
    for index, (start, end, text) in enumerate(cues, start=1):
        blocks.append(
            f"{index}\n{srt_timestamp(start)} --> {srt_timestamp(max(end, start + 0.15))}\n{text}"
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def output_paths(prefix: Path) -> list[Path]:
    return [
        prefix.with_suffix(".txt"),
        prefix.with_suffix(".srt"),
        prefix.with_suffix(".segments.json"),
        prefix.with_suffix(".checkpoint.jsonl"),
        prefix.with_suffix(".run.json"),
    ]


def prepare_run_identity(
    args: argparse.Namespace,
    *,
    audio_path: Path,
    cache_dir: Path,
    checkpoint_path: Path,
    tokenizer_path: Path,
    device: str,
) -> dict[str, Any]:
    audio_stat = audio_path.stat()
    return {
        "schema": 1,
        "script_version": SCRIPT_VERSION,
        "audio": {
            "path": str(audio_path),
            "size": audio_stat.st_size,
            "mtime_ns": audio_stat.st_mtime_ns,
            "sha256": sha256_file(audio_path),
        },
        "model": {
            "name": MODEL_NAME,
            "cache_dir": str(cache_dir),
            "checkpoint": str(checkpoint_path),
            "checkpoint_sha256": sha256_file(checkpoint_path),
            "tokenizer": str(tokenizer_path),
            "tokenizer_sha256": sha256_file(tokenizer_path),
            "gigaam_repo_commit": repository_commit(),
            "torch": torch.__version__,
        },
        "runtime": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "device": device,
            "fp16_encoder": device != "cpu",
        },
        "segmentation": {
            "sample_rate": SAMPLE_RATE,
            "ideal_seconds": args.ideal_seconds,
            "min_seconds": args.min_seconds,
            "max_seconds": args.max_seconds,
            "overlap_seconds": args.overlap_seconds,
        },
    }


def ensure_run_manifest(
    path: Path, identity: dict[str, Any], *, restart: bool
) -> dict[str, Any]:
    if path.exists() and not restart:
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing.get("identity") != identity:
            raise RuntimeError(
                "Run identity differs from existing manifest; use a new prefix or explicit --restart"
            )
        return existing
    payload = {"created_at": utc_now(), "identity": identity, "attempts": []}
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return payload


def write_outputs(
    prefix: Path,
    records: list[dict[str, Any]],
    *,
    run_manifest: dict[str, Any],
    elapsed_seconds: float,
) -> None:
    transcript = "\n".join(
        str(record.get("text", "")).strip()
        for record in records
        if str(record.get("text", "")).strip()
    )
    atomic_write_text(prefix.with_suffix(".txt"), transcript + ("\n" if transcript else ""))
    atomic_write_text(prefix.with_suffix(".srt"), make_srt(records))
    payload = {
        "model": "GigaAM-v3 e2e RNNT",
        "model_name": MODEL_NAME,
        "run_identity": run_manifest["identity"],
        "elapsed_seconds_this_attempt": round(elapsed_seconds, 3),
        "empty_segments": sum(not str(item.get("text", "")).strip() for item in records),
        "segments": records,
    }
    atomic_write_text(
        prefix.with_suffix(".segments.json"),
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("audio", type=Path)
    parser.add_argument("--out-prefix", type=Path, required=True)
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path(os.environ.get("GIGAAM_CACHE", str(Path.home() / ".cache" / "gigaam"))),
    )
    parser.add_argument("--device", choices=("auto", "mps", "cpu", "cuda"), default="auto")
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--ideal-seconds", type=float, default=18.0)
    parser.add_argument("--min-seconds", type=float, default=13.0)
    parser.add_argument("--max-seconds", type=float, default=22.0)
    parser.add_argument("--overlap-seconds", type=float, default=0.75)
    parser.add_argument("--restart", action="store_true")
    args = parser.parse_args()

    audio_path = args.audio.expanduser().resolve()
    prefix = args.out_prefix.expanduser().resolve()
    cache_dir = args.cache_dir.expanduser().resolve()
    checkpoint_model = cache_dir / f"{MODEL_NAME}.ckpt"
    tokenizer_model = cache_dir / f"{MODEL_NAME}_tokenizer.model"
    device = choose_device(args.device)

    if not audio_path.is_file():
        parser.error(f"Audio file not found: {audio_path}")
    if not checkpoint_model.is_file() or not tokenizer_model.is_file():
        parser.error(
            "Exact local v3_e2e_rnnt checkpoint/tokenizer missing; refusing auto-download"
        )
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")
    if not (0 < args.min_seconds < args.ideal_seconds < args.max_seconds):
        parser.error("Expected 0 < min < ideal < max")
    if args.overlap_seconds < 0:
        parser.error("--overlap-seconds must be non-negative")
    if args.max_seconds + 2 * args.overlap_seconds >= 25:
        parser.error("max segment plus two overlaps must remain below 25 seconds")

    prefix.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path = prefix.with_suffix(".checkpoint.jsonl")
    run_path = prefix.with_suffix(".run.json")
    if args.restart:
        for path in output_paths(prefix):
            path.unlink(missing_ok=True)
    elif checkpoint_path.exists() and not run_path.exists():
        raise RuntimeError(
            "Checkpoint exists without a run identity manifest; use a new prefix or explicit --restart"
        )

    identity = prepare_run_identity(
        args,
        audio_path=audio_path,
        cache_dir=cache_dir,
        checkpoint_path=checkpoint_model,
        tokenizer_path=tokenizer_model,
        device=device,
    )
    run_manifest = ensure_run_manifest(run_path, identity, restart=args.restart)

    started = time.monotonic()
    print(f"Loading audio: {audio_path}", flush=True)
    audio = load_audio(str(audio_path)).contiguous()
    if audio.numel() < int(0.02 * SAMPLE_RATE):
        parser.error("Audio is empty or shorter than 20 ms")
    duration = audio.numel() / SAMPLE_RATE
    segments = segment_waveform(
        audio,
        ideal_seconds=args.ideal_seconds,
        min_seconds=args.min_seconds,
        max_seconds=args.max_seconds,
        overlap_seconds=args.overlap_seconds,
    )
    boundaries = [segment_boundaries(item) for item in segments]
    print(f"Audio {duration / 60:.2f} min; {len(segments)} segments", flush=True)

    records = load_checkpoint(checkpoint_path, boundaries)
    resume_at = len(records)
    if resume_at:
        print(f"Resuming after {resume_at}/{len(segments)} segments", flush=True)
    if resume_at == len(segments):
        write_outputs(
            prefix,
            records,
            run_manifest=run_manifest,
            elapsed_seconds=time.monotonic() - started,
        )
        print("Checkpoint complete; outputs regenerated", flush=True)
        return

    print(f"Loading {MODEL_NAME} on {device}", flush=True)
    model = gigaam.load_model(
        MODEL_NAME,
        device=device,
        fp16_encoder=device != "cpu",
        use_flash=False,
        download_root=str(cache_dir),
    )

    remaining = [
        audio[item["input_start"] : item["input_end"]]
        for item in segments[resume_at:]
    ]
    dataset = AudioDataset(remaining, tokenizer=None)
    loader = DataLoader(
        dataset,
        batch_size=args.batch_size,
        shuffle=False,
        collate_fn=AudioDataset.collate,
        num_workers=0,
    )

    checkpoint_handle = checkpoint_path.open("a", encoding="utf-8")
    processed = resume_at
    try:
        with torch.inference_mode():
            for wav_pad, wav_lens in loader:
                wav_pad = wav_pad.to(model._device).to(model._dtype)
                wav_lens = wav_lens.to(model._device)
                encoded, encoded_lens = model.forward(wav_pad, wav_lens)
                decoded = model._decode(
                    encoded, encoded_lens, wav_lens, word_timestamps=True
                )
                for raw_text, decoded_words in decoded:
                    boundary = boundaries[processed]
                    raw_words, owned_words = own_words(
                        decoded_words or [],
                        input_start=boundary["input_start"],
                        core_start=boundary["start"],
                        core_end=boundary["end"],
                        segment_index=processed,
                        segment_count=len(segments),
                    )
                    owned_text = join_words([item["text"] for item in owned_words])
                    ownership_warning = None
                    if not owned_words and str(raw_text).strip():
                        owned_text = str(raw_text).strip()
                        ownership_warning = "raw text had no word timestamps; overlap dedup unavailable"
                    record = {
                        "index": processed,
                        **boundary,
                        "raw_text": str(raw_text).strip(),
                        "text": owned_text,
                        "raw_words": raw_words,
                        "words": owned_words,
                        "ownership_warning": ownership_warning,
                    }
                    checkpoint_handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                    records.append(record)
                    processed += 1
                checkpoint_handle.flush()
                os.fsync(checkpoint_handle.fileno())
                elapsed = time.monotonic() - started
                covered = boundaries[processed - 1]["end"]
                rtf = elapsed / covered if covered else 0.0
                print(
                    f"Progress {processed}/{len(segments)} "
                    f"({covered / 60:.1f}/{duration / 60:.1f} min), "
                    f"elapsed {elapsed / 60:.1f} min, RTF {rtf:.3f}",
                    flush=True,
                )
    finally:
        checkpoint_handle.close()

    elapsed = time.monotonic() - started
    write_outputs(prefix, records, run_manifest=run_manifest, elapsed_seconds=elapsed)
    run_manifest.setdefault("attempts", []).append(
        {
            "completed_at": utc_now(),
            "elapsed_seconds": round(elapsed, 3),
            "segment_count": len(records),
        }
    )
    atomic_write_text(run_path, json.dumps(run_manifest, ensure_ascii=False, indent=2) + "\n")
    print(f"TXT: {prefix.with_suffix('.txt')}", flush=True)
    print(f"SRT: {prefix.with_suffix('.srt')}", flush=True)
    print(f"JSON: {prefix.with_suffix('.segments.json')}", flush=True)


if __name__ == "__main__":
    main()
