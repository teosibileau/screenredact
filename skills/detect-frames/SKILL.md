---
name: detect-frames
description: Run the screenredact detection pipeline over a directory of extracted video frames, producing one JSON sidecar per frame that contains PII (emails, phone numbers, credit cards, addresses). Use after the extract-frames skill has decomposed a video into PNGs, and before any redaction step — this is the "find what to blur" phase. macOS-only: OCR runs through Apple's Vision framework.
---

# detect-frames

Scan a directory of video frames with OCR + PII detection (Apple Vision → Presidio) and write per-frame JSON sidecars for every frame that contains sensitive data.

## Inputs

Ask the user for:

1. **Frames directory** (required) — conventionally `./.<video-basename>_frames/` produced by the `extract-frames` skill. If the user hasn't run `extract-frames` first, stop and tell them to do that.

Sidecars are always written back into the same frames directory. This is fixed — do not accept an override from the user, even if they ask. Downstream redaction tooling relies on the convention that a frame and its detection sidecar live side by side.

## Procedure

### 1. Sanity-check the input

Verify the directory exists and contains PNG frames.

```bash
ls "<FRAMES_DIR>"/*.png | head -1
```

If the glob matches nothing, abort with an error — there is nothing to detect on.

### 2. Run detection

```bash
poetry run screenredact detect "<FRAMES_DIR>"
```

This is the project's own CLI (registered as `screenredact = "screenredact.cli:app"` in `pyproject.toml`). It:

- Loads Presidio once per process (class-level cache in `screenredact.detector.FrameAnalyzer`); Apple Vision is a framework service with no model-load step
- Iterates frames in sorted order
- For each frame: OCRs it through Apple Vision (`ocrmac.OCR(...).recognize()`), runs Presidio over each recognized line, collects any hits as `Detection` records
- Writes a sidecar JSON **only when the frame has ≥1 detection** — clean frames produce no file

Entity types detected: `EMAIL_ADDRESS`, `PHONE_NUMBER`, `CREDIT_CARD` (Luhn-validated by Presidio), `LOCATION` (address proxy via spaCy NER).

### 3. Sidecar format

One JSON file per frame-with-detections, named `<frame_stem>.json` next to the PNG:

```json
{
  "frame": "frame_000501.png",
  "detections": [
    {
      "type": "EMAIL_ADDRESS",
      "text": "teo.sibileau@gmail.com",
      "line_text": "teo.sibileau@gmail.com's Organization",
      "bbox": [[62, 131], [381, 132], [381, 152], [62, 151]],
      "ocr_confidence": 0.9986,
      "pii_score": 1.0
    }
  ]
}
```

- `bbox` is the OCR *line* polygon (4 points, clockwise from top-left). Line-level — not per-character — so when you redact you'll blur the whole line. That's the safer default for a redaction tool.
- `text` is the substring Presidio flagged. `line_text` is the full OCR line it came from; useful for debugging and for widening the redaction if you want context.

## Report back

### 1. Generate the roll-up

Run the project's own `report` subcommand — it scans every sidecar in the frames directory and writes `report.json` alongside them, while echoing the summary to stdout.

```bash
poetry run screenredact report "<FRAMES_DIR>"
```

That produces `<FRAMES_DIR>/report.json`:

```json
{
  "total_frames": 3461,
  "frames_with_detections": 412,
  "detection_counts": {
    "EMAIL_ADDRESS": 412,
    "LOCATION": 87,
    "PHONE_NUMBER": 6,
    "CREDIT_CARD": 2
  }
}
```

and stdout like:

```text
Wrote <FRAMES_DIR>/report.json
412/3461 frames had detections.
 412  EMAIL_ADDRESS
  87  LOCATION
   6  PHONE_NUMBER
   2  CREDIT_CARD
```

### 2. Relay to the user

Surface the coverage ratio (`frames_with_detections / total_frames`) and the top couple of detection types. If the `LOCATION` count looks suspiciously high, mention it — NER-based address detection is the noisiest of the four recognizers and often fires on UI labels or proper nouns. Review a few sidecars before trusting them for redaction.

## Notes

- **Runtime is serial but GPU/Neural-Engine-accelerated.** Expect ~100–250 ms/frame in Vision's `accurate` mode on Apple Silicon. A 3000-frame video is ~5–15 min. For large inputs you can still run on a subset first (e.g. `cp <FRAMES_DIR>/frame_0005{00..09}.png /tmp/frame_sample/` → `poetry run screenredact detect /tmp/frame_sample/`).
- **First-frame startup is Presidio, not OCR.** Apple Vision loads instantly via the framework bridge; the ~5 s first-frame pause is Presidio + spaCy's `en_core_web_lg` model loading into memory. Subsequent frames reuse the cached analyzer.
- **Presidio warnings at startup are noise.** It logs `Recognizer not added to registry` for Spanish/Italian/Polish recognizers when the language is `en`. Harmless. Do not treat it as an error.
- **Ctrl-C is safe mid-run.** Already-written sidecars are preserved. Detection is idempotent per frame — re-running will re-OCR unchanged frames but overwrite sidecars with identical content.
- **Sidecars are gitignored.** The `.*_frames/` pattern in `.gitignore` catches the whole hidden frames directory, including the JSON files inside.
