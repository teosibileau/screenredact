"""Unit tests for screenredact.report — sidecar aggregation."""

from __future__ import annotations

import json
from pathlib import Path

from screenredact.report import FrameAnalyzerReport


def _write_sidecar(dir_: Path, stem: str, types: list[str], *, valid: bool = True) -> Path:
    path = dir_ / f"{stem}.json"
    if not valid:
        path.write_text("{not json")
        return path
    payload = {
        "frame": f"{stem}.png",
        "detections": [
            {
                "type": t,
                "text": "x",
                "line_text": "x",
                "bbox": [[0, 0]],
                "ocr_confidence": 0.9,
                "pii_score": 1.0,
            }
            for t in types
        ],
    }
    path.write_text(json.dumps(payload))
    return path


def _write_png(dir_: Path, stem: str) -> Path:
    p = dir_ / f"{stem}.png"
    p.write_bytes(b"fake")
    return p


# ---------------------------------------------------------------------------
# scan()
# ---------------------------------------------------------------------------


def test_scan_empty_directory(tmp_path: Path):
    r = FrameAnalyzerReport(tmp_path)
    r.scan()
    assert r.total_frames == 0
    assert r.frames_with_detections == 0
    assert r.detection_counts == {}


def test_scan_counts_pngs_for_total_frames(tmp_path: Path):
    for stem in ["frame_000000", "frame_000001", "frame_000002"]:
        _write_png(tmp_path, stem)
    r = FrameAnalyzerReport(tmp_path)
    r.scan()
    assert r.total_frames == 3


def test_scan_counts_frames_with_detections(tmp_path: Path):
    _write_png(tmp_path, "frame_000000")
    _write_png(tmp_path, "frame_000001")
    _write_png(tmp_path, "frame_000002")
    _write_sidecar(tmp_path, "frame_000000", ["EMAIL_ADDRESS"])
    _write_sidecar(tmp_path, "frame_000002", ["EMAIL_ADDRESS", "PHONE_NUMBER"])

    r = FrameAnalyzerReport(tmp_path)
    r.scan()
    assert r.total_frames == 3
    assert r.frames_with_detections == 2


def test_scan_tallies_detection_types(tmp_path: Path):
    _write_sidecar(tmp_path, "a", ["EMAIL_ADDRESS"])
    _write_sidecar(tmp_path, "b", ["EMAIL_ADDRESS", "PHONE_NUMBER"])
    _write_sidecar(tmp_path, "c", ["LOCATION"])

    r = FrameAnalyzerReport(tmp_path)
    r.scan()
    assert dict(r.detection_counts) == {
        "EMAIL_ADDRESS": 2,
        "PHONE_NUMBER": 1,
        "LOCATION": 1,
    }


def test_scan_skips_malformed_json(tmp_path: Path):
    _write_sidecar(tmp_path, "good", ["EMAIL_ADDRESS"])
    _write_sidecar(tmp_path, "bad", [], valid=False)
    r = FrameAnalyzerReport(tmp_path)
    r.scan()
    assert r.detection_counts == {"EMAIL_ADDRESS": 1}
    assert r.frames_with_detections == 1


def test_scan_skips_non_detection_shapes(tmp_path: Path):
    # Mimic the shape written by the extract-frames skill's source.json:
    (tmp_path / "source.json").write_text(
        json.dumps(
            {
                "input": "some.mov",
                "frame_rate": "30000/1001",
                "width": 1920,
                "height": 1080,
                "pix_fmt": "yuv420p",
                "frame_count": 3461,
                "audio": None,
            }
        )
    )
    _write_sidecar(tmp_path, "frame_0", ["EMAIL_ADDRESS"])

    r = FrameAnalyzerReport(tmp_path)
    r.scan()
    assert r.frames_with_detections == 1
    assert r.detection_counts == {"EMAIL_ADDRESS": 1}


