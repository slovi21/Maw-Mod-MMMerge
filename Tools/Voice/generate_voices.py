#!/usr/bin/env python3
"""
Turn rows of the CSV from extract_texts.py into audio files named <key>.ogg,
which Scripts/General/zzzMaw_NPCVoices.lua plays in game.

TTS: Kokoro-82M (Apache-2.0) through kokoro-onnx (MIT), fully local.
Model files: kokoro-v1.0.onnx (or .int8.onnx) and voices-v1.0.bin from
https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0

    python generate_voices.py texts.csv --out ../../Data/Voices/NPC \
        --model kokoro-v1.0.onnx --voices voices-v1.0.bin --limit 50
    python generate_voices.py texts.csv --out ... --dry-run   # show the plan only

Each NPC keeps one voice for all of its lines (picked from the gender column,
deterministic by NPC id); a non-empty `voice` column overrides it. WAV comes
out of the model; with ffmpeg on PATH it is encoded to Ogg Vorbis, otherwise
the .wav is kept (the Lua side accepts both).
"""

import argparse
import csv
import shutil
import subprocess
import sys
import wave
from pathlib import Path

# Kokoro v1.0 English voices with the better quality grades in its VOICES.md.
# b* voices are British English, a* American.
VOICES = {
    "m": ["bm_george", "am_michael", "am_fenrir", "bm_fable", "am_puck", "bm_lewis"],
    "f": ["af_heart", "bf_emma", "af_bella", "bf_isabella", "af_nicole"],
}
VOICES["?"] = VOICES["m"]       # most named NPCs without a hint are male
VOICES["mixed"] = VOICES["m"]


def pick_voice(row, overrides) -> str:
    if row.get("voice", "").strip():
        return row["voice"].strip()
    ids = row.get("npc_ids", "").split()
    for npc in ids:
        if npc in overrides:
            return overrides[npc]
    pool = VOICES.get(row.get("gender", "?"), VOICES["?"])
    seed = int(ids[0]) if ids else int(row["key"], 16)
    return pool[seed % len(pool)]


def lang_of(voice: str) -> str:
    return "en-gb" if voice.startswith("b") else "en-us"


def selected_rows(rows, args):
    out = []
    for row in rows:
        if args.kind != "all" and row["kind"] != args.kind:
            continue
        if args.npc and not set(args.npc) & set(row["npc_ids"].split()):
            continue
        if "%" in row["text"]:  # filled in at run time (%s, %d): can never match a fixed recording
            continue
        out.append(row)
        if args.limit and len(out) >= args.limit:
            break
    return out


def write_wav(path: Path, samples, rate: int):
    import numpy as np
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


def encode_ogg(wav: Path, ogg: Path, ffmpeg: str) -> bool:
    r = subprocess.run([ffmpeg, "-v", "error", "-y", "-i", str(wav), "-c:a", "libvorbis", "-q:a", "4", str(ogg)],
                       capture_output=True)
    return r.returncode == 0 and ogg.exists()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", type=Path, help="output of extract_texts.py")
    ap.add_argument("--out", required=True, type=Path, help="usually <MM8>/Data/Voices/NPC")
    ap.add_argument("--model", type=Path, help="kokoro-v1.0.onnx or kokoro-v1.0.int8.onnx")
    ap.add_argument("--voices", type=Path, help="voices-v1.0.bin")
    ap.add_argument("--kind", choices=["greeting", "topic", "all"], default="greeting")
    ap.add_argument("--npc", nargs="*", help="only rows spoken by these NPC ids")
    ap.add_argument("--override", nargs="*", default=[], metavar="NPC=VOICE",
                    help="fixed voice for an NPC id, e.g. 30=af_heart (the gender guess misses many names)")
    ap.add_argument("--limit", type=int, default=0, help="stop after this many rows (0 = all)")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--force", action="store_true", help="regenerate files that already exist")
    ap.add_argument("--dry-run", action="store_true", help="print the plan, synthesize nothing")
    args = ap.parse_args(argv)

    with args.csv.open(encoding="utf-8", newline="") as f:
        rows = selected_rows(list(csv.DictReader(f)), args)
    overrides = dict(o.split("=", 1) for o in args.override)
    plan = [(row, pick_voice(row, overrides)) for row in rows]

    if args.dry_run:
        for row, voice in plan:
            print(f"{row['key']}  {voice:12} {row['source']:28} {row['npc_names'][:24]:24} {row['text'][:60]}")
        print(f"{len(plan)} rows, {sum(len(r['text']) for r, _ in plan)} characters", file=sys.stderr)
        return

    if not (args.model and args.voices):
        ap.error("--model and --voices are required unless --dry-run")
    from kokoro_onnx import Kokoro
    tts = Kokoro(str(args.model), str(args.voices))
    ffmpeg = shutil.which("ffmpeg")
    args.out.mkdir(parents=True, exist_ok=True)

    manifest = args.out / "voices.csv"
    new_manifest = not manifest.exists()
    seconds = 0.0
    with manifest.open("a", encoding="utf-8", newline="") as mf:
        mw = csv.writer(mf)
        if new_manifest:
            mw.writerow(["key", "file", "voice", "seconds", "source", "npc_names", "text"])
        for n, (row, voice) in enumerate(plan, 1):
            key = row["key"]
            if not args.force and any((args.out / f"{key}{ext}").exists() for ext in (".ogg", ".wav")):
                continue
            samples, rate = tts.create(row["text"], voice=voice, speed=args.speed, lang=lang_of(voice))
            wav = args.out / f"{key}.wav"
            write_wav(wav, samples, rate)
            final = wav
            if ffmpeg and encode_ogg(wav, args.out / f"{key}.ogg", ffmpeg):
                wav.unlink()
                final = args.out / f"{key}.ogg"
            length = len(samples) / rate
            seconds += length
            mw.writerow([key, final.name, voice, f"{length:.1f}", row["source"], row["npc_names"], row["text"]])
            print(f"[{n}/{len(plan)}] {final.name} {voice} {length:.1f}s  {row['text'][:50]}", file=sys.stderr)
    print(f"done, {seconds / 60:.1f} min of audio in {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
