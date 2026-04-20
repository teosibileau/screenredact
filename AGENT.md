# 🤖 AGENT.md

`screenredact` redacts PII (emails, phone numbers, credit cards, addresses) from screen recordings. The pipeline decomposes a video into lossless PNGs, OCRs every frame with Apple's Vision framework, runs Presidio PII recognition over the recognized text, Gaussian-blurs the matched regions, then reassembles the redacted frames back into a playable file. 🍎 macOS-only.

## 🧩 Pipeline

Four skills chain in sequence. Every skill consumes the previous one's artefacts from a single hidden directory (`./.<video-basename>_frames/`) and writes its own outputs back into the same place. All outputs are gitignored via the `.*_frames/` pattern.

```text
video.mov
   │
   ▼  🎬 extract-frames
.<video>_frames/
   ├─ frame_NNNNNN.png    (lossless, one per source frame)
   ├─ source.json         (framerate, pix_fmt, audio metadata, frame_count)
   └─ audio.<ext>         (only when the source has audio)
   │
   ▼  🔎 detect-frames
.<video>_frames/
   ├─ <stem>.json         (one per frame with ≥1 PII hit; clean frames produce no file)
   └─ report.json         (aggregate rollup across all sidecars)
   │
   ▼  🫧 blur-frames
.<video>_frames/
   └─ <stem>_blurred.png  (Gaussian-blurred copy sibling to the original)
   │
   ▼  🎞️ reassemble-video
<video>_redacted.mp4      (in the CWD, alongside the source)
   + concat.txt (kept in the frames dir for inspection)
```

Each step is documented in full at `skills/<name>/SKILL.md` — read the skill before invoking it.

### 📏 Orchestration rules

- **Never skip a step.** If a later skill is invoked before its predecessor ran, each skill stops and routes the user back.
- **Naming conventions are load-bearing.** Downstream globs depend on `frame_NNNNNN.png`, `<stem>.json`, `<stem>_blurred.png`, `source.json`. Don't let users override the frames dir path or the `_blurred.png` suffix, even if they ask.
- **Treat `source.json`'s `frame_rate` as the playback rate.** `extract-frames` stores `avg_frame_rate` there; `reassemble-video` passes it to ffmpeg as `-r`. Using ffprobe's `r_frame_rate` produces a video that plays 2–4× too fast for VFR sources.
- **Originals are never modified.** Every step writes a new sibling or nothing. Undo = delete the derived file.
- **Ctrl-C is safe everywhere.** Files are written atomically; re-running picks up where it left off.

## 🚨 Guardrails

- 🍎 **macOS only.** `ocrmac` wraps Apple Vision through pyobjc. Linux/Windows installs fail at dep resolution — intentional.
- 🔒 **Don't re-encode frames.** The pipeline is lossless up to reassembly; the final mux is the only encode step.
- ⚠️ **Don't delete the `.*_frames/` dir mid-pipeline without user confirmation.** It contains hours of OCR work even on a modest video.
- 📜 **`source.json` is the contract.** If its shape changes, `reassemble-video` breaks silently. Fields the pipeline relies on: `frame_rate`, `pix_fmt`, `frame_count`, `audio` (object or `null`).

## 🧰 Environment setup

Bring a fresh macOS machine from zero to running `screenredact`.

### 1. System dependencies

#### Homebrew

```sh
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Follow the post-install instructions to add `brew` to your `PATH`.

#### ffmpeg — video frame extraction

Used by the `extract-frames` and `reassemble-video` skills.

```sh
brew install ffmpeg
```

Verify:

```sh
ffmpeg -version   # expect ffmpeg version 8.x
```

#### jq — JSON reader

Used by the `reassemble-video` skill to read framerate, pixel format, and audio metadata from the `source.json` sidecar.

```sh
brew install jq
```

Verify:

```sh
jq --version   # expect jq-1.7 or newer
```

#### pyenv — Python version manager

```sh
brew install pyenv
```

Wire pyenv into your shell (zsh — adjust for bash):

```sh
echo 'export PYENV_ROOT="$HOME/.pyenv"' >> ~/.zshrc
echo '[[ -d $PYENV_ROOT/bin ]] && export PATH="$PYENV_ROOT/bin:$PATH"' >> ~/.zshrc
echo 'eval "$(pyenv init - zsh)"' >> ~/.zshrc
exec "$SHELL"
```

Install the Python version this project pins:

```sh
pyenv install 3.13
```

From the repo root, activate it locally:

```sh
cd <path-to-screenredact>
pyenv local 3.13
```

#### pipx — isolated Python app installer

```sh
brew install pipx
pipx ensurepath
exec "$SHELL"
```

#### Poetry — dependency manager

```sh
pipx install poetry
```

Verify:

```sh
poetry --version   # expect Poetry (version 2.x)
```

### 2. Install project dependencies

From the repo root:

```sh
poetry install
```

This creates a virtualenv (cached under `~/Library/Caches/pypoetry/virtualenvs/`) and installs all runtime + dev dependencies. The detect step uses Apple's Vision framework via `ocrmac`, so the runtime group is **macOS-only** — Linux / Windows installs fail at dependency resolution.

### 3. Download model weights

Presidio's default recognizers rely on spaCy's `en_core_web_lg` model (~700 MB) for NER-based address detection. Download it once per machine:

```sh
poetry run python -m spacy download en_core_web_lg
```

OCR itself uses Apple's Vision framework through `ocrmac` — no weights to download, no cache directory, and no first-run startup cost beyond loading the framework.

### Verify

```sh
poetry run pytest
poetry run screenredact --help
```

If both succeed you're ready to run the pipeline.