def test_scan_skips_detections_with_non_string_type(tmp_path: Path):
    (tmp_path / "weird.json").write_text(
        json.dumps(
            {
                "frame": "weird.png",
                "detections": [{"type": None, "text": "x"}, {"type": "EMAIL_ADDRESS", "text": "y"}],
            }
        )
    )
    r = FrameAnalyzerReport(tmp_path)
    r.scan()
    assert r.detection_counts == {"EMAIL_ADDRESS": 1}


def test_scan_ignores_previous_report_file(tmp_path: Path):
    # An old report.json should not be re-counted on the next scan:
    _write_sidecar(tmp_path, "frame_0", ["EMAIL_ADDRESS"])
    (tmp_path / "report.json").write_text(
        json.dumps({"total_frames": 999, "detections": [{"type": "SHOULD_NOT_COUNT"}]})
    )
    r = FrameAnalyzerReport(tmp_path)
    r.scan()
    assert r.detection_counts == {"EMAIL_ADDRESS": 1}


def test_scan_is_idempotent(tmp_path: Path):
    _write_sidecar(tmp_path, "a", ["EMAIL_ADDRESS"])
    r = FrameAnalyzerReport(tmp_path)
    r.scan()
    r.scan()  # second call should not double-count
    assert r.detection_counts == {"EMAIL_ADDRESS": 1}
    assert r.frames_with_detections == 1


# ---------------------------------------------------------------------------
# to_dict / write
# ---------------------------------------------------------------------------


def test_to_dict_shape(tmp_path: Path):
    _write_png(tmp_path, "a")
    _write_png(tmp_path, "b")
    _write_sidecar(tmp_path, "a", ["EMAIL_ADDRESS", "EMAIL_ADDRESS"])

    r = FrameAnalyzerReport(tmp_path)
    r.scan()
    d = r.to_dict()
    assert d == {
        "total_frames": 2,
        "frames_with_detections": 1,
        "detection_counts": {"EMAIL_ADDRESS": 2},
    }


def test_to_dict_counts_sorted_most_common_first(tmp_path: Path):
    _write_sidecar(tmp_path, "a", ["LOCATION"])
    _write_sidecar(tmp_path, "b", ["EMAIL_ADDRESS", "EMAIL_ADDRESS"])
    _write_sidecar(tmp_path, "c", ["EMAIL_ADDRESS", "PHONE_NUMBER"])

    r = FrameAnalyzerReport(tmp_path)
    r.scan()
    keys = list(r.to_dict()["detection_counts"].keys())
    assert keys[0] == "EMAIL_ADDRESS"  # 3 — highest count first


def test_write_creates_report_json(tmp_path: Path):
    _write_png(tmp_path, "a")
    _write_sidecar(tmp_path, "a", ["EMAIL_ADDRESS"])

    r = FrameAnalyzerReport(tmp_path)
    r.scan()
    out = r.write()

    assert out == tmp_path / "report.json"
    assert out.exists()
    data = json.loads(out.read_text())
    assert data["total_frames"] == 1
    assert data["frames_with_detections"] == 1
    assert data["detection_counts"] == {"EMAIL_ADDRESS": 1}


def test_write_without_scan_emits_zeros(tmp_path: Path):
    r = FrameAnalyzerReport(tmp_path)
    out = r.write()
    data = json.loads(out.read_text())
    assert data == {
        "total_frames": 0,
        "frames_with_detections": 0,
        "detection_counts": {},
    }


def test_write_overwrites_existing_report(tmp_path: Path):
    (tmp_path / "report.json").write_text('{"stale": true}')
    _write_sidecar(tmp_path, "a", ["EMAIL_ADDRESS"])
    r = FrameAnalyzerReport(tmp_path)
    r.scan()
    r.write()

    data = json.loads((tmp_path / "report.json").read_text())
    assert "stale" not in data
    assert data["detection_counts"] == {"EMAIL_ADDRESS": 1}
