"""Tests for the Typer CLI in screenredact.cli."""

from __future__ import annotations

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
    with patch("screenredact.cli.FrameAnalyzer"):
        result = runner.invoke(app, ["detect", str(empty)])
    assert result.exit_code == 1
    assert "No PNG frames found" in result.stderr


def test_cli_errors_on_directory_with_no_pngs(tmp_path):
    d = tmp_path / "notpngs"
    d.mkdir()
    (d / "hello.txt").write_text("nope")
    (d / "image.jpg").write_bytes(b"fake")
    with patch("screenredact.cli.FrameAnalyzer"):
        result = runner.invoke(app, ["detect", str(d)])
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# Analysis flow
# ---------------------------------------------------------------------------


def test_cli_runs_analyze_frame_per_png(tmp_path):
    d = tmp_path / "frames"
    d.mkdir()
    _make_frames(d, 3)

    with patch("screenredact.cli.FrameAnalyzer") as FakeAnalyzer:
        instance = FakeAnalyzer.return_value
        instance.write_detections.return_value = None
        result = runner.invoke(app, ["detect", str(d)])

    assert result.exit_code == 0
    assert instance.analyze_frame.call_count == 3


def test_cli_calls_write_detections_per_frame(tmp_path):
    d = tmp_path / "frames"
    d.mkdir()
    _make_frames(d, 3)

    with patch("screenredact.cli.FrameAnalyzer") as FakeAnalyzer:
        instance = FakeAnalyzer.return_value
        instance.write_detections.return_value = None
        runner.invoke(app, ["detect", str(d)])

    assert instance.write_detections.call_count == 3


def test_cli_counts_only_truthy_write_returns_as_written(tmp_path):
    d = tmp_path / "frames"
    d.mkdir()
    _make_frames(d, 3)

    with patch("screenredact.cli.FrameAnalyzer") as FakeAnalyzer:
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

    with patch("screenredact.cli.FrameAnalyzer") as FakeAnalyzer:
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

    with patch("screenredact.cli.FrameAnalyzer") as FakeAnalyzer:
        FakeAnalyzer.return_value.write_detections.return_value = None
        result = runner.invoke(
            app, ["detect", str(frames), "--output-dir", str(out)]
        )

    assert result.exit_code == 0
    FakeAnalyzer.return_value.write_detections.assert_called_with(
        frames / "frame_000000.png", out
    )


def test_cli_defaults_output_dir_to_frames_dir(tmp_path):
    frames = tmp_path / "frames"
    frames.mkdir()
    _make_frames(frames, 1)

    with patch("screenredact.cli.FrameAnalyzer") as FakeAnalyzer:
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

    with patch("screenredact.cli.FrameAnalyzer") as FakeAnalyzer:
        FakeAnalyzer.return_value.write_detections.return_value = None
        result = runner.invoke(app, ["detect", str(frames), "-o", str(out)])

    assert result.exit_code == 0
    assert out.is_dir()


def test_cli_lang_option_passed_to_analyzer(tmp_path):
    frames = tmp_path / "frames"
    frames.mkdir()
    _make_frames(frames, 1)

    with patch("screenredact.cli.FrameAnalyzer") as FakeAnalyzer:
        FakeAnalyzer.return_value.write_detections.return_value = None
        result = runner.invoke(app, ["detect", str(frames), "--lang", "es"])

    assert result.exit_code == 0
    # With one frame there is exactly one instantiation:
    FakeAnalyzer.assert_called_once_with(lang="es")


def test_cli_default_lang_is_en(tmp_path):
    frames = tmp_path / "frames"
    frames.mkdir()
    _make_frames(frames, 1)

    with patch("screenredact.cli.FrameAnalyzer") as FakeAnalyzer:
        FakeAnalyzer.return_value.write_detections.return_value = None
        runner.invoke(app, ["detect", str(frames)])

    FakeAnalyzer.assert_called_once_with(lang="en")


# ---------------------------------------------------------------------------
# Per-frame instantiation (moved inside the loop)
# ---------------------------------------------------------------------------


def test_cli_instantiates_analyzer_per_frame(tmp_path):
    d = tmp_path / "frames"
    d.mkdir()
    _make_frames(d, 4)

    with patch("screenredact.cli.FrameAnalyzer") as FakeAnalyzer:
        FakeAnalyzer.return_value.write_detections.return_value = None
        runner.invoke(app, ["detect", str(d)])

    assert FakeAnalyzer.call_count == 4
    assert FakeAnalyzer.call_args_list == [call(lang="en")] * 4


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

    with patch("screenredact.cli.FrameAnalyzer") as FakeAnalyzer:
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
