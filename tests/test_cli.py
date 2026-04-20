"""Tests for the Typer CLI in screenredact.cli."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import call, patch

from typer.testing import CliRunner

from screenredact.cli import app

runner = CliRunner()


def _make_frames(dir_: Path, n: int) -> None:
    for i in range(n):
        (dir_ / f"frame_{i:06d}.png").write_bytes(b"fake")


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def test_cli_errors_on_missing_directory(tmp_path):
    missing = tmp_path / "does_not_exist"
    result = runner.invoke(app, ["detect", str(missing)])
    assert result.exit_code != 0


def test_cli_errors_on_file_instead_of_directory(tmp_path):
    f = tmp_path / "not_a_dir.png"
    f.write_bytes(b"fake")
    result = runner.invoke(app, ["detect", str(f)])
    assert result.exit_code != 0


def test_cli_errors_on_empty_directory(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with patch("screenredact.detector.FrameAnalyzer"):
        result = runner.invoke(app, ["detect", str(empty)])
    assert result.exit_code == 1
    assert "No PNG frames found" in result.stderr


def test_cli_errors_on_directory_with_no_pngs(tmp_path):
    d = tmp_path / "notpngs"
    d.mkdir()
    (d / "hello.txt").write_text("nope")
    (d / "image.jpg").write_bytes(b"fake")
    with patch("screenredact.detector.FrameAnalyzer"):
        result = runner.invoke(app, ["detect", str(d)])
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# Analysis flow
# ---------------------------------------------------------------------------


def test_cli_runs_analyze_frame_per_png(tmp_path):
    d = tmp_path / "frames"
    d.mkdir()
    _make_frames(d, 3)

    with patch("screenredact.detector.FrameAnalyzer") as FakeAnalyzer:
        instance = FakeAnalyzer.return_value
        instance.write_detections.return_value = None
        result = runner.invoke(app, ["detect", str(d)])

    assert result.exit_code == 0
    assert instance.analyze_frame.call_count == 3


def test_cli_calls_write_detections_per_frame(tmp_path):
    d = tmp_path / "frames"
    d.mkdir()
    _make_frames(d, 3)

    with patch("screenredact.detector.FrameAnalyzer") as FakeAnalyzer:
        instance = FakeAnalyzer.return_value
        instance.write_detections.return_value = None
        runner.invoke(app, ["detect", str(d)])

    assert instance.write_detections.call_count == 3


def test_cli_counts_only_truthy_write_returns_as_written(tmp_path):
    d = tmp_path / "frames"
    d.mkdir()
    _make_frames(d, 3)

    with patch("screenredact.detector.FrameAnalyzer") as FakeAnalyzer:
        instance = FakeAnalyzer.return_value
        # Second frame yields a sidecar Path, others return None (no detections):
        instance.write_detections.side_effect = [
            None,
            d / "frame_000001.json",
            None,
        ]
        result = runner.invoke(app, ["detect", str(d)])

    assert result.exit_code == 0
    assert "Wrote 1 detection file(s)" in result.stdout


def test_cli_reports_total_frame_count(tmp_path):
    d = tmp_path / "frames"
    d.mkdir()
    _make_frames(d, 2)

    with patch("screenredact.detector.FrameAnalyzer") as FakeAnalyzer:
        FakeAnalyzer.return_value.write_detections.return_value = None
        result = runner.invoke(app, ["detect", str(d)])

    assert result.exit_code == 0
    assert "Analyzed 2 frame(s)" in result.stdout


# ---------------------------------------------------------------------------
# Options: --output-dir / --lang
# ---------------------------------------------------------------------------


def test_cli_output_dir_passed_to_write_detections(tmp_path):
    frames = tmp_path / "frames"
    out = tmp_path / "out"
    frames.mkdir()
    _make_frames(frames, 1)

    with patch("screenredact.detector.FrameAnalyzer") as FakeAnalyzer:
        FakeAnalyzer.return_value.write_detections.return_value = None
        result = runner.invoke(app, ["detect", str(frames), "--output-dir", str(out)])

    assert result.exit_code == 0
    FakeAnalyzer.return_value.write_detections.assert_called_with(frames / "frame_000000.png", out)


def test_cli_defaults_output_dir_to_frames_dir(tmp_path):
    frames = tmp_path / "frames"
    frames.mkdir()
    _make_frames(frames, 1)

    with patch("screenredact.detector.FrameAnalyzer") as FakeAnalyzer:
        FakeAnalyzer.return_value.write_detections.return_value = None
        runner.invoke(app, ["detect", str(frames)])

    FakeAnalyzer.return_value.write_detections.assert_called_with(
        frames / "frame_000000.png", frames
    )


def test_cli_creates_output_dir_if_missing(tmp_path):
    frames = tmp_path / "frames"
    frames.mkdir()
    _make_frames(frames, 1)
    out = tmp_path / "deep" / "out_dir"  # does not exist

    with patch("screenredact.detector.FrameAnalyzer") as FakeAnalyzer:
        FakeAnalyzer.return_value.write_detections.return_value = None
        result = runner.invoke(app, ["detect", str(frames), "-o", str(out)])

    assert result.exit_code == 0
    assert out.is_dir()


def test_cli_lang_option_passed_to_analyzer(tmp_path):
    frames = tmp_path / "frames"
    frames.mkdir()
    _make_frames(frames, 1)

    with patch("screenredact.detector.FrameAnalyzer") as FakeAnalyzer:
        FakeAnalyzer.return_value.write_detections.return_value = None
        result = runner.invoke(app, ["detect", str(frames), "--lang", "es"])

    assert result.exit_code == 0
    # With one frame there is exactly one instantiation:
    FakeAnalyzer.assert_called_once_with(lang="es")


def test_cli_default_lang_is_en_us(tmp_path):
    frames = tmp_path / "frames"
    frames.mkdir()
    _make_frames(frames, 1)

    with patch("screenredact.detector.FrameAnalyzer") as FakeAnalyzer:
        FakeAnalyzer.return_value.write_detections.return_value = None
        runner.invoke(app, ["detect", str(frames)])

    FakeAnalyzer.assert_called_once_with(lang="en-US")


# ---------------------------------------------------------------------------
# Per-frame instantiation (moved inside the loop)
# ---------------------------------------------------------------------------


def test_cli_instantiates_analyzer_per_frame(tmp_path):
    d = tmp_path / "frames"
    d.mkdir()
    _make_frames(d, 4)

    with patch("screenredact.detector.FrameAnalyzer") as FakeAnalyzer:
        FakeAnalyzer.return_value.write_detections.return_value = None
        runner.invoke(app, ["detect", str(d)])

    assert FakeAnalyzer.call_count == 4
    assert FakeAnalyzer.call_args_list == [call(lang="en-US")] * 4


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------


def test_cli_processes_frames_in_sorted_order(tmp_path):
    d = tmp_path / "frames"
    d.mkdir()
    for i in [5, 2, 9, 0, 3]:
        (d / f"frame_{i:06d}.png").write_bytes(b"x")

    seen: list[str] = []

    def _record(frame: Path):
        seen.append(frame.name)
        return []

    with patch("screenredact.detector.FrameAnalyzer") as FakeAnalyzer:
        FakeAnalyzer.return_value.analyze_frame.side_effect = _record
        FakeAnalyzer.return_value.write_detections.return_value = None
        result = runner.invoke(app, ["detect", str(d)])

    assert result.exit_code == 0
    assert seen == [
        "frame_000000.png",
        "frame_000002.png",
        "frame_000003.png",
        "frame_000005.png",
        "frame_000009.png",
    ]


# ---------------------------------------------------------------------------
# `report` subcommand
# ---------------------------------------------------------------------------


def _write_sidecar(dir_: Path, stem: str, types: list[str]) -> None:
    (dir_ / f"{stem}.json").write_text(
        json.dumps(
            {
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
        )
    )


def test_report_errors_on_missing_directory(tmp_path):
    result = runner.invoke(app, ["report", str(tmp_path / "nope")])
    assert result.exit_code != 0


def test_report_errors_on_file_instead_of_directory(tmp_path):
    f = tmp_path / "not_a_dir"
    f.write_bytes(b"x")
    result = runner.invoke(app, ["report", str(f)])
    assert result.exit_code != 0


def test_report_writes_report_json_in_frames_dir(tmp_path):
    _make_frames(tmp_path, 2)
    _write_sidecar(tmp_path, "frame_000000", ["EMAIL_ADDRESS"])

    result = runner.invoke(app, ["report", str(tmp_path)])

    assert result.exit_code == 0
    report_path = tmp_path / "report.json"
    assert report_path.exists()
    data = json.loads(report_path.read_text())
    assert data["total_frames"] == 2
    assert data["frames_with_detections"] == 1
    assert data["detection_counts"] == {"EMAIL_ADDRESS": 1}


def test_report_stdout_summary(tmp_path):
    _make_frames(tmp_path, 3)
    _write_sidecar(tmp_path, "frame_000000", ["EMAIL_ADDRESS"])
    _write_sidecar(tmp_path, "frame_000001", ["EMAIL_ADDRESS", "PHONE_NUMBER"])

    result = runner.invoke(app, ["report", str(tmp_path)])

    assert result.exit_code == 0
    assert "2/3 frames had detections" in result.stdout
    assert "EMAIL_ADDRESS" in result.stdout
    assert "PHONE_NUMBER" in result.stdout


def test_report_on_empty_dir_writes_zeros_and_does_not_list_counts(tmp_path):
    result = runner.invoke(app, ["report", str(tmp_path)])
    assert result.exit_code == 0
    data = json.loads((tmp_path / "report.json").read_text())
    assert data == {
        "total_frames": 0,
        "frames_with_detections": 0,
        "detection_counts": {},
    }
    # No type lines printed when there's nothing to report:
    assert "EMAIL_ADDRESS" not in result.stdout
    assert "0/0 frames had detections" in result.stdout


def test_report_prints_counts_most_common_first(tmp_path):
    # LOCATION has 1, EMAIL has 3, PHONE has 2:
    _write_sidecar(tmp_path, "a", ["LOCATION"])
    _write_sidecar(tmp_path, "b", ["EMAIL_ADDRESS", "EMAIL_ADDRESS", "PHONE_NUMBER"])
    _write_sidecar(tmp_path, "c", ["EMAIL_ADDRESS", "PHONE_NUMBER"])

    result = runner.invoke(app, ["report", str(tmp_path)])

    assert result.exit_code == 0
    email_idx = result.stdout.index("EMAIL_ADDRESS")
    phone_idx = result.stdout.index("PHONE_NUMBER")
    location_idx = result.stdout.index("LOCATION")
    assert email_idx < phone_idx < location_idx


def test_report_ignores_non_detection_jsons(tmp_path):
    _make_frames(tmp_path, 2)
    # source.json from extract-frames skill shape — no "detections" key:
    (tmp_path / "source.json").write_text(
        json.dumps({"input": "x.mov", "frame_rate": "30/1", "frame_count": 2})
    )
    _write_sidecar(tmp_path, "frame_000000", ["EMAIL_ADDRESS"])

    result = runner.invoke(app, ["report", str(tmp_path)])

    assert result.exit_code == 0
    data = json.loads((tmp_path / "report.json").read_text())
    assert data["frames_with_detections"] == 1
    assert data["detection_counts"] == {"EMAIL_ADDRESS": 1}


# ---------------------------------------------------------------------------
# `blur` subcommand
# ---------------------------------------------------------------------------


def test_blur_errors_on_missing_directory(tmp_path):
    result = runner.invoke(app, ["blur", str(tmp_path / "nope")])
    assert result.exit_code != 0


def test_blur_errors_on_file_instead_of_directory(tmp_path):
    f = tmp_path / "not_a_dir.png"
    f.write_bytes(b"fake")
    result = runner.invoke(app, ["blur", str(f)])
    assert result.exit_code != 0


def test_blur_errors_on_empty_directory(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    result = runner.invoke(app, ["blur", str(empty)])
    assert result.exit_code == 1
    assert "No PNG frames found" in result.stderr


def test_blur_skips_frames_without_sidecar(tmp_path):
    _make_frames(tmp_path, 3)
    # No sidecars at all — nothing to blur.
    with patch("screenredact.blurrer.FrameBlurrer") as FakeBlurrer:
        result = runner.invoke(app, ["blur", str(tmp_path)])
    assert result.exit_code == 0
    FakeBlurrer.return_value.blur_and_write.assert_not_called()
    assert "Blurred 0/3" in result.stdout


def test_blur_skips_frames_with_empty_detections(tmp_path):
    _make_frames(tmp_path, 2)
    _write_sidecar(tmp_path, "frame_000000", [])  # no detections
    with patch("screenredact.blurrer.FrameBlurrer") as FakeBlurrer:
        result = runner.invoke(app, ["blur", str(tmp_path)])
    assert result.exit_code == 0
    FakeBlurrer.return_value.blur_and_write.assert_not_called()


def test_blur_writes_blurred_png_for_frames_with_detections(tmp_path):
    _make_frames(tmp_path, 2)
    _write_sidecar(tmp_path, "frame_000000", ["EMAIL_ADDRESS"])
    with patch("screenredact.blurrer.FrameBlurrer") as FakeBlurrer:
        result = runner.invoke(app, ["blur", str(tmp_path)])
    assert result.exit_code == 0
    # Exactly one sidecar has detections, so exactly one blur call:
    assert FakeBlurrer.return_value.blur_and_write.call_count == 1
    args, _ = FakeBlurrer.return_value.blur_and_write.call_args
    assert args[0] == tmp_path / "frame_000000.png"
    assert args[2] == tmp_path / "frame_000000_blurred.png"


def test_blur_excludes_existing_blurred_outputs_from_input(tmp_path):
    _make_frames(tmp_path, 1)
    # Pre-existing blurred output from a prior run — must not be re-processed:
    (tmp_path / "frame_000000_blurred.png").write_bytes(b"already-blurred")
    _write_sidecar(tmp_path, "frame_000000", ["EMAIL_ADDRESS"])

    with patch("screenredact.blurrer.FrameBlurrer") as FakeBlurrer:
        result = runner.invoke(app, ["blur", str(tmp_path)])

    assert result.exit_code == 0
    # One real frame with detections -> one call; the pre-existing blurred
    # output should have been filtered out of the input glob entirely.
    assert FakeBlurrer.return_value.blur_and_write.call_count == 1
    assert "Blurred 1/1" in result.stdout


def test_blur_stdout_reports_blurred_over_total(tmp_path):
    _make_frames(tmp_path, 4)
    _write_sidecar(tmp_path, "frame_000000", ["EMAIL_ADDRESS"])
    _write_sidecar(tmp_path, "frame_000002", ["PHONE_NUMBER"])
    with patch("screenredact.blurrer.FrameBlurrer"):
        result = runner.invoke(app, ["blur", str(tmp_path)])
    assert result.exit_code == 0
    assert "Blurred 2/4" in result.stdout


def test_blur_passes_padding_option_to_blurrer(tmp_path):
    _make_frames(tmp_path, 1)
    _write_sidecar(tmp_path, "frame_000000", ["EMAIL_ADDRESS"])
    with patch("screenredact.blurrer.FrameBlurrer") as FakeBlurrer:
        runner.invoke(app, ["blur", str(tmp_path), "--padding", "12"])
    FakeBlurrer.assert_called_once_with(padding=12)


def test_blur_default_padding_is_4(tmp_path):
    _make_frames(tmp_path, 1)
    _write_sidecar(tmp_path, "frame_000000", ["EMAIL_ADDRESS"])
    with patch("screenredact.blurrer.FrameBlurrer") as FakeBlurrer:
        runner.invoke(app, ["blur", str(tmp_path)])
    FakeBlurrer.assert_called_once_with(padding=4)


def test_blur_tolerates_malformed_sidecar_json(tmp_path):
    _make_frames(tmp_path, 2)
    (tmp_path / "frame_000000.json").write_text("{not valid json")
    _write_sidecar(tmp_path, "frame_000001", ["EMAIL_ADDRESS"])
    with patch("screenredact.blurrer.FrameBlurrer") as FakeBlurrer:
        result = runner.invoke(app, ["blur", str(tmp_path)])
    assert result.exit_code == 0
    # Malformed sidecar is skipped silently; only the valid one blurs.
    assert FakeBlurrer.return_value.blur_and_write.call_count == 1


def test_blur_overwrites_existing_blurred_output(tmp_path):
    _make_frames(tmp_path, 1)
    _write_sidecar(tmp_path, "frame_000000", ["EMAIL_ADDRESS"])
    pre = tmp_path / "frame_000000_blurred.png"
    pre.write_bytes(b"stale-output")

    # Use the real FrameBlurrer + conftest's cv2 stub (which writes bytes
    # via imwrite) to verify the on-disk file actually gets rewritten.
    result = runner.invoke(app, ["blur", str(tmp_path)])

    assert result.exit_code == 0
    assert pre.read_bytes() != b"stale-output"
    # Original frame is never touched:
    assert (tmp_path / "frame_000000.png").read_bytes() == b"fake"
