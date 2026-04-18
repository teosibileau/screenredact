from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import ClassVar

from ocrmac import ocrmac
from PIL import Image
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
    # Presidio init is multi-second so we cache the analyzer at class level.
    # Apple Vision is a framework service — no model load per invocation —
    # so there is no corresponding OCR cache.
    _analyzer: ClassVar[AnalyzerEngine | None] = None

    def __init__(self, lang: str = "en-US"):
        self.lang = lang
        self.analyzer = self._get_analyzer()
        self.detections: list[Detection] = []

    @classmethod
    def _get_analyzer(cls) -> AnalyzerEngine:
        if cls._analyzer is None:
            cls._analyzer = AnalyzerEngine()
        return cls._analyzer

    @staticmethod
    def _rect_to_bbox(
        rect: tuple[float, float, float, float], img_w: int, img_h: int
    ) -> list[list[float]]:
        # Vision returns normalized (x, y, w, h) with a bottom-left origin.
        # The downstream blurrer wants a 4-point polygon in pixel space with
        # a top-left origin, ordered clockwise from the top-left corner.
        # Flip Y (y1 is top, y2 is bottom) and scale each dim by the image size.
        x, y, bw, bh = rect
        x1 = x * img_w
        x2 = (x + bw) * img_w
        y1 = (1.0 - y - bh) * img_h
        y2 = (1.0 - y) * img_h
        return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]

    def analyze_frame(self, image_path: Path) -> list[Detection]:
        self.detections = []

        with Image.open(image_path) as img:
            img_w, img_h = img.size

        language_preference = [self.lang] if self.lang else None
        annotations = ocrmac.OCR(
            str(image_path), language_preference=language_preference
        ).recognize()
        for raw_text, ocr_confidence, rect in annotations:
            if not raw_text:
                continue
            # ocrmac hands back `objc.pyobjc_unicode`, which Presidio's spaCy
            # tokenizer rejects ("expected str, got pyobjc_unicode"). Coerce
            # to a plain str at the boundary.
            text = str(raw_text)
            pii_results = self.analyzer.analyze(text=text, entities=PII_ENTITIES, language="en")
            bbox = self._rect_to_bbox(rect, img_w, img_h)
            for pii in pii_results:
                self.detections.append(
                    Detection(
                        type=pii.entity_type,
                        text=text[pii.start : pii.end],
                        line_text=text,
                        bbox=bbox,
                        ocr_confidence=float(ocr_confidence),
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
