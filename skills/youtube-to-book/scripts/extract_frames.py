#!/usr/bin/env python3
"""extract_frames.py — плотная сетка кадров из видео для визуальной сверки.

Кадры нужны редакторам, чтобы читать то, чего нет в звуке: имена в титрах,
цифры на слайдах, названия на экране, код в скринкасте. ASR систематически
коверкает термины, которые на экране написаны буквально.

Использование:
    extract_frames.py <video.mp4> <out-dir> [--interval 60] [--mode hybrid]
                      [--scene-threshold 0.35] [--width 960] [--max-frames 400]

Режимы:
    uniform  — равномерная сетка через --interval секунд
    scene    — только смены сцены (слайды, монтажные склейки)
    hybrid   — сетка + сцены, дубли ближе 8 с схлопываются (по умолчанию)

Пишет <out-dir>/frame-HHMMSS.jpg и <out-dir>/frames.json.
"""
import argparse
import json
import os
import subprocess
import sys


def probe_duration(video):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video],
        capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def hms(seconds):
    s = int(seconds)
    return f"{s//3600:02d}{s%3600//60:02d}{s%60:02d}"


def label(seconds):
    s = int(seconds)
    return f"{s//3600}:{s%3600//60:02d}:{s%60:02d}"


def scene_timestamps(video, threshold):
    """Времена смены сцены по фильтру ffmpeg select=gt(scene,...)."""
    proc = subprocess.run(
        ["ffmpeg", "-i", video, "-filter:v",
         f"select='gt(scene,{threshold})',showinfo", "-f", "null", "-"],
        capture_output=True, text=True)
    stamps = []
    for line in proc.stderr.splitlines():
        if "pts_time:" in line:
            try:
                stamps.append(float(line.split("pts_time:")[1].split()[0]))
            except (IndexError, ValueError):
                continue
    return stamps


def grab(video, seconds, out_path, width):
    """Один кадр. -ss до -i = быстрый seek по ключевым кадрам."""
    subprocess.run(
        ["ffmpeg", "-y", "-ss", str(seconds), "-i", video,
         "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "3",
         out_path, "-loglevel", "error"],
        check=False)
    return os.path.exists(out_path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("out_dir")
    ap.add_argument("--interval", type=float, default=60.0)
    ap.add_argument("--mode", choices=["uniform", "scene", "hybrid"], default="hybrid")
    ap.add_argument("--scene-threshold", type=float, default=0.35)
    ap.add_argument("--width", type=int, default=960)
    ap.add_argument("--max-frames", type=int, default=400)
    args = ap.parse_args()

    if args.interval <= 0 or args.width <= 0 or args.max_frames < 1:
        ap.error("interval, width and max-frames must be positive")
    if not os.path.exists(args.video):
        sys.exit(f"нет файла: {args.video}")
    os.makedirs(args.out_dir, exist_ok=True)

    duration = probe_duration(args.video)
    stamps = []

    if args.mode in ("uniform", "hybrid"):
        t = args.interval / 2  # первый кадр в середине первого окна, не на чёрной заставке
        while t < duration:
            stamps.append(t)
            t += args.interval

    if args.mode in ("scene", "hybrid"):
        scenes = scene_timestamps(args.video, args.scene_threshold)
        print(f"смен сцены найдено: {len(scenes)}")
        stamps.extend(scenes)

    # схлопнуть близкие: две картинки в пределах 8 с почти всегда одинаковы
    stamps.sort()
    deduped = []
    for t in stamps:
        if not deduped or t - deduped[-1] >= 8.0:
            deduped.append(t)

    # если сцен оказалось слишком много (клиповый монтаж) — прорядить равномерно
    if len(deduped) > args.max_frames:
        step = len(deduped) / args.max_frames
        deduped = [deduped[int(i * step)] for i in range(args.max_frames)]
        print(f"прорежено до {len(deduped)} кадров (лимит --max-frames)")

    if not deduped and duration > 0:
        deduped.append(duration / 2)
    frames = []
    for t in deduped:
        name = f"frame-{hms(t)}.jpg"
        path = os.path.join(args.out_dir, name)
        if grab(args.video, t, path, args.width):
            frames.append({"sec": round(t, 2), "hms": label(t), "path": os.path.abspath(path)})

    if not frames:
        sys.exit("No frames decoded; inspect video and ffmpeg output")

    with open(os.path.join(args.out_dir, "frames.json"), "w", encoding="utf-8") as f:
        json.dump(frames, f, ensure_ascii=False, indent=2)

    print(f"{len(frames)} кадров → {args.out_dir}")
    print(f"плотность: 1 кадр / {duration/max(1,len(frames)):.0f} с видео")


if __name__ == "__main__":
    main()
