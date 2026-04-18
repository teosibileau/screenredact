---
name: detect-frames
description: Run the screenredact detection pipeline over a directory of extracted video frames, producing one JSON sidecar per frame that contains PII (emails, phone numbers, credit cards, addresses). Use after the extract-frames skill has decomposed a video into PNGs, and before any redaction step — this is the "find what to blur" phase.
---

# detect-frames

Scan a directory of video frames with OCR + PII detection (PaddleOCR → Presidio) and write per-frame JSON sidecars for every frame that contains sensitive data.

## Prerequisites

The project's Python environment must be set up per `SETUP.md` — in particular:

- `poetry install` has been run
- `poetry run python -m spacy download en_core_web_lg` has been run at least once

If either is missing, run them before proceeding. They are one-time setup steps.

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

- Loads PaddleOCR + Presidio once per process (class-level cache in `screenredact.detector.FrameAnalyzer`)
- Iterates frames in sorted order
- For each frame: OCRs it, runs Presidio over each recognized line, collects any hits as `Detection` records
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

- Frames scanned (the CLI prints `Analyzed N frame(s). Wrote M detection file(s) to <dir>/`)
- A breakdown of detection types across all sidecars. Use:

  ```bash
  poetry run python -c "
  import json, pathlib, collections, sys
  d = pathlib.Path(sys.argv[1])
  c = collections.Counter()
  for p in sorted(d.glob('*.json')):
      for det in json.loads(p.read_text())['detections']:
          c[det['type']] += 1
  for t, n in c.most_common(): print(f'{n:4d}  {t}')
  " "<FRAMES_DIR>"
  ```

- If the `LOCATION` count looks suspiciously high, mention it — NER-based address detection is the noisiest of the four recognizers and often fires on UI labels or proper nouns. Review a few sidecars before trusting them for redaction.

## Notes

- **Runtime is serial and CPU-bound.** Expect 0.5–2 s/frame on Apple Silicon CPU. A 3000-frame video is 25–100 min. Warn the user up front if the frames dir is large and offer to run on a subset first (e.g. `cp <FRAMES_DIR>/frame_0005{00..09}.png /tmp/frame_sample/` → `poetry run screenredact detect /tmp/frame_sample/`).
- **First invocation per machine is slow.** PaddleOCR downloads ~30 MB of detection + recognition + angle-classification weights to `~/.paddleocr/` on the first frame it sees. Subsequent invocations are fast. If the first run appears to hang, it's almost certainly this download — don't kill it.
- **Presidio warnings at startup are noise.** It logs `Recognizer not added to registry` for Spanish/Italian/Polish recognizers when the language is `en`. Harmless. Do not treat it as an error.
- **Do not run the legacy script path.** The `scripts/` folder was removed; the canonical entry point is `poetry run screenredact detect ...` (or equivalently `poetry run python -m screenredact.cli detect ...`).
- **Ctrl-C is safe mid-run.** Already-written sidecars are preserved. Detection is idempotent per frame — re-running will re-OCR unchanged frames but overwrite sidecars with identical content.
- **Sidecars are gitignored.** The `.*_frames/` pattern in `.gitignore` catches the whole hidden frames directory, including the JSON files inside.
