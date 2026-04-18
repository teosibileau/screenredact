# Setup

Bring a fresh macOS machine from zero to running `screenredact`.

## 1. System dependencies

### Homebrew

```sh
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Follow the post-install instructions to add `brew` to your `PATH`.

### ffmpeg — video frame extraction

Used by the `extract-frames` skill to decompose input videos into lossless PNGs and, later, to reassemble redacted frames back into a playable video.

```sh
brew install ffmpeg
```

Verify:

```sh
ffmpeg -version   # expect ffmpeg version 8.x
```

### pyenv — Python version manager

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

### pipx — isolated Python app installer

```sh
brew install pipx
pipx ensurepath
exec "$SHELL"
```

### Poetry — dependency manager

```sh
pipx install poetry
```

Verify:

```sh
poetry --version   # expect Poetry (version 2.x)
```

## 2. Install project dependencies

From the repo root:

```sh
poetry install
```

This creates a virtualenv (cached under `~/Library/Caches/pypoetry/virtualenvs/`) and installs all runtime + dev dependencies. Expect ~500 MB of wheels the first time — PaddlePaddle alone is ~200 MB.

## 3. Download model weights

Presidio's default recognizers rely on spaCy's `en_core_web_lg` model (~700 MB) for NER-based address detection. Download it once per machine:

```sh
poetry run python -m spacy download en_core_web_lg
```

PaddleOCR pulls its own detection, recognition, and angle-classification weights on first inference and caches them under `~/.paddleocr/`. No manual step needed — the first run of `screenredact detect` will download ~30 MB.

## Verify

```sh
poetry run pytest
poetry run screenredact --help
```

If both succeed you're ready to run the pipeline.
