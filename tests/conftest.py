"""Shared fixtures. Heavy deps (ocrmac, Presidio) are never imported in tests —
both are patched at the module boundary so tests are fast and hermetic."""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


def _minimal_png_bytes() -> bytes:
    """Return a valid 1x1 sRGB PNG (IHDR + IDAT + IEND).

    The blurrer's `_preserve_color_chunks` step parses the target file after
    `cv2.imwrite`; a stub that wrote arbitrary bytes would make that parse
    explode. A real (if tiny) PNG keeps the round-trip honest.
    """

    def chunk(ctype: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + ctype
            + data
            + struct.pack(">I", zlib.crc32(ctype + data) & 0xFFFFFFFF)
        )

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    # 1 scanline, filter byte + 3 RGB bytes, deflate-compressed.
    idat = chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00"))
    iend = chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


# --- Stub modules ------------------------------------------------------------
# Without these stubs, importing `screenredact.detector` would trigger
# `from ocrmac import ocrmac` + `from PIL import Image` + `from presidio_analyzer
# import AnalyzerEngine`, which pull in the Vision framework bridge, Pillow,
# and Presidio's spaCy-backed recognizers. We replace them with do-nothing
# module stubs that only expose the symbols detector.py uses, and rely on
# monkeypatching for per-test behaviour.


def _install_stub_module(name: str, **attrs: Any) -> ModuleType:
    module = ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


class _StubOcrmacOCR:
    """Default no-op OCR. Tests that need per-case output monkeypatch this
    via `screenredact.detector.ocrmac.OCR`.
    """

    def __init__(self, image: Any, **kwargs: Any) -> None:
        self.image = image
        self.init_kwargs = kwargs

    def recognize(self, px: bool = False) -> list:
        return []


class _StubAnalyzerEngine:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def analyze(self, *_args: Any, **_kwargs: Any) -> list:
        return []


class _StubPilImage:
    """Context-manager-compatible stand-in for `PIL.Image.Image`. Only exposes
    `.size`, which is what detector.py reads. Default matches the size tests
    use when computing expected bbox values (1000x500)."""

    def __init__(self, size: tuple[int, int] = (1000, 500)) -> None:
        self.size = size

    def __enter__(self) -> _StubPilImage:
        return self

    def __exit__(self, *_args: Any) -> None:
        pass


def _stub_pil_open(_path: Any) -> _StubPilImage:
    return _StubPilImage()


class _StubImage:
    """Minimal stand-in for a numpy image: exposes `shape` plus subscript
    get/set so `img[y1:y2, x1:x2] = blurred` works in blurrer.py without
    pulling numpy into the test environment.
    """

    def __init__(self, shape: tuple[int, int, int] = (1080, 1920, 3)) -> None:
        self.shape = shape
        self.writes: list[tuple[Any, Any]] = []

    def __getitem__(self, key: Any) -> Any:
        return ("roi", key)

    def __setitem__(self, key: Any, value: Any) -> None:
        self.writes.append((key, value))


def _stub_imread(path: Any, *_args: Any, **_kwargs: Any) -> _StubImage:
    return _StubImage()


def _stub_imwrite(path: Any, _img: Any, *_args: Any, **_kwargs: Any) -> bool:
    # Produce a real (if tiny) PNG so downstream parsing — notably the
    # blurrer's color-chunk preservation step — has a valid file to read.
    Path(path).write_bytes(_minimal_png_bytes())
    return True


def _stub_gaussian_blur(src: Any, ksize: Any, _sigma: Any, *_args: Any, **_kwargs: Any) -> Any:
    return ("blurred", src, ksize)


# `ocrmac` is a package whose interesting symbols live on its submodule
# `ocrmac.ocrmac`. `from ocrmac import ocrmac` resolves through both entries
# in sys.modules, so stub both.
_ocrmac_submodule = _install_stub_module("ocrmac.ocrmac", OCR=_StubOcrmacOCR)
_install_stub_module("ocrmac", ocrmac=_ocrmac_submodule)

# Same pattern for PIL — `from PIL import Image` resolves `Image` as an
# attribute on the `PIL` package *and* as the submodule `PIL.Image`.
_pil_image_submodule = _install_stub_module("PIL.Image", open=_stub_pil_open)
_install_stub_module("PIL", Image=_pil_image_submodule)

_install_stub_module("presidio_analyzer", AnalyzerEngine=_StubAnalyzerEngine)
_install_stub_module(
    "cv2",
    imread=_stub_imread,
    imwrite=_stub_imwrite,
    GaussianBlur=_stub_gaussian_blur,
)


# --- Fixtures ---------------------------------------------------------------


@pytest.fixture
def frames_dir(tmp_path: Path) -> Path:
    d = tmp_path / "frames"
    d.mkdir()
    for i in range(3):
        (d / f"frame_{i:06d}.png").write_bytes(b"fake-png")
    return d
