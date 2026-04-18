"""Tests for screenredact.blurrer."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path
from unittest.mock import patch

import pytest

from screenredact.blurrer import FrameBlurrer


def _chunk(ctype: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + ctype
        + data
        + struct.pack(">I", zlib.crc32(ctype + data) & 0xFFFFFFFF)
    )


def _build_png(extra_chunks: list[tuple[bytes, bytes]] | None = None) -> bytes:
    """Build a minimal valid PNG, optionally with extra ancillary chunks after IHDR."""
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    ancillary = b"".join(_chunk(t, d) for t, d in (extra_chunks or []))
    idat = _chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00"))
    iend = _chunk(b"IEND", b"")
    return sig + ihdr + ancillary + idat + iend


_CHRM_PAYLOAD = struct.pack(">8I", 31270, 32900, 64000, 33000, 30000, 60000, 15000, 6000)
_GAMA_PAYLOAD = struct.pack(">I", 45455)
_SRGB_PAYLOAD = b"\x00"
_CICP_PAYLOAD = b"\x01\x0d\x00\x01"  # Rec. 709 primaries/transfer, full-range


# ---------------------------------------------------------------------------
# Polygon -> axis-aligned bbox
# ---------------------------------------------------------------------------


def test_polygon_to_bbox_handles_axis_aligned_quad():
    poly = [[10.0, 20.0], [100.0, 20.0], [100.0, 50.0], [10.0, 50.0]]
    assert FrameBlurrer._polygon_to_bbox(poly, padding=0, max_x=1920, max_y=1080) == (
        10,
        20,
        100,
        50,
    )


def test_polygon_to_bbox_applies_padding():
    poly = [[10.0, 20.0], [100.0, 20.0], [100.0, 50.0], [10.0, 50.0]]
    assert FrameBlurrer._polygon_to_bbox(poly, padding=4, max_x=1920, max_y=1080) == (
        6,
        16,
        104,
        54,
    )


def test_polygon_to_bbox_clamps_to_image_bounds():
    # Box near bottom-right corner; padding would overshoot without clamp.
    poly = [[1900.0, 1060.0], [1920.0, 1060.0], [1920.0, 1080.0], [1900.0, 1080.0]]
    assert FrameBlurrer._polygon_to_bbox(poly, padding=10, max_x=1920, max_y=1080) == (
        1890,
        1050,
        1920,
        1080,
    )


def test_polygon_to_bbox_clamps_negative_origin():
    # Padding that would push x1/y1 negative gets floored at 0.
    poly = [[1.0, 1.0], [50.0, 1.0], [50.0, 20.0], [1.0, 20.0]]
    assert FrameBlurrer._polygon_to_bbox(poly, padding=10, max_x=1920, max_y=1080)[:2] == (0, 0)


def test_polygon_to_bbox_handles_slightly_skewed_quad():
    # OCR polygons are near-axis-aligned but not perfectly so. AABB should
    # over-cover (safe for redaction), not under-cover.
    poly = [[10.0, 21.0], [100.0, 20.0], [101.0, 50.0], [11.0, 51.0]]
    x1, y1, x2, y2 = FrameBlurrer._polygon_to_bbox(poly, padding=0, max_x=1920, max_y=1080)
    assert (x1, y1, x2, y2) == (10, 20, 101, 51)


# ---------------------------------------------------------------------------
# Adaptive kernel size
# ---------------------------------------------------------------------------


def test_kernel_size_floor_is_15():
    # Tiny bbox still gets a visibly destructive kernel.
    assert FrameBlurrer._kernel_size(2) == 15
    assert FrameBlurrer._kernel_size(20) == 15  # 20//2 = 10, below floor


def test_kernel_size_scales_with_height():
    # Large text heights drive a proportional kernel.
    assert FrameBlurrer._kernel_size(80) == 41  # 80//2 = 40, forced odd -> 41
    assert FrameBlurrer._kernel_size(100) == 51


def test_kernel_size_is_always_odd():
    for h in range(0, 500, 7):
        k = FrameBlurrer._kernel_size(h)
        assert k % 2 == 1, f"kernel {k} for height {h} is not odd"


# ---------------------------------------------------------------------------
# FrameBlurrer.blur_frame
# ---------------------------------------------------------------------------


def _sidecar_det(bbox: list[list[float]], type_: str = "EMAIL_ADDRESS") -> dict:
    return {
        "type": type_,
        "text": "x",
        "line_text": "x",
        "bbox": bbox,
        "ocr_confidence": 0.99,
        "pii_score": 1.0,
    }


def test_blur_frame_returns_image_from_cv2_imread(tmp_path: Path):
    png = tmp_path / "frame.png"
    png.write_bytes(b"fake")
    blurrer = FrameBlurrer()
    img = blurrer.blur_frame(png, detections=[])
    # The conftest stub returns a _StubImage; shape attribute is enough
    # to confirm we got the object back unchanged.
    assert hasattr(img, "shape")


def test_blur_frame_calls_gaussian_blur_per_detection(tmp_path: Path):
    png = tmp_path / "frame.png"
    png.write_bytes(b"fake")
    detections = [
        _sidecar_det([[10, 20], [100, 20], [100, 50], [10, 50]]),
        _sidecar_det([[200, 300], [400, 300], [400, 340], [200, 340]]),
    ]
    with patch("screenredact.blurrer.cv2.GaussianBlur") as fake_blur:
        fake_blur.return_value = "blurred-roi"
        FrameBlurrer().blur_frame(png, detections)
    assert fake_blur.call_count == 2


def test_blur_frame_kernel_is_derived_from_bbox_height(tmp_path: Path):
    png = tmp_path / "frame.png"
    png.write_bytes(b"fake")
    # Height 30 -> padded to 38 -> kernel max(15, 38//2=19) = 19 (odd already).
    detections = [_sidecar_det([[10, 20], [100, 20], [100, 50], [10, 50]])]
    with patch("screenredact.blurrer.cv2.GaussianBlur") as fake_blur:
        fake_blur.return_value = "blurred-roi"
        FrameBlurrer(padding=4).blur_frame(png, detections)
    _, ksize, _sigma = fake_blur.call_args.args
    assert ksize == (19, 19)


def test_blur_frame_skips_detections_without_bbox(tmp_path: Path):
    png = tmp_path / "frame.png"
    png.write_bytes(b"fake")
    detections = [
        {
            "type": "EMAIL_ADDRESS",
            "text": "x",
            "line_text": "x",
            "ocr_confidence": 0.9,
            "pii_score": 1.0,
        },  # no bbox key
        _sidecar_det([]),  # empty bbox list
    ]
    with patch("screenredact.blurrer.cv2.GaussianBlur") as fake_blur:
        FrameBlurrer().blur_frame(png, detections)
    fake_blur.assert_not_called()


def test_blur_frame_skips_degenerate_bboxes(tmp_path: Path):
    png = tmp_path / "frame.png"
    png.write_bytes(b"fake")
    # Zero-area polygon + zero padding collapses to x2==x1 / y2==y1 and
    # should be dropped without calling blur — otherwise cv2 would choke
    # on an empty ROI.
    detections = [_sidecar_det([[0, 0], [0, 0], [0, 0], [0, 0]])]
    with patch("screenredact.blurrer.cv2.GaussianBlur") as fake_blur:
        FrameBlurrer(padding=0).blur_frame(png, detections)
    fake_blur.assert_not_called()


def test_blur_frame_raises_when_image_missing(tmp_path: Path):
    missing = tmp_path / "missing.png"
    with (
        patch("screenredact.blurrer.cv2.imread", return_value=None),
        pytest.raises(FileNotFoundError),
    ):
        FrameBlurrer().blur_frame(missing, detections=[])


def test_blur_frame_assigns_blurred_roi_back_to_image(tmp_path: Path):
    png = tmp_path / "frame.png"
    png.write_bytes(b"fake")
    detections = [_sidecar_det([[10, 20], [100, 20], [100, 50], [10, 50]])]
    img = FrameBlurrer().blur_frame(png, detections)
    # _StubImage records every __setitem__ in .writes — one per detection.
    assert len(img.writes) == 1
    (key, value) = img.writes[0]
    assert isinstance(key, tuple) and len(key) == 2
    # Value should be the tuple returned by _stub_gaussian_blur.
    assert value[0] == "blurred"


# ---------------------------------------------------------------------------
# FrameBlurrer.blur_and_write
# ---------------------------------------------------------------------------


def test_blur_and_write_invokes_imwrite_with_output_path(tmp_path: Path):
    png = tmp_path / "frame.png"
    png.write_bytes(b"fake")
    out = tmp_path / "frame_blurred.png"
    detections = [_sidecar_det([[10, 20], [100, 20], [100, 50], [10, 50]])]
    with patch("screenredact.blurrer.cv2.imwrite") as fake_imwrite:
        FrameBlurrer().blur_and_write(png, detections, out)
    fake_imwrite.assert_called_once()
    args, _ = fake_imwrite.call_args
    assert args[0] == str(out)


def test_blur_and_write_produces_output_file_via_stub(tmp_path: Path):
    # With the conftest cv2 stub (imwrite actually writes a placeholder),
    # round-tripping through blur_and_write leaves a real file on disk —
    # which is what CLI-level tests downstream will rely on.
    png = tmp_path / "frame.png"
    png.write_bytes(b"fake")
    out = tmp_path / "frame_blurred.png"
    detections = [_sidecar_det([[10, 20], [100, 20], [100, 50], [10, 50]])]
    FrameBlurrer().blur_and_write(png, detections, out)
    assert out.exists()


# ---------------------------------------------------------------------------
# PNG color-chunk preservation
# ---------------------------------------------------------------------------


def test_extract_color_chunks_picks_up_chrm_and_gama():
    png = _build_png([(b"cHRM", _CHRM_PAYLOAD), (b"gAMA", _GAMA_PAYLOAD)])
    chunks = FrameBlurrer._extract_color_chunks(png)
    # cHRM total = 12 + 32 = 44; gAMA total = 12 + 4 = 16.
    assert len(chunks) == 44 + 16
    assert b"cHRM" in chunks
    assert b"gAMA" in chunks


def test_extract_color_chunks_preserves_source_order():
    png = _build_png(
        [
            (b"cICP", _CICP_PAYLOAD),
            (b"cHRM", _CHRM_PAYLOAD),
            (b"gAMA", _GAMA_PAYLOAD),
        ]
    )
    chunks = FrameBlurrer._extract_color_chunks(png)
    # Each type must appear in the same relative order as the source.
    assert chunks.index(b"cICP") < chunks.index(b"cHRM") < chunks.index(b"gAMA")


def test_extract_color_chunks_returns_empty_on_untagged_png():
    png = _build_png([])
    assert FrameBlurrer._extract_color_chunks(png) == b""


def test_extract_color_chunks_ignores_non_color_chunks():
    # tEXt is ancillary metadata but not color-related — must not be copied.
    png = _build_png([(b"tEXt", b"Author\x00me"), (b"gAMA", _GAMA_PAYLOAD)])
    chunks = FrameBlurrer._extract_color_chunks(png)
    assert b"gAMA" in chunks
    assert b"tEXt" not in chunks


def test_extract_color_chunks_tolerates_garbage_input():
    # Non-PNG bytes (e.g. a test fixture written as `b"fake"`) must not crash
    # the extractor; it should simply find no chunks and return empty.
    assert FrameBlurrer._extract_color_chunks(b"fake") == b""
    assert FrameBlurrer._extract_color_chunks(b"") == b""


def test_insert_chunks_after_ihdr_splices_at_byte_33():
    png = _build_png([])
    injected = _chunk(b"gAMA", _GAMA_PAYLOAD)
    out = FrameBlurrer._insert_chunks_after_ihdr(png, injected)
    assert out[:33] == png[:33]  # signature + IHDR unchanged
    assert out[33 : 33 + len(injected)] == injected
    assert out[33 + len(injected) :] == png[33:]


def test_insert_chunks_after_ihdr_noop_on_empty_chunks():
    png = _build_png([])
    assert (
        FrameBlurrer._insert_chunks_after_ihdr(png, b"") is png
        or FrameBlurrer._insert_chunks_after_ihdr(png, b"") == png
    )


def test_preserve_color_chunks_end_to_end(tmp_path: Path):
    src = tmp_path / "src.png"
    src.write_bytes(
        _build_png(
            [
                (b"cICP", _CICP_PAYLOAD),
                (b"cHRM", _CHRM_PAYLOAD),
                (b"gAMA", _GAMA_PAYLOAD),
            ]
        )
    )
    tgt = tmp_path / "tgt.png"
    tgt.write_bytes(_build_png([]))  # untagged

    FrameBlurrer._preserve_color_chunks(src, tgt)

    after = tgt.read_bytes()
    assert b"cICP" in after
    assert b"cHRM" in after
    assert b"gAMA" in after
    # Injected chunks land after IHDR and before IDAT:
    assert after.index(b"cICP") < after.index(b"IDAT")
    assert after.index(b"cHRM") < after.index(b"IDAT")


def test_preserve_color_chunks_noop_when_source_has_none(tmp_path: Path):
    src = tmp_path / "src.png"
    src.write_bytes(_build_png([]))  # no color chunks
    tgt = tmp_path / "tgt.png"
    original = _build_png([])
    tgt.write_bytes(original)

    FrameBlurrer._preserve_color_chunks(src, tgt)

    assert tgt.read_bytes() == original


def test_blur_and_write_carries_color_chunks_to_output(tmp_path: Path):
    # End-to-end: a real PNG source with cHRM+gAMA, run through blur_and_write
    # (backed by the cv2 stub that writes a valid minimal PNG) — the output
    # should end up carrying the source's color-management chunks.
    src = tmp_path / "frame.png"
    src.write_bytes(_build_png([(b"cHRM", _CHRM_PAYLOAD), (b"gAMA", _GAMA_PAYLOAD)]))
    out = tmp_path / "frame_blurred.png"
    detections = [_sidecar_det([[10, 20], [100, 20], [100, 50], [10, 50]])]

    FrameBlurrer().blur_and_write(src, detections, out)

    after = out.read_bytes()
    assert b"cHRM" in after
    assert b"gAMA" in after
