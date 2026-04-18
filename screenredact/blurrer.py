"""Deterministic Gaussian-blur pass over a frame + its detection sidecar.

Consumes the bbox polygons written by `FrameAnalyzer.write_detections` and
produces a blurred copy of the frame. No ML, no model calls — pure pixel math
so the redaction step is reproducible and reviewable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

import cv2


class FrameBlurrer:
    # PNG ancillary chunks that carry color-management metadata. cv2.imwrite
    # strips all of them, which leaves the blurred output with a bare
    # IHDR/IDAT/IEND — an "untagged" PNG. The source ffmpeg frames, by
    # contrast, carry cICP + cHRM + gAMA describing their exact colorspace.
    # Feeding a mix of tagged and untagged frames to ffmpeg for reassembly
    # invites per-frame colorspace conversion mismatches, so we copy the
    # source's chunks into the target verbatim.
    _COLOR_CHUNK_TYPES: ClassVar[frozenset[bytes]] = frozenset(
        {b"cICP", b"iCCP", b"sRGB", b"cHRM", b"gAMA", b"pHYs", b"bKGD"}
    )

    # Byte offset after the mandatory PNG signature (8) + IHDR chunk
    # (length 4 + type 4 + data 13 + CRC 4 = 25) — i.e. the splice point
    # for inserting additional ancillary chunks before the first IDAT.
    _POST_IHDR_OFFSET: ClassVar[int] = 33

    def __init__(self, padding: int = 4) -> None:
        self.padding = padding

    @staticmethod
    def _polygon_to_bbox(
        poly: list[list[float]], padding: int, max_x: int, max_y: int
    ) -> tuple[int, int, int, int]:
        # PaddleOCR returns 4-point polygons that, on screen recordings, are
        # effectively axis-aligned. Collapsing to an AABB is both simpler and a
        # slight over-cover — which is the right bias for a redaction tool.
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        x1 = max(0, int(min(xs)) - padding)
        y1 = max(0, int(min(ys)) - padding)
        x2 = min(max_x, int(max(xs)) + padding)
        y2 = min(max_y, int(max(ys)) + padding)
        return x1, y1, x2, y2

    @staticmethod
    def _kernel_size(bbox_height: int) -> int:
        # Kernel scales with text height so small captions and large headings both
        # get visually destroyed. Floor of 15 keeps tiny bboxes from looking sharp.
        # cv2.GaussianBlur requires an odd kernel, so force the low bit on.
        k = max(15, bbox_height // 2)
        if k % 2 == 0:
            k += 1
        return k

    @classmethod
    def _extract_color_chunks(cls, png_bytes: bytes) -> bytes:
        out = bytearray()
        pos = 8  # skip the 8-byte PNG signature
        n = len(png_bytes)
        while pos + 8 <= n:
            length = int.from_bytes(png_bytes[pos : pos + 4], "big")
            ctype = png_bytes[pos + 4 : pos + 8]
            total = 12 + length  # length(4) + type(4) + data(length) + CRC(4)
            if ctype == b"IEND":
                break
            if ctype in cls._COLOR_CHUNK_TYPES and pos + total <= n:
                out += png_bytes[pos : pos + total]
            pos += total
        return bytes(out)

    @classmethod
    def _insert_chunks_after_ihdr(cls, png_bytes: bytes, chunks: bytes) -> bytes:
        if not chunks:
            return png_bytes
        return png_bytes[: cls._POST_IHDR_OFFSET] + chunks + png_bytes[cls._POST_IHDR_OFFSET :]

    @classmethod
    def _preserve_color_chunks(cls, source_path: Path, target_path: Path) -> None:
        chunks = cls._extract_color_chunks(source_path.read_bytes())
        if not chunks:
            return
        target_path.write_bytes(cls._insert_chunks_after_ihdr(target_path.read_bytes(), chunks))

    def blur_frame(self, image_path: Path, detections: list[dict[str, Any]]) -> Any:
        img = cv2.imread(str(image_path))
        if img is None:
            raise FileNotFoundError(f"Could not read image: {image_path}")
        h, w = img.shape[0], img.shape[1]
        for det in detections:
            poly = det.get("bbox")
            if not poly:
                continue
            x1, y1, x2, y2 = self._polygon_to_bbox(poly, self.padding, w, h)
            if x2 <= x1 or y2 <= y1:
                continue
            ksize = self._kernel_size(y2 - y1)
            roi = img[y1:y2, x1:x2]
            img[y1:y2, x1:x2] = cv2.GaussianBlur(roi, (ksize, ksize), 0)
        return img

    def blur_and_write(
        self,
        image_path: Path,
        detections: list[dict[str, Any]],
        output_path: Path,
    ) -> None:
        img = self.blur_frame(image_path, detections)
        cv2.imwrite(str(output_path), img)
        self._preserve_color_chunks(image_path, output_path)
