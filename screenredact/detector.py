from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import ClassVar

from paddleocr import PaddleOCR
from presidio_analyzer import AnalyzerEngine

PII_ENTITIES = ["EMAIL_ADDRESS", "PHONE_NUMBER", "CREDIT_CARD", "LOCATION"]


@dataclass
class Detection:
    type: str
    text: str
    line_text: str
    bbox: list[list[float]]
    ocr_confidence: float
    pii_score: float


class FrameAnalyzer:
    # Class-level caches: PaddleOCR + Presidio models load at most once per
    # process. Instances created inside tight loops reuse them for free.
    _ocr_cache: ClassVar[dict[str, PaddleOCR]] = {}
    _analyzer: ClassVar[AnalyzerEngine | None] = None

    def __init__(self, lang: str = "en"):
        self.lang = lang
        self.ocr = self._get_ocr(lang)
        self.analyzer = self._get_analyzer()
        self.detections: list[Detection] = []

    @classmethod
    def _get_ocr(cls, lang: str) -> PaddleOCR:
        if lang not in cls._ocr_cache:
            cls._ocr_cache[lang] = PaddleOCR(lang=lang)
        return cls._ocr_cache[lang]

    @classmethod
    def _get_analyzer(cls) -> AnalyzerEngine:
        if cls._analyzer is None:
            cls._analyzer = AnalyzerEngine()
        return cls._analyzer

    @staticmethod
    def _extract_ocr_fields(result: object) -> tuple[list, list, list]:
        # PaddleOCR 3.x returns pipeline objects exposing rec_texts / rec_scores
        # / rec_polys either as attributes or as dict-like access. Support both
        # so the detector survives minor API drift between releases.
        def get(name: str) -> list:
            if hasattr(result, name):
                return getattr(result, name) or []
            if isinstance(result, dict):
                return result.get(name) or []
            return []

        return get("rec_texts"), get("rec_scores"), get("rec_polys")

    def analyze_frame(self, image_path: Path) -> list[Detection]:
        self.detections = []

        results = self.ocr.predict(str(image_path))
        if not results:
            return self.detections

        for result in results:
            texts, scores, polys = self._extract_ocr_fields(result)
            for text, score, poly in zip(texts, scores, polys, strict=False):
                if not text:
                    continue
                pii_results = self.analyzer.analyze(text=text, entities=PII_ENTITIES, language="en")
                bbox = [[float(p[0]), float(p[1])] for p in poly]
                for pii in pii_results:
                    self.detections.append(
                        Detection(
                            type=pii.entity_type,
                            text=text[pii.start : pii.end],
                            line_text=text,
                            bbox=bbox,
                            ocr_confidence=float(score),
                            pii_score=float(pii.score),
                        )
                    )
        return self.detections

    def write_detections(self, image_path: Path, output_dir: Path) -> Path | None:
        if not self.detections:
            return None
        output_path = output_dir / f"{image_path.stem}.json"
        payload = {
            "frame": image_path.name,
            "detections": [asdict(d) for d in self.detections],
        }
        output_path.write_text(json.dumps(payload, indent=2))
        return output_path
