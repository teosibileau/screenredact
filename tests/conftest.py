"""Shared fixtures. Heavy deps (PaddleOCR, Presidio) are never imported in tests —
both are patched at the module boundary so tests are fast and hermetic."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

# --- Stub modules ------------------------------------------------------------
# Without these stubs, importing `screenredact.detector` would trigger
# `import paddleocr` / `import presidio_analyzer`, which pull in large
# ML dependencies (and need downloaded models). We replace them with
# do-nothing module stubs that only expose the symbols detector.py uses.


def _install_stub_module(name: str, **attrs: Any) -> ModuleType:
    module = ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


class _StubPaddleOCR:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.init_args = args
        self.init_kwargs = kwargs

    def predict(self, *_args: Any, **_kwargs: Any) -> list:
        return []


class _StubAnalyzerEngine:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def analyze(self, *_args: Any, **_kwargs: Any) -> list:
        return []


_install_stub_module("paddleocr", PaddleOCR=_StubPaddleOCR)
_install_stub_module("presidio_analyzer", AnalyzerEngine=_StubAnalyzerEngine)


# --- Fixtures ---------------------------------------------------------------


@dataclass
class FakePiiResult:
    entity_type: str
    start: int
    end: int
    score: float


class FakeOcrResult:
    """Mimics the PaddleOCR 3.x result object (attribute access)."""

    def __init__(
        self,
        rec_texts: list[str],
        rec_scores: list[float],
        rec_polys: list[list[list[float]]],
    ) -> None:
        self.rec_texts = rec_texts
        self.rec_scores = rec_scores
        self.rec_polys = rec_polys


@pytest.fixture
def frames_dir(tmp_path: Path) -> Path:
    d = tmp_path / "frames"
    d.mkdir()
    for i in range(3):
        (d / f"frame_{i:06d}.png").write_bytes(b"fake-png")
    return d


@pytest.fixture
def fake_ocr_result():
    return FakeOcrResult


@pytest.fixture
def fake_pii_result():
    return FakePiiResult
