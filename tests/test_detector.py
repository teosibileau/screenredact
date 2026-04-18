"""Unit tests for screenredact.detector — OCR output normalisation,
PII mapping, and JSON sidecar writing."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from screenredact.detector import PII_ENTITIES, Detection, FrameAnalyzer


@pytest.fixture(autouse=True)
def _reset_frameanalyzer_caches():
    # FrameAnalyzer caches PaddleOCR + Presidio at the class level so models
    # load once per process. Tests monkey-patch those cached instances and
    # would leak into each other without a reset between tests.
    FrameAnalyzer._ocr_cache.clear()
    FrameAnalyzer._analyzer = None
    yield
    FrameAnalyzer._ocr_cache.clear()
    FrameAnalyzer._analyzer = None


# ---------------------------------------------------------------------------
# FrameAnalyzer._extract_ocr_fields (staticmethod)
# ---------------------------------------------------------------------------


class _HasAttrs:
    def __init__(self, **attrs):
        for k, v in attrs.items():
            setattr(self, k, v)


def test_extract_fields_from_object_attributes():
    obj = _HasAttrs(
        rec_texts=["hello"],
        rec_scores=[0.9],
        rec_polys=[[[0, 0], [1, 0], [1, 1], [0, 1]]],
    )
    texts, scores, polys = FrameAnalyzer._extract_ocr_fields(obj)
    assert texts == ["hello"]
    assert scores == [0.9]
    assert polys == [[[0, 0], [1, 0], [1, 1], [0, 1]]]


def test_extract_fields_from_dict():
    d = {
        "rec_texts": ["a", "b"],
        "rec_scores": [0.8, 0.7],
        "rec_polys": [[[0, 0]], [[1, 1]]],
    }
    texts, scores, polys = FrameAnalyzer._extract_ocr_fields(d)
    assert texts == ["a", "b"]
    assert scores == [0.8, 0.7]
    assert polys == [[[0, 0]], [[1, 1]]]


def test_extract_fields_missing_attrs_return_empty():
    assert FrameAnalyzer._extract_ocr_fields(_HasAttrs()) == ([], [], [])


def test_extract_fields_missing_dict_keys_return_empty():
    assert FrameAnalyzer._extract_ocr_fields({}) == ([], [], [])


def test_extract_fields_none_values_treated_as_empty():
    obj = _HasAttrs(rec_texts=None, rec_scores=None, rec_polys=None)
    assert FrameAnalyzer._extract_ocr_fields(obj) == ([], [], [])


def test_extract_fields_mixed_shape_object_and_dict_keys():
    obj = _HasAttrs(rec_texts=["only"])
    texts, scores, polys = FrameAnalyzer._extract_ocr_fields(obj)
    assert texts == ["only"]
    assert scores == []
    assert polys == []


def test_extract_fields_unexpected_type_does_not_crash():
    assert FrameAnalyzer._extract_ocr_fields(42) == ([], [], [])
    assert FrameAnalyzer._extract_ocr_fields("string") == ([], [], [])
    assert FrameAnalyzer._extract_ocr_fields(None) == ([], [], [])


def test_extract_ocr_fields_callable_without_instance():
    # Staticmethod — callable off the class directly:
    assert FrameAnalyzer._extract_ocr_fields({}) == ([], [], [])


# ---------------------------------------------------------------------------
# Class-level caching of PaddleOCR + Presidio
# ---------------------------------------------------------------------------


def test_multiple_analyzers_share_ocr_per_lang():
    a = FrameAnalyzer(lang="en")
    b = FrameAnalyzer(lang="en")
    assert a.ocr is b.ocr


def test_different_langs_get_different_ocr_instances():
    en = FrameAnalyzer(lang="en")
    es = FrameAnalyzer(lang="es")
    assert en.ocr is not es.ocr


def test_multiple_analyzers_share_presidio_engine():
    a = FrameAnalyzer(lang="en")
    b = FrameAnalyzer(lang="es")
    assert a.analyzer is b.analyzer


def test_analyzer_initializes_detections_as_empty_list():
    a = FrameAnalyzer()
    assert a.detections == []


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


class _FakeOcrResult:
    def __init__(self, rec_texts, rec_scores, rec_polys):
        self.rec_texts = rec_texts
        self.rec_scores = rec_scores
        self.rec_polys = rec_polys


class _FakePiiResult:
    def __init__(self, entity_type, start, end, score):
        self.entity_type = entity_type
        self.start = start
        self.end = end
        self.score = score


@pytest.fixture
def analyzer():
    """FrameAnalyzer with stubbed OCR/Presidio (from conftest)."""
    return FrameAnalyzer()


def _set_ocr(analyzer: FrameAnalyzer, results):
    analyzer.ocr.predict = lambda *a, **k: results  # type: ignore[method-assign]


def _set_pii(analyzer: FrameAnalyzer, fn):
    analyzer.analyzer.analyze = fn  # type: ignore[method-assign]


def test_analyze_frame_empty_ocr_returns_no_detections(analyzer):
    _set_ocr(analyzer, [])
    _set_pii(analyzer, lambda **kw: [])
    assert analyzer.analyze_frame(Path("ignored.png")) == []


def test_analyze_frame_none_ocr_returns_no_detections(analyzer):
    _set_ocr(analyzer, None)
    assert analyzer.analyze_frame(Path("ignored.png")) == []


def test_analyze_frame_skips_empty_text_strings(analyzer):
    _set_ocr(
        analyzer,
        [_FakeOcrResult(rec_texts=["", "", ""], rec_scores=[0.9, 0.9, 0.9], rec_polys=[[[0, 0]]] * 3)],
    )
    calls = []
    _set_pii(analyzer, lambda **kw: calls.append(kw) or [])
    assert analyzer.analyze_frame(Path("ignored.png")) == []
    assert calls == []


def test_analyze_frame_no_pii_returns_empty(analyzer):
    _set_ocr(
        analyzer,
        [_FakeOcrResult(rec_texts=["boring text"], rec_scores=[0.99], rec_polys=[[[0, 0], [1, 1]]])],
    )
    _set_pii(analyzer, lambda **kw: [])
    assert analyzer.analyze_frame(Path("ignored.png")) == []


def test_analyze_frame_single_pii_maps_to_detection(analyzer):
    text = "Email me at foo@bar.com please"
    _set_ocr(
        analyzer,
        [_FakeOcrResult(rec_texts=[text], rec_scores=[0.97], rec_polys=[[[10, 20], [110, 20], [110, 40], [10, 40]]])],
    )
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
    assert d.bbox == [[10.0, 20.0], [110.0, 20.0], [110.0, 40.0], [10.0, 40.0]]
    assert d.ocr_confidence == pytest.approx(0.97)
    assert d.pii_score == pytest.approx(1.0)


def test_analyze_frame_multiple_pii_in_same_line(analyzer):
    text = "foo@bar.com or call +1 555 1234"
    _set_ocr(
        analyzer,
        [_FakeOcrResult(rec_texts=[text], rec_scores=[0.9], rec_polys=[[[0, 0]]])],
    )
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
    assert detections[0].bbox == detections[1].bbox


def test_analyze_frame_multiple_ocr_lines(analyzer):
    _set_ocr(
        analyzer,
        [
            _FakeOcrResult(
                rec_texts=["first@line.com", "second"],
                rec_scores=[0.9, 0.9],
                rec_polys=[[[0, 0]], [[5, 5]]],
            )
        ],
    )

    def _analyze(text, **kw):
        if "@" in text:
            return [_FakePiiResult("EMAIL_ADDRESS", 0, len(text), 1.0)]
        return []

    _set_pii(analyzer, _analyze)

    detections = analyzer.analyze_frame(Path("ignored.png"))
    assert len(detections) == 1
    assert detections[0].bbox == [[0.0, 0.0]]  # from first polygon


def test_analyze_frame_bbox_coerces_numeric_types(analyzer):
    _set_ocr(
        analyzer,
        [_FakeOcrResult(rec_texts=["a"], rec_scores=[1], rec_polys=[[[1, 2], [3, 4]]])],
    )
    _set_pii(analyzer, lambda **kw: [_FakePiiResult("EMAIL_ADDRESS", 0, 1, 1.0)])
    det = analyzer.analyze_frame(Path("ignored.png"))[0]
    assert det.bbox == [[1.0, 2.0], [3.0, 4.0]]
    assert all(isinstance(c, float) for row in det.bbox for c in row)


def test_analyze_frame_passes_expected_entities_to_presidio(analyzer):
    _set_ocr(
        analyzer,
        [_FakeOcrResult(rec_texts=["text"], rec_scores=[0.9], rec_polys=[[[0, 0]]])],
    )
    captured: dict = {}

    def _capture(**kw):
        captured.update(kw)
        return []

    _set_pii(analyzer, _capture)
    analyzer.analyze_frame(Path("ignored.png"))
    assert captured["entities"] == PII_ENTITIES
    assert captured["language"] == "en"


def test_pii_entities_cover_all_required_categories():
    assert "EMAIL_ADDRESS" in PII_ENTITIES
    assert "PHONE_NUMBER" in PII_ENTITIES
    assert "CREDIT_CARD" in PII_ENTITIES
    assert "LOCATION" in PII_ENTITIES  # address proxy


# ---------------------------------------------------------------------------
# self.detections state
# ---------------------------------------------------------------------------


def test_analyze_frame_stores_detections_on_self(analyzer):
    _set_ocr(
        analyzer,
        [_FakeOcrResult(rec_texts=["a@b.com"], rec_scores=[0.9], rec_polys=[[[0, 0]]])],
    )
    _set_pii(analyzer, lambda **kw: [_FakePiiResult("EMAIL_ADDRESS", 0, 7, 1.0)])

    returned = analyzer.analyze_frame(Path("f.png"))
    assert analyzer.detections is returned
    assert len(analyzer.detections) == 1


def test_analyze_frame_resets_detections_each_call(analyzer):
    _set_ocr(
        analyzer,
        [_FakeOcrResult(rec_texts=["a@b.com"], rec_scores=[0.9], rec_polys=[[[0, 0]]])],
    )
    _set_pii(analyzer, lambda **kw: [_FakePiiResult("EMAIL_ADDRESS", 0, 7, 1.0)])

    analyzer.analyze_frame(Path("f1.png"))
    assert len(analyzer.detections) == 1

    # Now a clean frame:
    _set_ocr(
        analyzer,
        [_FakeOcrResult(rec_texts=["nothing"], rec_scores=[0.9], rec_polys=[[[0, 0]]])],
    )
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
