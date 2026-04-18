---
name: blur-frames
description: Apply a deterministic Gaussian blur to the bbox regions identified by the detect-frames skill, producing `<stem>_blurred.png` siblings of each affected frame. Use after `detect-frames` has produced sidecars, and before any video reassembly step — this is the "actually redact the pixels" phase. Pure code, no model calls.
---

# blur-frames

Consume the detection sidecars written by `detect-frames` and blur the PII bbox regions on each flagged PNG. Output is a blurred sibling file per affected frame; originals are never modified.

## Prerequisites

The project's Python environment must be set up per `SETUP.md`. `poetry install` (the default, which includes the `runtime` group) installs `opencv-contrib-python` directly, which provides `cv2`. No extra step needed.

If `detect-frames` has not been run against this directory yet, stop and do that first. Without sidecar JSONs there is nothing for this skill to blur.

## Inputs

Ask the user for:

1. **Frames directory** (required) — the same `./.<video-basename>_frames/` that `detect-frames` wrote sidecars into.

Blurred outputs are always written back into the same frames directory with a `_blurred.png` suffix on the stem (e.g. `frame_000501.png` → `frame_000501_blurred.png`). This is fixed — do not accept an override, even if the user asks. Keeping frames and their blurred counterparts co-located is what lets the future reassembly step pick the redacted variant with a simple glob.

## Procedure

### 1. Sanity-check the input

Verify the directory exists and contains PNG frames plus at least one detection sidecar.

```bash
ls "<FRAMES_DIR>"/*.png | head -1
ls "<FRAMES_DIR>"/*.json | head -1
```

If there are no sidecars, route the user back to `detect-frames`.

### 2. Run the blur pass

```bash
poetry run screenredact blur "<FRAMES_DIR>"
```

This is the project's own CLI. It:

- Globs `<FRAMES_DIR>/*.png` but excludes any existing `*_blurred.png` from a prior run, so re-invocations never double-blur.
- For each remaining frame, looks for a matching `<stem>.json` sidecar. If the sidecar is absent, malformed, or has an empty `detections` list, the frame is skipped — no output is written.
- For each frame that does have detections: collapses every 4-point bbox polygon to its axis-aligned bounding rectangle, pads it by 4px (configurable via `--padding`), and applies a `cv2.GaussianBlur` whose kernel size scales with the bbox height (floor 15, forced odd).
- Writes the blurred result to `<FRAMES_DIR>/<stem>_blurred.png` next to the original. The original frame is never touched.

Output convention: the blurred copy sits beside the source so downstream tooling can pick `<stem>_blurred.png` when present and fall back to `<stem>.png` otherwise.

### 3. Spot-check the output

Open one of the blurred outputs side-by-side with its source to confirm the PII region is actually obscured:

```bash
open "<FRAMES_DIR>/frame_000501.png" "<FRAMES_DIR>/frame_000501_blurred.png"
```

The bbox region (plus its 4px pad) should be visibly blurred; everything outside that region should be pixel-identical to the source.

## Report back

Surface:

- **Blurred count** — the CLI prints `Blurred M/N frame(s)`. `M` should equal `frames_with_detections` from `detect-frames`' `report.json`. If it doesn't, something went wrong (most likely a sidecar became unreadable between the two runs) — dig in before declaring success.
- **Output location** — `<FRAMES_DIR>/*_blurred.png`.

If the user wants to rewind, deleting every `<FRAMES_DIR>/*_blurred.png` restores the directory to its pre-blur state — originals were never modified.

## Notes

- **Gaussian blur is not cryptographically irreversible.** It is the standard visual redaction in screen recordings and is more than sufficient for "don't accidentally dox yourself on YouTube". If the user has high-sensitivity data (e.g. credentials that must resist recovery attacks), flag that this skill is the wrong tool and we should swap the default to solid-fill or pixelation before shipping.
- **Over-coverage is deliberate.** The AABB of the OCR polygon plus 4px padding errs toward blurring a slightly larger region than strictly required. This is the safe bias for a redaction tool.
- **Ctrl-C is safe mid-run.** Each frame is written atomically via `cv2.imwrite`. Already-written `_blurred.png` files are preserved. Re-running picks up where it left off and overwrites any blurred outputs in place — originals are never read as candidates (the `_blurred.png` suffix is excluded from the input glob).
- **Outputs are gitignored.** The existing `.*_frames/` pattern in `.gitignore` catches `_blurred.png` files automatically — they live in the same hidden frames directory as everything else.
- **No new dependencies required.** `opencv-contrib-python` (which provides `cv2`) is a direct entry in the `runtime` Poetry group. If `poetry install` has been run, you're ready.
