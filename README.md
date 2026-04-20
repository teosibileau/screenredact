# 🕶️ screenredact

Deterministic PII redaction for screen recordings. Feeds a video in, spits out the same video with emails, phone numbers, credit-card numbers, and addresses blurred.

## 🔍 What it redacts

- 📧 `EMAIL_ADDRESS`
- 📞 `PHONE_NUMBER`
- 💳 `CREDIT_CARD` (Luhn-validated)
- 📍 `LOCATION` (address proxy via spaCy NER)

OCR runs on-device through Apple's Vision framework — no network call, no model download, ~150–200 ms/frame on Apple Silicon. **🍎 macOS only.**

## ⚙️ How

Four pipeline steps, documented as skills in `skills/`:

1. 🎬 `extract-frames` — `ffmpeg` to lossless PNGs + `source.json` metadata
2. 🔎 `detect-frames` — Apple Vision OCR → Presidio recognition → per-frame sidecar JSONs
3. 🫧 `blur-frames` — Gaussian-blur the matched regions into `<stem>_blurred.png` siblings (originals untouched)
4. 🎞️ `reassemble-video` — `ffmpeg` muxes the redacted frames back into a playable file at the source's average framerate

Orchestration contract (file naming, step ordering, invariants) and environment setup: **[AGENT.md](./AGENT.md)**.

## 🚀 Usage

The pipeline is designed to be driven by an agent (Claude Code): open the repo, ask it to redact a video, and it follows the skills in order.

To drive it manually, see the individual `SKILL.md` files under `skills/`. The two Python steps are one-liners:

```sh
poetry run screenredact detect ./.<video-basename>_frames/
poetry run screenredact blur   ./.<video-basename>_frames/
```

The `extract-frames` and `reassemble-video` skills are thin wrappers around `ffmpeg`; their SKILL.md files contain copy-paste commands.

## 📁 Project layout

```text
screenredact/
├─ 📦 screenredact/   # library code (detector, blurrer, report, CLI)
├─ 🧠 skills/         # runbooks for each pipeline step
├─ 🧪 tests/          # hermetic unit tests (heavy deps stubbed at import)
├─ 🤖 AGENT.md        # orchestration contract + env setup
└─ 🔗 CLAUDE.md       # → AGENT.md (symlink; Claude Code picks this up)
```

## 🧪 Development

```sh
poetry install
poetry run pytest
poetry run pre-commit run --all-files
```
