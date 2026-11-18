#!/usr/bin/env python3
"""chunk_transcript.py — нарезать транскрипт на куски по границам ASR-сегментов.

Резать надо по сегментам, а не по строкам и не механически поровну: иначе кусок
начинается с середины слова, редактор достраивает смысл сам и выдумывает.

Использование:
    chunk_transcript.py <segments.json> <n-chunks> <out-dir> [--frames frames.json]

Вход — primary.segments.json от GigaAM (ключ "segments": [{start,end,text},...])
или whisper.json (ключ "transcription" с offsets в мс — тоже понимается).

Пишет <out-dir>/chunk-NN.txt и <out-dir>/chunks.json. При --frames в каждый
чанк подмешивается список кадров, попадающих в его временной диапазон, — их
редактор этого куска обязан просмотреть.
"""
import argparse
import json
import os


def load_segments(path):
    data = json.load(open(path, encoding="utf-8"))
    if isinstance(data, dict) and "segments" in data:          # GigaAM
        return [{"start": s["start"], "end": s["end"], "text": s["text"]}
                for s in data["segments"]]
    if isinstance(data, dict) and "transcription" in data:      # whisper.cpp -oj
        segs = []
        for s in data["transcription"]:
            off = s.get("offsets", {})
            segs.append({"start": off.get("from", 0) / 1000.0,
                         "end": off.get("to", 0) / 1000.0,
                         "text": s.get("text", "")})
        return segs
    raise SystemExit(f"не понимаю формат {path}: нет ни segments, ни transcription")


def hms(seconds):
    s = int(seconds)
    return f"{s//3600}:{s%3600//60:02d}:{s%60:02d}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("segments")
    ap.add_argument("n_chunks", type=int)
    ap.add_argument("out_dir")
    ap.add_argument("--frames", default=None)
    args = ap.parse_args()

    segs = load_segments(args.segments)
    if not segs:
        raise SystemExit("пустой транскрипт")
    n_chunks = max(1, args.n_chunks)
    os.makedirs(args.out_dir, exist_ok=True)

    frames = []
    if args.frames and os.path.exists(args.frames):
        frames = json.load(open(args.frames, encoding="utf-8"))

    total = segs[-1]["end"]
    target = total / n_chunks

    chunks, buf = [], []
    idx = 1
    start_t = segs[0]["start"]

    def flush(end_t):
        nonlocal buf, idx, start_t
        if not buf:
            return
        text = "\n\n".join(s["text"].strip() for s in buf if s["text"].strip())
        path = os.path.join(args.out_dir, f"chunk-{idx:02d}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        chunks.append({
            "chunk": idx,
            "path": os.path.abspath(path),
            "edited_path": os.path.abspath(
                os.path.join(args.out_dir, f"chunk-{idx:02d}.edited.md")),
            "start_sec": round(start_t, 2), "end_sec": round(end_t, 2),
            "start_hms": hms(start_t), "end_hms": hms(end_t),
            "n_segments": len(buf), "words": len(text.split()),
            "head": text[:200], "tail": text[-200:],
        })
        idx += 1
        start_t = end_t
        buf = []

    for s in segs:
        buf.append(s)
        if idx < n_chunks and s["end"] >= target * idx:
            flush(s["end"])
    flush(segs[-1]["end"])

    # Кадры раздаём после нарезки: фактических кусков может оказаться меньше
    # запрошенных (мало сегментов, один длинный сегмент через несколько границ),
    # и тогда «последним» надо считать реальный последний, а не N-й.
    # Первый кусок забирает всё до начала речи (заставка, титры с именами),
    # последний — всё после её конца.
    for pos, c in enumerate(chunks):
        lo = 0.0 if pos == 0 else c["start_sec"]
        hi = float("inf") if pos == len(chunks) - 1 else c["end_sec"]
        mine = [fr for fr in frames if lo <= fr["sec"] < hi]
        c["frames"] = [fr["path"] for fr in mine]
        c["frames_hms"] = [fr["hms"] for fr in mine]

    if frames:
        given = sum(len(c["frames"]) for c in chunks)
        if given != len(frames):
            print(f"⚠ роздано {given} кадров из {len(frames)} — часть потеряна")

    with open(os.path.join(args.out_dir, "chunks.json"), "w", encoding="utf-8") as f:
        json.dump(chunks, f, ensure_ascii=False, indent=2)

    print(f"{len(chunks)} чанков → {args.out_dir}")
    for c in chunks:
        fr = f", кадров {len(c['frames'])}" if frames else ""
        print(f"  chunk-{c['chunk']:02d}: {c['start_hms']}–{c['end_hms']}  "
              f"{c['words']} слов, сегментов {c['n_segments']}{fr}")


if __name__ == "__main__":
    main()
