"""Unit tests for screenredact.detector — rect→bbox conversion, ocrmac +
Presidio orchestration, and JSON sidecar writing."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from screenredact.detector import PII_ENTITIES, Detection, FrameAnalyzer


def _flat_bbox(bbox: list[list[float]]) -> list[float]:
    # Flatten so pytest.approx can compare with a tolerance — the (1-y-h)*h
    # arithmetic in `_rect_to_bbox` routinely lands a ULP or two off from the
    # obvious integer, and exact equality would be spuriously brittle.
    return [c for row in bbox for c in row]


@pytest.fixture(autouse=True)
def _reset_frameanalyzer_caches():
    # FrameAnalyzer caches Presidio at the class level so the analyzer loads
    # once per process. Tests monkeypatch that cached instance, so without a
    # reset the patches leak into each other.
    FrameAnalyzer._analyzer = None
    yield
    FrameAnalyzer._analyzer = None


# ---------------------------------------------------------------------------
# FrameAnalyzer._rect_to_bbox (staticmethod — pure coord math)
# ---------------------------------------------------------------------------


def test_rect_to_bbox_full_frame_spans_image():
    # rect=(0,0,1,1) covers the whole image. Vision's bottom-left origin
    # flipped to top-left produces y1=0 (top) and y2=h (bottom).
    bbox = FrameAnalyzer._rect_to_bbox((0.0, 0.0, 1.0, 1.0), 1000, 500)
    assert bbox == [[0.0, 0.0], [1000.0, 0.0], [1000.0, 500.0], [0.0, 500.0]]


def test_rect_to_bbox_y_flip_to_pixel_coords():
    # Vision rect at (x=0.1, y=0.9) with size (0.3, 0.05) on a 1000x500 image:
    #   x1 = 0.1*1000 = 100
    #   x2 = 0.4*1000 = 400
    #   y1 = (1 - 0.9 - 0.05)*500 = 25    (top edge in pixel space)
    #   y2 = (1 - 0.9)*500         = 50    (bottom edge in pixel space)
    bbox = FrameAnalyzer._rect_to_bbox((0.1, 0.9, 0.3, 0.05), 1000, 500)
    expected = [[100.0, 25.0], [400.0, 25.0], [400.0, 50.0], [100.0, 50.0]]
    assert _flat_bbox(bbox) == pytest.approx(_flat_bbox(expected), abs=1e-6)


def test_rect_to_bbox_clockwise_from_top_left():
    # Downstream blurrer expects the 4-point polygon ordered clockwise from
    # the top-left corner. Lock that contract here.
    bbox = FrameAnalyzer._rect_to_bbox((0.2, 0.3, 0.1, 0.1), 800, 400)
    tl, tr, br, bl = bbox
    assert tl[0] < tr[0] and tl[1] == tr[1]  # top edge, left to right
    assert tr[1] < br[1] and tr[0] == br[0]  # right edge, top to bottom
    assert br[0] > bl[0] and br[1] == bl[1]  # bottom edge, right to left
    assert bl[1] > tl[1] and bl[0] == tl[0]  # left edge, bottom to top


# ---------------------------------------------------------------------------
# Class-level Presidio cache + instance init
# ---------------------------------------------------------------------------


def test_multiple_analyzers_share_presidio_engine():
    a = FrameAnalyzer()
    b = FrameAnalyzer(lang="de-DE")
    assert a.analyzer is b.analyzer


def test_analyzer_initializes_detections_as_empty_list():
    a = FrameAnalyzer()
    assert a.detections == []


def test_analyzer_default_lang_is_bcp47_english():
    # Apple Vision takes BCP-47 codes ("en-US"), not ISO-639 ("en"). Lock
    # the default so a casual `FrameAnalyzer()` call keeps working.
    assert FrameAnalyzer().lang == "en-US"


# ---------------------------------------------------------------------------
# Detection dataclass
# ---------------------------------------------------------------------------


def test_detection_asdict_round_trips_via_json():
    det = Detection(
        type="EMAIL_ADDRESS",
        text="a@b.com",
        line_text="contact: a@b.com",
        bbox=[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
        ocr_confidence=0.95,
        pii_score=1.0,
    )
    payload = json.dumps(asdict(det))
    restored = json.loads(payload)
    assert restored["type"] == "EMAIL_ADDRESS"
    assert restored["text"] == "a@b.com"
    assert restored["bbox"][0] == [0.0, 0.0]


# ---------------------------------------------------------------------------
# FrameAnalyzer.analyze_frame
# ---------------------------------------------------------------------------


class _FakePiiResult:
    def __init__(self, entity_type, start, end, score):
        self.entity_type = entity_type
        self.start = start
        self.end = end
        self.score = score


@pytest.fixture
def analyzer():
    """FrameAnalyzer with stubbed Presidio (from conftest)."""
    return FrameAnalyzer()


@pytest.fixture
def stub_ocr(monkeypatch):
    """Monkeypatches the ocrmac.OCR class and PIL.Image.open so analyze_frame
    sees controllable OCR output and image dimensions. Returns a setter
    the test calls as `stub_ocr(annotations, image_size=(w, h))`.
    """
    state: dict = {"annotations": [], "size": (1000, 500)}

    class _FakeOCR:
        def __init__(self, *_args, **_kwargs):
            pass

        def recognize(self, px: bool = False):
            return state["annotations"]

    class _FakeImg:
        def __init__(self, size):
            self.size = size

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

    monkeypatch.setattr("screenredact.detector.ocrmac.OCR", _FakeOCR)
    monkeypatch.setattr("screenredact.detector.Image.open", lambda _p: _FakeImg(state["size"]))

    def _configure(annotations, image_size: tuple[int, int] | None = None) -> None:
        state["annotations"] = list(annotations)
        if image_size is not None:
            state["size"] = image_size

    return _configure


def _set_pii(analyzer: FrameAnalyzer, fn):
    analyzer.analyzer.analyze = fn  # type: ignore[method-assign]


def test_analyze_frame_empty_ocr_returns_no_detections(analyzer, stub_ocr):
    stub_ocr([])
    _set_pii(analyzer, lambda **kw: [])
    assert analyzer.analyze_frame(Path("ignored.png")) == []


def test_analyze_frame_skips_empty_text_strings(analyzer, stub_ocr):
    stub_ocr(
        [
            ("", 0.9, (0.0, 0.0, 0.0, 0.0)),
            ("", 0.9, (0.1, 0.1, 0.1, 0.1)),
        ]
    )
    calls: list = []
    _set_pii(analyzer, lambda **kw: calls.append(kw) or [])
    assert analyzer.analyze_frame(Path("ignored.png")) == []
    assert calls == []


def test_analyze_frame_no_pii_returns_empty(analyzer, stub_ocr):
    stub_ocr([("boring text", 0.99, (0.1, 0.1, 0.1, 0.1))])
    _set_pii(analyzer, lambda **kw: [])
    assert analyzer.analyze_frame(Path("ignored.png")) == []


def test_analyze_frame_single_pii_maps_to_detection(analyzer, stub_ocr):
    text = "Email me at foo@bar.com please"
    # Vision rect anchored at (x=0.1, y=0.9) with size (0.3, 0.05)
    # at the default 1000x500 image size → pixel bbox (100,25)-(400,50).
    stub_ocr([(text, 0.97, (0.1, 0.9, 0.3, 0.05))])
    _set_pii(
        analyzer,
        lambda text, **kw: [_FakePiiResult("EMAIL_ADDRESS", 12, 23, 1.0)],
    )

    detections = analyzer.analyze_frame(Path("ignored.png"))

    assert len(detections) == 1
    d = detections[0]
    assert d.type == "EMAIL_ADDRESS"
    assert d.text == "foo@bar.com"
    assert d.line_text == text
    expected_bbox = [[100.0, 25.0], [400.0, 25.0], [400.0, 50.0], [100.0, 50.0]]
    assert _flat_bbox(d.bbox) == pytest.approx(_flat_bbox(expected_bbox), abs=1e-6)
    assert d.ocr_confidence == pytest.approx(0.97)
    assert d.pii_score == pytest.approx(1.0)


def test_analyze_frame_multiple_pii_in_same_line(analyzer, stub_ocr):
    text = "foo@bar.com or call +1 555 1234"
    stub_ocr([(text, 0.9, (0.0, 0.0, 1.0, 1.0))])
    _set_pii(
        analyzer,
        lambda text, **kw: [
            _FakePiiResult("EMAIL_ADDRESS", 0, 11, 1.0),
            _FakePiiResult("PHONE_NUMBER", 20, 31, 0.85),
        ],
    )

    detections = analyzer.analyze_frame(Path("ignored.png"))

    assert [d.type for d in detections] == ["EMAIL_ADDRESS", "PHONE_NUMBER"]
    assert detections[0].text == "foo@bar.com"
    assert detections[1].text == "+1 555 1234"
    # Same OCR line → same bbox on both detections.
    assert detections[0].bbox == detections[1].bbox


def test_analyze_frame_multiple_ocr_lines(analyzer, stub_ocr):
    stub_ocr(
        [
            ("first@line.com", 0.9, (0.0, 0.9, 0.2, 0.05)),
            ("second", 0.9, (0.0, 0.1, 0.1, 0.05)),
        ]
    )

    def _analyze(text, **kw):
        if "@" in text:
            return [_FakePiiResult("EMAIL_ADDRESS", 0, len(text), 1.0)]
        return []

    _set_pii(analyzer, _analyze)

    detections = analyzer.analyze_frame(Path("ignored.png"))
    assert len(detections) == 1
    # First rect at y=0.9, h=0.05 on a 1000x500 image:
    #   x1=0, x2=200, y1=(1-0.95)*500=25, y2=(1-0.9)*500=50
    expected = [[0.0, 25.0], [200.0, 25.0], [200.0, 50.0], [0.0, 50.0]]
    assert _flat_bbox(detections[0].bbox) == pytest.approx(_flat_bbox(expected), abs=1e-6)


def test_analyze_frame_bbox_coerces_numeric_types(analyzer, stub_ocr):
    stub_ocr([("a", 1, (0.5, 0.5, 0.1, 0.1))])
    _set_pii(analyzer, lambda **kw: [_FakePiiResult("EMAIL_ADDRESS", 0, 1, 1.0)])
    det = analyzer.analyze_frame(Path("ignored.png"))[0]
    assert all(isinstance(c, float) for row in det.bbox for c in row)


def test_analyze_frame_passes_expected_entities_to_presidio(analyzer, stub_ocr):
    stub_ocr([("text", 0.9, (0.0, 0.0, 0.1, 0.1))])
    captured: dict = {}

    def _capture(**kw):
        captured.update(kw)
        return []

    _set_pii(analyzer, _capture)
    analyzer.analyze_frame(Path("ignored.png"))
    assert captured["entities"] == PII_ENTITIES
    assert captured["language"] == "en"


def test_analyze_frame_forwards_lang_to_ocrmac(analyzer, monkeypatch):
    # Catch the kwargs ocrmac.OCR was constructed with so we can assert the
    # BCP-47 language_preference plumbing still works end-to-end.
    captured_kwargs: dict = {}

    class _FakeOCR:
        def __init__(self, *_args, **kwargs):
            captured_kwargs.update(kwargs)

        def recognize(self, px: bool = False):
            return []

    class _FakeImg:
        size = (1000, 500)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

    monkeypatch.setattr("screenredact.detector.ocrmac.OCR", _FakeOCR)
    monkeypatch.setattr("screenredact.detector.Image.open", lambda _p: _FakeImg())

    FrameAnalyzer(lang="de-DE").analyze_frame(Path("ignored.png"))
    assert captured_kwargs.get("language_preference") == ["de-DE"]


def test_analyze_frame_empty_lang_sends_no_language_preference(monkeypatch):
    captured_kwargs: dict = {}

    class _FakeOCR:
        def __init__(self, *_args, **kwargs):
            captured_kwargs.update(kwargs)

        def recognize(self, px: bool = False):
            return []

    class _FakeImg:
        size = (1000, 500)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

    monkeypatch.setattr("screenredact.detector.ocrmac.OCR", _FakeOCR)
    monkeypatch.setattr("screenredact.detector.Image.open", lambda _p: _FakeImg())

    FrameAnalyzer(lang="").analyze_frame(Path("ignored.png"))
    assert captured_kwargs.get("language_preference") is None


def test_pii_entities_cover_all_required_categories():
    assert "EMAIL_ADDRESS" in PII_ENTITIES
    assert "PHONE_NUMBER" in PII_ENTITIES
    assert "CREDIT_CARD" in PII_ENTITIES
    assert "LOCATION" in PII_ENTITIES  # address proxy


# ---------------------------------------------------------------------------
# self.detections state
# ---------------------------------------------------------------------------


def test_analyze_frame_stores_detections_on_self(analyzer, stub_ocr):
    stub_ocr([("a@b.com", 0.9, (0.0, 0.0, 0.1, 0.1))])
    _set_pii(analyzer, lambda **kw: [_FakePiiResult("EMAIL_ADDRESS", 0, 7, 1.0)])

    returned = analyzer.analyze_frame(Path("f.png"))
    assert analyzer.detections is returned
    assert len(analyzer.detections) == 1


def test_analyze_frame_resets_detections_each_call(analyzer, stub_ocr):
    stub_ocr([("a@b.com", 0.9, (0.0, 0.0, 0.1, 0.1))])
    _set_pii(analyzer, lambda **kw: [_FakePiiResult("EMAIL_ADDRESS", 0, 7, 1.0)])
    analyzer.analyze_frame(Path("f1.png"))
    assert len(analyzer.detections) == 1

    # Clean frame:
    stub_ocr([("nothing", 0.9, (0.0, 0.0, 0.1, 0.1))])
    _set_pii(analyzer, lambda **kw: [])
    analyzer.analyze_frame(Path("f2.png"))
    assert analyzer.detections == []


# ---------------------------------------------------------------------------
# FrameAnalyzer.write_detections (reads from self.detections)
# ---------------------------------------------------------------------------


def _sample_detection() -> Detection:
    return Detection(
        type="EMAIL_ADDRESS",
        text="a@b.com",
        line_text="a@b.com",
        bbox=[[0.0, 0.0], [1.0, 1.0]],
        ocr_confidence=0.9,
        pii_score=1.0,
    )


def test_write_detections_empty_returns_none(analyzer, tmp_path):
    analyzer.detections = []
    assert analyzer.write_detections(tmp_path / "frame_1.png", tmp_path) is None


def test_write_detections_empty_writes_no_file(analyzer, tmp_path):
    analyzer.detections = []
    analyzer.write_detections(tmp_path / "frame_1.png", tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_write_detections_non_empty_returns_path(analyzer, tmp_path):
    analyzer.detections = [_sample_detection()]
    out = analyzer.write_detections(tmp_path / "frame_1.png", tmp_path)
    assert out is not None
    assert out.exists()


def test_write_detections_filename_matches_frame_stem(analyzer, tmp_path):
    analyzer.detections = [_sample_detection()]
    out = analyzer.write_detections(tmp_path / "frame_000042.png", tmp_path)
    assert out == tmp_path / "frame_000042.json"


def test_write_detections_uses_output_dir_not_image_parent(analyzer, tmp_path):
    frames = tmp_path / "frames"
    frames.mkdir()
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    analyzer.detections = [_sample_detection()]
    out = analyzer.write_detections(frames / "frame_000001.png", out_dir)

    assert out is not None
    assert out.parent == out_dir
    assert not (frames / "frame_000001.json").exists()


def test_write_detections_json_shape(analyzer, tmp_path):
    analyzer.detections = [_sample_detection()]
    out = analyzer.write_detections(tmp_path / "frame_7.png", tmp_path)
    payload = json.loads(out.read_text())
    assert payload["frame"] == "frame_7.png"
    assert isinstance(payload["detections"], list)
    assert len(payload["detections"]) == 1
    d = payload["detections"][0]
    assert d["type"] == "EMAIL_ADDRESS"
    assert d["bbox"] == [[0.0, 0.0], [1.0, 1.0]]
    assert d["ocr_confidence"] == 0.9
    assert d["pii_score"] == 1.0


def test_write_detections_multiple_preserved(analyzer, tmp_path):
    analyzer.detections = [
        _sample_detection(),
        Detection(
            type="PHONE_NUMBER",
            text="+1 555 0100",
            line_text="call +1 555 0100",
            bbox=[[5.0, 5.0]],
            ocr_confidence=0.8,
            pii_score=0.9,
        ),
    ]
    out = analyzer.write_detections(tmp_path / "frame.png", tmp_path)
    payload = json.loads(out.read_text())
    assert [d["type"] for d in payload["detections"]] == ["EMAIL_ADDRESS", "PHONE_NUMBER"]
