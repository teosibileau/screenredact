"""Aggregate detection sidecars in a frames directory into a single report.json."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


class FrameAnalyzerReport:
    """Scan detection sidecars (`frame_*.json`) in a frames directory and
    produce a roll-up `report.json` summarizing totals and per-type counts.

    Non-detection JSON files in the same directory (e.g. the `source.json`
    written by the extract-frames skill) are silently skipped so they don't
    pollute the tally.
    """

    REPORT_FILENAME = "report.json"

    def __init__(self, frames_dir: Path):
        self.frames_dir = frames_dir
        self.total_frames: int = 0
        self.frames_with_detections: int = 0
        self.detection_counts: Counter[str] = Counter()

    def scan(self) -> None:
        """Populate counters by reading every sidecar in the directory."""
        self.total_frames = len(list(self.frames_dir.glob("*.png")))
        self.frames_with_detections = 0
        self.detection_counts = Counter()

        for sidecar in sorted(self.frames_dir.glob("*.json")):
            if sidecar.name == self.REPORT_FILENAME:
                continue  # don't re-count a previous report
            try:
                payload = json.loads(sidecar.read_text())
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            detections = payload.get("detections")
            if not isinstance(detections, list):
                continue  # not a detection sidecar — skip
            self.frames_with_detections += 1
            for det in detections:
                if not isinstance(det, dict):
                    continue
                t = det.get("type")
                if isinstance(t, str):
                    self.detection_counts[t] += 1

    def to_dict(self) -> dict:
        return {
            "total_frames": self.total_frames,
            "frames_with_detections": self.frames_with_detections,
            "detection_counts": dict(self.detection_counts.most_common()),
        }

    def write(self) -> Path:
        """Write `report.json` into the frames directory."""
        out = self.frames_dir / self.REPORT_FILENAME
        out.write_text(json.dumps(self.to_dict(), indent=2))
        return out
