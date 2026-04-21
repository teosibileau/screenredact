---
name: reassemble-video
description: Rebuild a video from the per-frame PNGs produced by `extract-frames` / `blur-frames`, preferring each frame's `_blurred.png` sibling when one exists and falling back to the original otherwise. Restores the source framerate, pix_fmt, and audio track. Use as the final step of the extract → detect → blur → reassemble pipeline.
---

# reassemble-video

Recompose a redacted video from the frames directory. For each source frame, the skill picks `<stem>_blurred.png` if `blur-frames` produced one and falls back to `<stem>.png` otherwise. Writes an `ffmpeg` concat list, then invokes `ffmpeg` to mux the frame sequence back into a playable file, re-attaching the source's audio track if one was extracted.

## Prerequisites

- The frames directory contains `source.json` (written by `extract-frames`). Without it, framerate / pix_fmt / audio metadata are unknown and reassembly is a guess — stop and route back to `extract-frames` if it's missing.
- Ideally `blur-frames` has run at least once. If there are no `*_blurred.png` files, the output will be a re-encoded copy of the source with nothing redacted — harmless, but usually not what the user wants. Warn before proceeding.

## Inputs

Ask the user for:

1. **Frames directory** (required) — the `./.screenredact/<video-basename>/` that `extract-frames` wrote to.
2. **Output path** (optional) — defaults to `<video-basename>_redacted.mp4` **in the same directory as the original source video** (read `input` from `source.json` and take its parent). Do *not* default to the current working directory — the agent may be invoked from anywhere. Accept an override here; this is the user-facing deliverable, not an internal artefact.

## Procedure

### 1. Sanity-check the inputs

```bash
test -f "<FRAMES_DIR>/source.json" || { echo "Missing source.json — run extract-frames first"; exit 1; }
ls "<FRAMES_DIR>"/frame_*.png >/dev/null 2>&1 || { echo "No frames in <FRAMES_DIR>"; exit 1; }
```

If no `*_blurred.png` files exist in `<FRAMES_DIR>`, surface that to the user and confirm they still want to proceed (the output will have nothing redacted).

### 2. Read metadata from `source.json`

```bash
FPS=$(jq -r '.avg_frame_rate // .frame_rate' "<FRAMES_DIR>/source.json")
PIX_FMT=$(jq -r '.pix_fmt' "<FRAMES_DIR>/source.json")
AUDIO_FILE=$(jq -r '.audio.file // ""' "<FRAMES_DIR>/source.json")
```

`FPS` will be the fraction form (e.g. `30000/1001`) — ffmpeg accepts that directly. The `.avg_frame_rate // .frame_rate` alternative exists for legacy sidecars written before `extract-frames` started storing the average rate in `frame_rate` directly; new sidecars have no `avg_frame_rate` field and the expression falls through to `.frame_rate`. `AUDIO_FILE` is an empty string when the source had no audio stream (jq's `//` on `.audio.file` coerces the null from `"audio": null` to `""`).

### 3. Write the concat list

Iterate frames in filename order. For each source frame, prefer its `_blurred.png` sibling; skip `*_blurred.png` itself as a candidate so it isn't listed twice.

```bash
cd "<FRAMES_DIR>"
{
  for f in frame_*.png; do
    case "$f" in *_blurred.png) continue;; esac
    stem="${f%.png}"
    if [ -f "${stem}_blurred.png" ]; then
      echo "file '${stem}_blurred.png'"
    else
      echo "file '$f'"
    fi
  done
} > concat.txt
```

The list is written into the per-video frames directory (where it's gitignored by the existing `.screenredact/` rule). Treat it as a debugging artefact — if the output video has frame-ordering issues, this file is the first place to look.

### 4. Reassemble with ffmpeg

Resume check first — skip the mux when the output already exists, unless the user explicitly wants a rebuild:

```bash
if [ -f "<OUTPUT>" ] && [ -z "$FORCE" ]; then
  echo "<OUTPUT> already exists; set FORCE=1 to rebuild" && exit 0
fi
```

**No audio** (source.json's `"audio"` is `null`):

```bash
ffmpeg -y -r "$FPS" -f concat -safe 0 -i "<FRAMES_DIR>/concat.txt" \
  -c:v libx264 -crf 18 -pix_fmt "$PIX_FMT" \
  "<OUTPUT>"
```

**With audio**:

```bash
ffmpeg -y -r "$FPS" -f concat -safe 0 -i "<FRAMES_DIR>/concat.txt" \
  -i "<FRAMES_DIR>/$AUDIO_FILE" \
  -c:v libx264 -crf 18 -pix_fmt "$PIX_FMT" \
  -c:a copy -shortest \
  "<OUTPUT>"
```

Flag notes:

- `-r "$FPS"` **before** `-i concat.txt` tells ffmpeg the concat demuxer is producing frames at this rate. One file = one frame, so this reconstructs the original timing one-for-one.
- `-safe 0` allows the concat list to reference files by name; without it, ffmpeg refuses entries it considers "unsafe".
- `-crf 18` is visually lossless H.264. Drop to `17` for extra headroom; raise to `20` if file size matters more than fidelity.
- `-c:a copy` re-muxes the extracted audio track without re-encoding — bit-exact round-trip.
- `-shortest` trims whichever input runs long. Video and audio durations should already match, but this is cheap insurance against a one-frame overrun.

### 5. Spot-check

```bash
open "<OUTPUT>"
```

Scrub through and confirm: the redacted regions stay blurred across frames, timing matches the source, audio (if present) is in sync.

## Report back

- **Output path** (absolute)
- **Frames consumed** — `wc -l "<FRAMES_DIR>/concat.txt"`; should equal `frame_count` from `source.json`
- **Audio preserved** — yes / no / n-a (no audio in source)
- **Duration check** — compare `ffprobe -v error -show_entries format=duration -of csv=p=0 "<OUTPUT>"` against the source's duration; they should match within a frame.

If counts or durations diverge meaningfully, dig in before calling it done — most likely a missing frame or a `_blurred.png` that got half-written in a prior interrupted run.

## Notes

- **The concat list lives in the per-video frames directory on purpose.** It's cheap to regenerate, it's gitignored along with everything else in `.screenredact/`, and keeping it makes the reassembly step reviewable after the fact.
- **Re-running is safe.** `concat.txt` is overwritten; ffmpeg will prompt before overwriting `<OUTPUT>` unless `-y` is added. Add `-y` only when driving the skill unattended.
- **The output is a re-encoded derivative, not a container-level round-trip of the source.** libx264 + CRF 18 is the default because it's widely playable and near-lossless. If the user needs the original codec or container preserved, that's a different task — surface it rather than silently switching encoders.
- **CFR only.** `extract-frames` writes exactly one PNG per source frame, but PNGs carry no timing — so the reassembly is inherently constant-framerate at the source's average rate. For true VFR sources (rare for screen recordings), tiny per-frame timing drift is unavoidable with this pipeline.
- **10-bit / HDR sources**: if `PIX_FMT` is `yuv420p10le` or similar, libx264 needs a 10-bit build to encode it. If ffmpeg errors out on pix_fmt, swap `libx264` for `libx265` — it handles 10-bit natively.
