---
name: extract-frames
description: Extract every frame of a video as lossless PNGs using ffmpeg, preserving the metadata needed to rebuild the video later (source fps, resolution, audio track). Use when the user wants to decompose a video into per-frame images — typically as step one of a process-then-reassemble workflow.
---

# extract-frames

Decompose a video into per-frame lossless PNGs plus a sidecar of metadata needed for reassembly.

## Inputs

Ask the user for:

1. **Input video path** (required)

The output directory is always `./.screenredact/<video-basename>/` (hidden `.screenredact/` parent at the CWD root, then a per-video subdirectory named after the input file's stem — e.g. `demo.mov` → `./.screenredact/demo/`). This is fixed — do not accept an override from the user, even if they ask. The parent is shared across videos so repeated runs land side by side; the per-video subdirectory isolates each run's frames, sidecars, and audio. Downstream tooling relies on this convention.

## Procedure

### 1. Probe the source

Capture fps, resolution, and whether an audio track exists. These are needed to reconstruct the video with the same timing and sound.

```bash
ffprobe -v error -print_format json -show_streams -show_format "<INPUT>"
```

Extract from the JSON:

- Video stream: `avg_frame_rate` (e.g. `30000/1001`), `width`, `height`, `pix_fmt`
- Audio stream (if any): `codec_name`, `sample_rate`, `channels`

Use `avg_frame_rate`, **not** `r_frame_rate`. For CFR broadcast-style sources they match, but screen recordings are typically VFR: `r_frame_rate` reports the stream's timebase max (often 100 or 1000 fps) while `avg_frame_rate` is the actual average. Reassembling at `r_frame_rate` produces a visibly fast, out-of-sync output.

### 2. Create the output directory

```bash
mkdir -p "<OUT>"
```

### 3. Extract every frame

Resume check first — if `source.json` exists and its `frame_count` matches the PNGs already on disk, the extract was done by a prior run and we skip ffmpeg:

```bash
if [ -f "<OUT>/source.json" ]; then
  # Count only the originals — exclude any *_blurred.png siblings that a
  # later `blur-frames` pass may have written into the same directory.
  have=$(find "<OUT>" -maxdepth 1 -name 'frame_*.png' -not -name '*_blurred.png' | wc -l | tr -d ' ')
  want=$(jq -r '.frame_count' "<OUT>/source.json")
  [ "$have" = "$want" ] && echo "already extracted ($have frames); skipping" && exit 0
fi
```

Otherwise extract:

```bash
ffmpeg -i "<INPUT>" \
  -fps_mode passthrough \
  -compression_level 1 \
  "<OUT>/frame_%06d.png"
```

- `-fps_mode passthrough` preserves the exact frame sequence — no duplicates inserted, no frames dropped. This is critical for round-trip integrity.
- `-compression_level 1` — PNG is lossless regardless; this only trades encode speed vs. file size. `1` is fast; bump to `9` only if disk space matters more than time.
- `%06d` handles up to ~1M frames. Use `%07d` for longer videos.
- Partial extracts (a Ctrl-C'd run) are rare since the full extract is ~5 s for thousands of frames; the resume check above mostly prevents needless re-extracts of a complete directory.

### 4. Extract the audio track (if present)

Copy the audio stream without re-encoding so it stays bit-exact:

```bash
ffmpeg -i "<INPUT>" -vn -acodec copy "<OUT>/audio.<EXT>"
```

Use the source's native extension (e.g. `.aac`, `.m4a`, `.opus`) based on `codec_name` from the probe. If there's no audio stream, skip this step and note it in the sidecar.

### 5. Write a sidecar manifest

Save `<OUT>/source.json` so a future reassembly step knows how to rebuild:

```json
{
  "input": "<absolute path to original video>",
  "frame_rate": "30000/1001",
  "width": 1920,
  "height": 1080,
  "pix_fmt": "yuv420p",
  "frame_count": 1234,
  "audio": {
    "file": "audio.aac",
    "codec": "aac",
    "sample_rate": 48000,
    "channels": 2
  }
}
```

Set `"audio": null` if the source has no audio track. `frame_count` should be the actual count of PNG files written, not the probe's estimate — they can differ on variable-frame-rate sources. `frame_rate` must hold `avg_frame_rate` from the probe (see step 1) — downstream reassembly reads this field verbatim and uses it as the output rate.

## Report back

- Output directory (absolute path)
- Frame count
- Source fps (as the fraction, e.g. `30000/1001 ≈ 29.97`)
- Whether audio was extracted

## Notes

- **Disk usage warning**: lossless PNG at 1080p is ~2–5 MB per frame. A 60-second 30fps video = 1800 frames ≈ 5–10 GB. Warn the user up front and offer to check available disk space with `df -h .` if the source is long.
- **Don't re-encode the video stream** at any step. The whole point is a lossless round-trip.
- **Quote all paths** — video filenames often contain spaces or special characters.
- For HDR or 10-bit sources, PNG is still fine (it supports 16-bit), but note the `pix_fmt` in the sidecar so reassembly can preserve it.
